#!/usr/bin/env python3
"""
Peyda Xray Proxy Auto-Switcher & Health Monitor
- Continuously tests all candidate configs from xray/config.json
- Automatically connects to the first config with latency < 600ms
- If the active config disconnects or degrades (ping >= 600ms), cycles through subsequent configs
- Preserves all configs in config.json with the active one placed first
- Supports vless://, vmess://, trojan:// link parsing and batch import
- Provides a clean status table report and a systemd daemon service
"""

import os
import sys
import json
import time
import socket
import logging
import argparse
import base64
import urllib.parse
import subprocess
from pathlib import Path

# Paths & Defaults
BASE_DIR = Path("/root/moshtari-yab/xray")
CONFIG_FILE = BASE_DIR / "config.json"
XRAY_BIN = Path("/usr/local/bin/xray")
COMPOSE_DIR = Path("/root/moshtari-yab")
PING_THRESHOLD_MS = 600.0
CHECK_INTERVAL_SEC = 20
PROBE_URL = "https://api.telegram.org"
DOCKER_SERVICE_NAME = "xray"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ProxySwitcher")


def get_free_port() -> int:
    """Finds an ephemeral free TCP port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def parse_vless_url(url: str) -> dict:
    """Parses a vless:// URL into an Xray outbound dict."""
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme != "vless":
        raise ValueError("Not a vless link")

    uuid = parsed.username
    host = parsed.hostname
    port = parsed.port or 443
    params = urllib.parse.parse_qs(parsed.query)

    tag = urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"vless_{host}_{port}"
    security = params.get("security", ["none"])[0]
    network = params.get("type", ["tcp"])[0]
    sni = params.get("sni", [host])[0]
    fp = params.get("fp", ["chrome"])[0]
    path = params.get("path", ["/"])[0]
    ws_host = params.get("host", [host])[0]

    stream_settings = {
        "network": network,
        "security": security
    }

    if security == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": sni,
            "fingerprint": fp,
            "allowInsecure": False
        }
    elif security == "reality":
        stream_settings["realitySettings"] = {
            "serverName": sni,
            "fingerprint": fp,
            "show": False,
            "publicKey": params.get("pbk", [""])[0],
            "shortId": params.get("sid", [""])[0],
            "spiderX": params.get("spx", ["/"])[0]
        }

    if network == "ws":
        stream_settings["wsSettings"] = {
            "path": path,
            "headers": {"Host": ws_host}
        }
    elif network == "grpc":
        stream_settings["grpcSettings"] = {
            "serviceName": params.get("serviceName", [""])[0]
        }

    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": host,
                    "port": int(port),
                    "users": [{"id": uuid, "encryption": "none"}]
                }
            ]
        },
        "streamSettings": stream_settings
    }


def parse_vmess_url(url: str) -> dict:
    """Parses a vmess://base64 URL into an Xray outbound dict."""
    raw = url.strip()[8:]
    raw += "=" * ((4 - len(raw) % 4) % 4)
    data = json.loads(base64.b64decode(raw).decode("utf-8"))

    tag = data.get("ps", f"vmess_{data.get('add')}_{data.get('port')}")
    net = data.get("net", "tcp")
    sec = data.get("tls", "none")

    stream_settings = {
        "network": net,
        "security": sec if sec in ["tls", "reality"] else "none"
    }
    if sec == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": data.get("sni") or data.get("host") or data.get("add"),
            "allowInsecure": False
        }
    if net == "ws":
        stream_settings["wsSettings"] = {
            "path": data.get("path", "/"),
            "headers": {"Host": data.get("host", data.get("add"))}
        }

    return {
        "tag": tag,
        "protocol": "vmess",
        "settings": {
            "vnext": [
                {
                    "address": data.get("add"),
                    "port": int(data.get("port", 443)),
                    "users": [
                        {
                            "id": data.get("id"),
                            "alterId": int(data.get("aid", 0)),
                            "security": "auto"
                        }
                    ]
                }
            ]
        },
        "streamSettings": stream_settings
    }


def parse_trojan_url(url: str) -> dict:
    """Parses a trojan://password@host:port URL into an Xray outbound dict."""
    parsed = urllib.parse.urlparse(url.strip())
    password = parsed.username
    host = parsed.hostname
    port = parsed.port or 443
    params = urllib.parse.parse_qs(parsed.query)

    tag = urllib.parse.unquote(parsed.fragment) if parsed.fragment else f"trojan_{host}_{port}"
    sec = params.get("security", ["tls"])[0]
    net = params.get("type", ["tcp"])[0]
    sni = params.get("sni", [host])[0]

    stream_settings = {
        "network": net,
        "security": sec
    }
    if sec == "tls":
        stream_settings["tlsSettings"] = {
            "serverName": sni,
            "allowInsecure": False
        }

    return {
        "tag": tag,
        "protocol": "trojan",
        "settings": {
            "servers": [
                {
                    "address": host,
                    "port": int(port),
                    "password": password
                }
            ]
        },
        "streamSettings": stream_settings
    }


def parse_link(link: str) -> dict:
    """Auto-detects and parses vless, vmess, or trojan link."""
    link = link.strip()
    if link.startswith("vless://"):
        return parse_vless_url(link)
    elif link.startswith("vmess://"):
        return parse_vmess_url(link)
    elif link.startswith("trojan://"):
        return parse_trojan_url(link)
    raise ValueError(f"Unsupported protocol in link: {link[:10]}...")


def load_config_outbounds() -> list:
    """
    Loads all non-freedom proxy outbounds from config.json.
    Returns list of outbounds in order.
    """
    if not CONFIG_FILE.exists():
        logger.error(f"Config file not found: {CONFIG_FILE}")
        return []

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        outbounds = cfg.get("outbounds", [])
        proxies = [ob for ob in outbounds if ob.get("protocol") != "freedom"]
        return proxies
    except Exception as e:
        logger.error(f"Failed to read {CONFIG_FILE}: {e}")
        return []


def test_outbound(ob: dict, timeout_sec: float = 3.5) -> tuple:
    """
    Runs a standalone temporary Xray instance on an ephemeral port to test this outbound.
    Returns (is_connected: bool, ping_ms: float, error_msg: str).
    """
    if not XRAY_BIN.exists():
        return False, 9999.0, f"Xray binary not found at {XRAY_BIN}"

    test_port = get_free_port()
    test_cfg_path = Path(f"/tmp/xray_test_{test_port}.json")

    test_cfg = {
        "log": {"loglevel": "error"},
        "inbounds": [
            {
                "tag": "test_socks",
                "listen": "127.0.0.1",
                "port": test_port,
                "protocol": "socks"
            }
        ],
        "outbounds": [
            ob,
            {"tag": "direct", "protocol": "freedom"}
        ]
    }

    try:
        with open(test_cfg_path, "w", encoding="utf-8") as f:
            json.dump(test_cfg, f)
    except Exception as e:
        return False, 9999.0, f"Failed to write test config: {e}"

    proc = None
    try:
        proc = subprocess.Popen(
            [str(XRAY_BIN), "-c", str(test_cfg_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        ready = False
        t_start = time.time()
        while time.time() - t_start < 1.5:
            try:
                with socket.create_connection(("127.0.0.1", test_port), timeout=0.1):
                    ready = True
                    break
            except (ConnectionRefusedError, socket.timeout):
                time.sleep(0.05)

        if not ready:
            return False, 9999.0, "Xray failed to bind test port"

        t0 = time.time()
        res = subprocess.run(
            [
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}:%{time_total}",
                "--connect-timeout", str(int(timeout_sec)),
                "--max-time", str(int(timeout_sec) + 2),
                "-x", f"socks5h://127.0.0.1:{test_port}",
                PROBE_URL
            ],
            capture_output=True,
            text=True
        )
        elapsed_total_ms = (time.time() - t0) * 1000.0

        raw_out = res.stdout.strip()
        parts = raw_out.split(":")
        http_code = parts[0] if parts else "000"
        time_total = float(parts[1]) * 1000.0 if len(parts) > 1 and parts[1] else elapsed_total_ms

        if http_code in ["200", "301", "302", "307", "400", "403", "404"]:
            return True, time_total, ""
        else:
            return False, 9999.0, f"HTTP Code: {http_code}"
    except Exception as e:
        return False, 9999.0, str(e)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=0.8)
            except Exception:
                proc.kill()
        if test_cfg_path.exists():
            try:
                test_cfg_path.unlink()
            except Exception:
                pass


def test_active_docker_proxy() -> tuple:
    """
    Tests the currently active proxy inside docker or on 127.0.0.1:1080.
    Returns (is_connected: bool, ping_ms: float).
    """
    try:
        t0 = time.time()
        res = subprocess.run(
            [
                "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}:%{time_total}",
                "--connect-timeout", "3",
                "--max-time", "5",
                "-x", "socks5h://127.0.0.1:1080",
                PROBE_URL
            ],
            capture_output=True,
            text=True
        )
        raw = res.stdout.strip()
        parts = raw.split(":")
        code = parts[0] if parts else "000"
        dt_ms = float(parts[1]) * 1000.0 if len(parts) > 1 and parts[1] else (time.time() - t0) * 1000.0
        if code in ["200", "301", "302", "307", "400", "403", "404"]:
            return True, dt_ms
    except Exception:
        pass

    try:
        t0 = time.time()
        cmd = [
            "docker", "compose", "exec", "-T", "web",
            "python", "-c",
            "import urllib.request as u; print(u.build_opener(u.ProxyHandler({'https':'http://xray:8080'})).open('" + PROBE_URL + "', timeout=4).status)"
        ]
        res = subprocess.run(cmd, cwd=str(COMPOSE_DIR), capture_output=True, text=True, timeout=6)
        dt_ms = (time.time() - t0) * 1000.0
        if res.returncode == 0 and res.stdout.strip() in ["200", "301", "302", "307", "400", "403", "404"]:
            return True, dt_ms
    except Exception:
        pass

    return False, 9999.0


def set_active_config(chosen_tag: str) -> bool:
    """
    Reorders outbounds in config.json so chosen_tag is at index 0.
    Keeps all other configs preserved after it, with direct/freedom at the end.
    Restarts the xray container.
    """
    if not CONFIG_FILE.exists():
        logger.error(f"{CONFIG_FILE} does not exist.")
        return False

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        existing_outbounds = cfg.get("outbounds", [])
        proxies = [ob for ob in existing_outbounds if ob.get("protocol") != "freedom"]
        direct = next((ob for ob in existing_outbounds if ob.get("protocol") == "freedom"), {"tag": "direct", "protocol": "freedom"})

        target_ob = next((ob for ob in proxies if ob.get("tag") == chosen_tag), None)
        if not target_ob:
            logger.error(f"Config tag [{chosen_tag}] not found in outbounds.")
            return False

        if proxies and proxies[0].get("tag") == chosen_tag:
            logger.info(f"Config [{chosen_tag}] is already primary in {CONFIG_FILE}.")
            return True

        others = [ob for ob in proxies if ob.get("tag") != chosen_tag]
        cfg["outbounds"] = [target_ob] + others + [direct]

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

        logger.info(f"Updated {CONFIG_FILE}: primary outbound set to [{chosen_tag}].")

        cmd = ["docker", "compose", "--profile", "proxy", "restart", DOCKER_SERVICE_NAME]
        res = subprocess.run(cmd, cwd=str(COMPOSE_DIR), capture_output=True, text=True)
        if res.returncode == 0:
            logger.info(f"Restarted {DOCKER_SERVICE_NAME} container successfully.")
            return True
        else:
            logger.error(f"Failed to restart xray container: {res.stderr}")
            return False

    except Exception as e:
        logger.error(f"Error updating config: {e}")
        return False


def find_and_connect_best_config(max_ping: float = PING_THRESHOLD_MS, start_from_next: bool = False) -> dict:
    """
    Tests configs in order and connects to the FIRST config with ping < max_ping.
    If start_from_next is True, skips index 0 (current active) and checks index 1..N first.
    """
    proxies = load_config_outbounds()
    if not proxies:
        logger.warning("No candidate proxy configs found in config.json.")
        return {}

    if start_from_next and len(proxies) > 1:
        candidates = proxies[1:] + [proxies[0]]
    else:
        candidates = proxies

    logger.info(f"Checking {len(candidates)} candidate configs (Threshold: < {max_ping:.0f}ms)...")

    for idx, ob in enumerate(candidates, start=1):
        tag = ob.get("tag", f"config_{idx}")
        proto = ob.get("protocol", "unknown")
        server = "unknown"
        port = 443
        if "settings" in ob:
            vnext = ob["settings"].get("vnext", [])
            servers = ob["settings"].get("servers", [])
            if vnext:
                server = vnext[0].get("address", "unknown")
                port = vnext[0].get("port", 443)
            elif servers:
                server = servers[0].get("address", "unknown")
                port = servers[0].get("port", 443)

        logger.info(f"[{idx}/{len(candidates)}] Testing [{tag}] ({proto} -> {server}:{port})...")
        ok, ping_ms, err = test_outbound(ob)

        if ok and ping_ms < max_ping:
            logger.info(f"  >>> MATCH FOUND: [{tag}] CONNECTED with ping {ping_ms:.1f}ms (< {max_ping:.0f}ms)!")
            set_active_config(tag)
            return {"tag": tag, "ping": ping_ms, "outbound": ob}
        elif ok:
            logger.warning(f"  >>> HIGH PING: [{tag}] connected but ping is {ping_ms:.1f}ms (>= {max_ping:.0f}ms). Checking next...")
        else:
            logger.warning(f"  >>> FAILED: [{tag}] unreachable. {err}")

    logger.error(f"No candidate config met the < {max_ping:.0f}ms ping threshold!")
    return {}


def run_table_report():
    """Prints a detailed terminal report of all configs in config.json."""
    proxies = load_config_outbounds()
    if not proxies:
        print("[WARNING] No proxy configs found in config.json.")
        return

    print("\n" + "=" * 92)
    print(f"{chr(35):<3} | {'Active':<6} | {'Tag':<22} | {'Proto':<7} | {'Server:Port':<26} | {'Status':<11} | {'Ping':<9}")
    print("=" * 92)

    best_match = None
    for idx, ob in enumerate(proxies, start=1):
        is_active = (idx == 1)
        active_str = " [*]  " if is_active else "      "
        tag = ob.get("tag", f"config_{idx}")[:22]
        proto = ob.get("protocol", "vless")[:7]

        server = "unknown"
        port = 443
        if "settings" in ob:
            vnext = ob["settings"].get("vnext", [])
            servers = ob["settings"].get("servers", [])
            if vnext:
                server = vnext[0].get("address", "unknown")
                port = vnext[0].get("port", 443)
            elif servers:
                server = servers[0].get("address", "unknown")
                port = servers[0].get("port", 443)

        srv_str = f"{server}:{port}"[:26]

        ok, ping_ms, err = test_outbound(ob)
        if ok and ping_ms < PING_THRESHOLD_MS:
            status = "CONNECTED"
            ping_str = f"{ping_ms:.1f}ms"
            if not best_match:
                best_match = ob
        elif ok:
            status = "HIGH PING"
            ping_str = f"{ping_ms:.1f}ms"
        else:
            status = "FAILED"
            ping_str = "Timeout"

        print(f"{idx:<3} | {active_str} | {tag:<22} | {proto:<7} | {srv_str:<26} | {status:<11} | {ping_str:<9}")

    print("=" * 92)
    if best_match:
        print(f"\n[RECOMMENDED] First valid config (< 600ms): [{best_match.get('tag')}]")
    else:
        print("\n[ALERT] No config currently has ping < 600ms.")


def run_daemon(max_ping: float = PING_THRESHOLD_MS, interval_sec: int = CHECK_INTERVAL_SEC):
    """
    Continuous watchdog daemon:
    - Monitors active connection every interval_sec seconds
    - If healthy (ping < 600ms), stays on it
    - If fails or ping >= 600ms, cycles through subsequent configs until a healthy one is found
    """
    logger.info(f"Starting Xray watchdog daemon (Check interval: {interval_sec}s, Ping threshold: < {max_ping:.0f}ms)")

    while True:
        try:
            live_ok, live_ping = test_active_docker_proxy()

            if live_ok and live_ping < max_ping:
                logger.info(f"Active proxy is HEALTHY (Ping: {live_ping:.1f}ms < {max_ping:.0f}ms).")
            else:
                if not live_ok:
                    logger.warning("Active proxy connection is DOWN or disconnected!")
                else:
                    logger.warning(f"Active proxy ping degraded ({live_ping:.1f}ms >= {max_ping:.0f}ms)!")

                logger.info("Initiating automatic failover scan across subsequent configs...")
                res = find_and_connect_best_config(max_ping=max_ping, start_from_next=True)
                if res:
                    logger.info(f"Failover successful: connected to [{res['tag']}] ({res['ping']:.1f}ms).")
                else:
                    logger.error("Failover scan completed: no working config found yet. Will retry next cycle.")

        except Exception as e:
            logger.error(f"Error in watchdog daemon loop: {e}")

        time.sleep(interval_sec)


def add_link_to_config(link: str):
    """Adds a single vless/vmess/trojan link into config.json."""
    try:
        new_ob = parse_link(link)
    except Exception as e:
        logger.error(f"Failed to parse link: {e}")
        return False

    if not CONFIG_FILE.exists():
        logger.error(f"{CONFIG_FILE} not found.")
        return False

    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    outbounds = cfg.get("outbounds", [])
    outbounds = [ob for ob in outbounds if ob.get("tag") != new_ob.get("tag")]
    direct = [ob for ob in outbounds if ob.get("protocol") == "freedom"]
    proxies = [ob for ob in outbounds if ob.get("protocol") != "freedom"]

    cfg["outbounds"] = proxies + [new_ob] + direct
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    logger.info(f"Added config [{new_ob.get('tag')}] ({new_ob.get('protocol')}) to {CONFIG_FILE}.")
    return True


def import_links_from_file(filepath: Path):
    """Imports multiple links from a text file into config.json."""
    if not filepath.exists():
        logger.error(f"File not found: {filepath}")
        return

    with open(filepath, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]

    added = 0
    for line in lines:
        if add_link_to_config(line):
            added += 1

    logger.info(f"Imported {added} configs from {filepath}.")


def install_systemd_service():
    """Generates and enables the xray-failover systemd service."""
    service_content = f"""[Unit]
Description=Peyda Xray Proxy Auto-Switcher & Failover Watchdog
After=docker.service network-online.target
Wants=docker.service

[Service]
Type=simple
User=root
WorkingDirectory={COMPOSE_DIR}
ExecStart=/usr/bin/python3 {BASE_DIR}/proxy_switcher.py --daemon
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
    svc_path = Path("/etc/systemd/system/xray-failover.service")
    try:
        with open(svc_path, "w", encoding="utf-8") as f:
            f.write(service_content)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", "--now", "xray-failover.service"], check=True)
        logger.info(f"Successfully installed and started {svc_path.name}!")
        return True
    except Exception as e:
        logger.error(f"Failed to install systemd service: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Peyda Xray Proxy Auto-Switcher & Failover Watchdog")
    parser.add_argument("--check", action="store_true", help="Test all configs in config.json and display table report")
    parser.add_argument("--switch", action="store_true", help="Find first config with ping < 600ms and switch to it now")
    parser.add_argument("--daemon", action="store_true", help="Run continuous failover watchdog daemon")
    parser.add_argument("--threshold", type=float, default=PING_THRESHOLD_MS, help="Max ping threshold in ms (default: 600)")
    parser.add_argument("--interval", type=int, default=CHECK_INTERVAL_SEC, help="Watchdog check interval in seconds (default: 20)")
    parser.add_argument("--add-link", type=str, help="Add a vless://, vmess://, or trojan:// URL into config.json")
    parser.add_argument("--import-links", type=str, help="Import links from a text file into config.json")
    parser.add_argument("--install-service", action="store_true", help="Install and start systemd daemon service")

    args = parser.parse_args()

    if args.add_link:
        add_link_to_config(args.add_link)
        return

    if args.import_links:
        import_links_from_file(Path(args.import_links))
        return

    if args.install_service:
        install_systemd_service()
        return

    if args.check:
        run_table_report()
    elif args.daemon:
        run_daemon(max_ping=args.threshold, interval_sec=args.interval)
    elif args.switch:
        res = find_and_connect_best_config(max_ping=args.threshold)
        if res:
            print(f"\n[OK] Connected to [{res['tag']}] with ping {res['ping']:.1f}ms.")
        else:
            print(f"\n[WARNING] No config with ping < {args.threshold:.0f}ms found.")
    else:
        res = find_and_connect_best_config(max_ping=args.threshold)
        if res:
            print(f"\n[OK] Connected to [{res['tag']}] with ping {res['ping']:.1f}ms.")
        else:
            print(f"\n[WARNING] No config with ping < {args.threshold:.0f}ms found.")


if __name__ == "__main__":
    main()

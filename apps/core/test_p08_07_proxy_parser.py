import base64
import json
from django.test import TestCase

from xray.multi_proxy_manager import parse_link_to_outbound as mpm_parse, validate_config_format
from xray.proxy_parser import (
    parse_proxy_link,
    parse_ss_url,
    parse_throne_url,
    parse_trojan_url,
    parse_vless_url,
    parse_vmess_url,
)
from xray.proxy_switcher import parse_link as ps_parse


class TestProxyParser(TestCase):
    def test_parse_vless_url_tls_and_reality(self):
        vless_reality = (
            "vless://11111111-2222-3333-4444-555555555555@proxy.example.com:443"
            "?security=reality&type=grpc&serviceName=my-grpc&sni=speed.cloudflare.com"
            "&pbk=abcdef123456&sid=1234&spx=/path#RealityNode"
        )
        res = parse_vless_url(vless_reality)
        self.assertEqual(res["protocol"], "vless")
        self.assertEqual(res["tag"], "RealityNode")
        self.assertEqual(res["settings"]["vnext"][0]["address"], "proxy.example.com")
        self.assertEqual(res["settings"]["vnext"][0]["port"], 443)
        self.assertEqual(res["settings"]["vnext"][0]["users"][0]["id"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(res["streamSettings"]["security"], "reality")
        self.assertEqual(res["streamSettings"]["network"], "grpc")
        self.assertEqual(res["streamSettings"]["realitySettings"]["publicKey"], "abcdef123456")

        # Equivalence across multi_proxy_manager and proxy_switcher
        self.assertEqual(mpm_parse(vless_reality), res)
        self.assertEqual(ps_parse(vless_reality), res)

    def test_parse_vmess_url(self):
        vmess_data = {
            "v": "2",
            "ps": "VMessNode",
            "add": "vmess.example.com",
            "port": 8443,
            "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "aid": 0,
            "net": "ws",
            "type": "none",
            "host": "vmess.example.com",
            "path": "/ws",
            "tls": "tls",
            "sni": "vmess.example.com",
        }
        raw_b64 = base64.b64encode(json.dumps(vmess_data).encode("utf-8")).decode("utf-8")
        vmess_url = f"vmess://{raw_b64}"

        res = parse_vmess_url(vmess_url)
        self.assertEqual(res["protocol"], "vmess")
        self.assertEqual(res["tag"], "VMessNode")
        self.assertEqual(res["settings"]["vnext"][0]["address"], "vmess.example.com")
        self.assertEqual(res["settings"]["vnext"][0]["port"], 8443)
        self.assertEqual(res["streamSettings"]["network"], "ws")
        self.assertEqual(res["streamSettings"]["security"], "tls")

        self.assertEqual(mpm_parse(vmess_url), res)
        self.assertEqual(ps_parse(vmess_url), res)

    def test_parse_trojan_url(self):
        trojan_url = "trojan://password123@trojan.example.com:443?security=tls&sni=trojan.example.com#TrojanNode"
        res = parse_trojan_url(trojan_url)
        self.assertEqual(res["protocol"], "trojan")
        self.assertEqual(res["tag"], "TrojanNode")
        self.assertEqual(res["settings"]["servers"][0]["address"], "trojan.example.com")
        self.assertEqual(res["settings"]["servers"][0]["password"], "password123")
        self.assertEqual(res["streamSettings"]["security"], "tls")

        self.assertEqual(mpm_parse(trojan_url), res)
        self.assertEqual(ps_parse(trojan_url), res)

    def test_parse_shadowsocks_url(self):
        userinfo = base64.b64encode(b"chacha20-ietf-poly1305:secretpassword").decode("utf-8")
        ss_url = f"ss://{userinfo}@ss.example.com:8388#ShadowsocksNode"
        res = parse_ss_url(ss_url)
        self.assertEqual(res["protocol"], "shadowsocks")
        self.assertEqual(res["tag"], "ShadowsocksNode")
        self.assertEqual(res["settings"]["servers"][0]["address"], "ss.example.com")
        self.assertEqual(res["settings"]["servers"][0]["port"], 8388)
        self.assertEqual(res["settings"]["servers"][0]["method"], "chacha20-ietf-poly1305")
        self.assertEqual(res["settings"]["servers"][0]["password"], "secretpassword")

        self.assertEqual(mpm_parse(ss_url), res)
        self.assertEqual(ps_parse(ss_url), res)

    def test_parse_throne_url(self):
        throne_data = {
            "server": "throne.example.com",
            "server_port": 1080,
            "method": "aes-256-gcm",
            "password": "throne_password",
            "tag": "ThroneSS",
        }
        b64_data = base64.urlsafe_b64encode(json.dumps(throne_data).encode("utf-8")).decode("utf-8")
        throne_url = f"throne://add/{b64_data}"
        res = parse_throne_url(throne_url)
        self.assertEqual(res["protocol"], "shadowsocks")
        self.assertEqual(res["tag"], "ThroneSS")
        self.assertEqual(res["settings"]["servers"][0]["address"], "throne.example.com")
        self.assertEqual(res["settings"]["servers"][0]["method"], "aes-256-gcm")

        # parse_link in proxy_switcher supports throne
        self.assertEqual(ps_parse(throne_url), res)

    def test_validate_config_format_and_errors(self):
        # Empty string
        ok, scheme, err = validate_config_format("")
        self.assertFalse(ok)
        self.assertIn("خالی", err)

        # Unsupported protocol
        ok, scheme, err = validate_config_format("http://example.com/proxy")
        self.assertFalse(ok)
        self.assertIn("پشتیبانی نمی‌شود", err)

        # Invalid hostname
        ok, scheme, err = validate_config_format("vless://user@:443")
        self.assertFalse(ok)
        self.assertIn("نامعتبر", err)

        # Valid vless link
        valid_vless = "vless://uuid@host.com:443?security=tls"
        ok, scheme, err = validate_config_format(valid_vless)
        self.assertTrue(ok)
        self.assertEqual(scheme, "vless")
        self.assertEqual(err, "")

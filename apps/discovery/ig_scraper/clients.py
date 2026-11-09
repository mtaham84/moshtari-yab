"""Instagram clients for scraper accounts: one warm client per account, always behind the account's proxy."""
from apps.discovery.ig_scraper import session
from apps.discovery.ig_scraper.context import PROXY_DOWN_REST_HOURS
from apps.discovery.ig_session import redact


class ProxyUnavailable(RuntimeError):
    """The account's proxy is down; the account is rested instead of scraping from the server's own IP."""


def get_persistent_client(run, account):
    """
    Maintains a persistent, warm in-memory Client pool per account.
    Never rebuilds the client from scratch if one already exists,
    preventing repeated login/logout handshakes and connection churn.
    """
    if account.id in run.client_pool:
        return run.client_pool[account.id]

    cl = build_client(run, account)
    run.client_pool[account.id] = cl
    return cl


def build_client(run, account):
    from instagrapi import Client
    import re

    cl = Client()
    cl.delay_range = [3, 7]
    cl.request_timeout = 15

    # Apply realistic unique device emulation profile (Pixel 8 Pro, S24 Ultra, Xiaomi 14, etc.)
    try:
        from apps.discovery.device_emulator import apply_device_profile_to_client
        apply_device_profile_to_client(cl, account)
    except Exception:
        if account.device_settings:
            try:
                cl.set_device(account.device_settings, reset=False)
            except Exception:
                pass

    session.restore_from_mirror(run, account)
    if account.session_json:
        try:
            cl.set_settings(account.session_json)
            cl.username = account.username
            cookies = account.session_json.get("cookies", {})
            for k, v in cookies.items():
                try:
                    cl.public.cookies.set(k, v, domain=".instagram.com")
                    cl.private.cookies.set(k, v, domain=".instagram.com")
                except Exception:
                    pass
            sess_id = cookies.get("sessionid")
            if sess_id:
                m = re.search(r"^\d+", sess_id)
                if m:
                    uid = m.group()
                    cl.authorization_data = {
                        "ds_user_id": uid,
                        "sessionid": sess_id,
                        "should_use_header_over_cookies": True,
                    }
        except Exception as e:
            run.stderr.write(f"Warning restoring session settings for @{account.username}: {e}")

    from apps.discovery.proxies.health import proxy_reachable
    proxy = account.clean_proxy   # its own proxy / system config, else the shared pool (never the server IP)
    if not proxy:
        run.stdout.write(run.style.WARNING(
            f"    [Proxy Guard]: No proxy configured for @{account.username}; allocating replacement config..."
        ))
        if rotate_or_heal_proxy(run, account, cl=cl):
            proxy = account.clean_proxy
        if not proxy:
            raise ProxyUnavailable(f"no proxy configured for @{account.username} and no healthy fallback available")

    if proxy:
        proxy_ok, proxy_msg = proxy_reachable(proxy, timeout=2.5)
        if not proxy_ok:
            run.stderr.write(f"    proxy unreachable for @{account.username} ({proxy_msg}); triggering auto-failover...")
            if rotate_or_heal_proxy(run, account, cl=cl):
                proxy = account.clean_proxy
            else:
                # Never fall back to the server's own IP: the same session suddenly coming from a datacenter IP
                # (and the same IP for every account) is a far bigger risk than skipping one cycle.
                raise ProxyUnavailable(f"proxy unreachable for @{account.username} and no healthy fallback: {proxy_msg}")
        if proxy:
            cl.set_proxy(proxy)
    else:
        raise ProxyUnavailable(f"no proxy configured for @{account.username}")

    return cl


def rotate_or_heal_proxy(run, account, cl=None) -> bool:
    """
    Automatically heals/failovers account connection when proxy drops or dies.
    Finds the next free healthy config (or pool) and updates client proxy immediately.
    """
    from apps.discovery.ig_accounts import _heal_account
    from apps.discovery.proxies.health import proxy_reachable
    old_proxy = account.clean_proxy
    curr_cfg_id = account.proxy_config_id

    run.stdout.write(run.style.WARNING(
        f"    [Auto-Failover]: Proxy for @{account.username} failed. Searching for healthy replacement config..."
    ))
    res = _heal_account(account, actor="scraper_auto_heal", force_rotate=True, failed_config_id=curr_cfg_id)
    account.refresh_from_db()
    new_proxy = account.clean_proxy
    if new_proxy and (new_proxy != old_proxy or (res and res.get("changed"))):
        ok, msg = proxy_reachable(new_proxy, timeout=3.0)
        if ok:
            if cl:
                cl.set_proxy(new_proxy)
            # Invalidate client pool cache so fresh client/session is rebuilt with new proxy
            run.client_pool.pop(account.id, None)
            run.stdout.write(run.style.SUCCESS(
                f"    [Auto-Failover]: Account @{account.username} successfully rotated to new proxy: {new_proxy}"
            ))
            return True
        else:
            run.stderr.write(f"    [Auto-Failover]: Fallback proxy {new_proxy} unreachable: {msg}")
    else:
        run.stderr.write(f"    [Auto-Failover]: No alternative healthy proxy available for @{account.username}")
    return False


def client_or_rest(run, account):
    """Client for `account`, or None after resting it for a while when its proxy is down."""
    try:
        return get_persistent_client(run, account)
    except ProxyUnavailable as exc:
        # One final failover attempt before resting
        if rotate_or_heal_proxy(run, account):
            try:
                return get_persistent_client(run, account)
            except Exception:
                pass
        session.set_state(account, "COOLDOWN", f"پروکسی در دسترس نیست؛ بدون پروکسی وصل نمی‌شویم: {redact(exc, 150)}",
                        hours=PROXY_DOWN_REST_HOURS)
        run.stderr.write(f"    proxy_unavailable: account {account.id} rests {PROXY_DOWN_REST_HOURS}h (no direct fallback)")
        return None

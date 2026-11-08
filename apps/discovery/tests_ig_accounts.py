"""apps.discovery.ig_accounts: login with the code typed in the panel (one login, the code goes into it), sessionid,
sticky system configs (heal / split / release), config export, pause/resume, per-customer pitch."""
import importlib
import threading
from types import SimpleNamespace
from unittest import mock

from django.apps import apps as django_apps
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from instagrapi.exceptions import BadPassword, ChallengeRequired, ClientConnectionError, LoginRequired, \
    TwoFactorRequired, UnknownError

from apps.discovery import ig_accounts as ig
from apps.discovery.models import InstagramAccountEvent, InstagramEngineAccount, OutreachTenantProfile, ProxyConfig

CODE = "123456"


class FakeIG:
    """Stands in for instagrapi.Client; `script` says how Instagram answers the login."""
    instances: list = []
    script: dict = {}

    def __init__(self, *args, **kwargs):
        self.proxy = None
        self.challenge_code_handler = None
        self.change_password_handler = None
        self.last_json = {}
        self.logins, self.codes, self.private_calls, self.sessionids = [], [], [], []
        self.settings = {}
        self.phone_id, self.token, self.uuid, self.android_device_id = "phone-1", "csrf", "uuid-1", "android-1"
        self.authorization_data = {}
        self.timeline_calls = 0
        FakeIG.instances.append(self)

    def set_proxy(self, url):
        self.proxy = url

    def set_uuids(self, uuids):
        self.uuids = dict(uuids)

    def set_device(self, device, reset=False):
        self.device_settings = dict(device or {})

    def login(self, username, password, verification_code=""):
        self.logins.append((username, password, verification_code))
        mode = FakeIG.script.get("mode", "ok")
        if mode == "challenge":   # instagrapi asks challenge_code_handler and submits the code itself
            self.last_json = {"step_name": "verify_email", "step_data": {"contact_point": "j***@gmail.com"}}
            code = self.challenge_code_handler(username, "EMAIL")
            self.codes.append(code)
            if code != FakeIG.script.get("code", CODE):
                raise ChallengeRequired("challenge_required")
            return True
        if mode == "two_factor":
            self.last_json = {"two_factor_info": {"two_factor_identifier": "tf-id", "obfuscated_phone_number": "**12",
                                                  "sms_two_factor_on": True, "totp_two_factor_on": False}}
            raise TwoFactorRequired("Two-factor authentication required")
        if mode == "bad_password":
            raise BadPassword("The password you entered is incorrect.")
        if mode == "network":
            raise ClientConnectionError("timed out")
        return True

    def private_request(self, endpoint, data=None, login=False, **kwargs):
        self.private_calls.append((endpoint, dict(data or {})))
        if endpoint == "accounts/two_factor_login/":
            if data["verification_code"] != FakeIG.script.get("code", CODE):
                raise UnknownError("Please check the security code and try again.")
            self.last_response = SimpleNamespace(headers={"ig-set-authorization": "Bearer IGT:2:xyz"})
            return True
        return {}

    def parse_authorization(self, value):
        return {"sessionid": "sid-2fa"}

    def login_flow(self):
        return True

    def login_by_sessionid(self, sessionid):
        self.sessionids.append(sessionid)
        exc = FakeIG.script.get("sessionid_error")
        if exc:
            raise exc
        return True

    def set_settings(self, settings):
        self.settings = settings

    def get_settings(self):
        return {"cookies": {"sessionid": "sid-new"}, "uuids": {"uuid": "uuid-1", "phone_id": "phone-1"},
                "device_settings": {"model": "Pixel 7", "manufacturer": "Google"}}

    def get_timeline_feed(self):
        self.timeline_calls += 1
        exc = FakeIG.script.get("feed_error")
        if exc:
            raise exc
        return {}


class Operator:
    """The person at the panel (runs where the login thread sleeps): types the next code once one is asked for."""

    def __init__(self, account, codes=(), cancel=False):
        self.account, self.codes, self.cancel = account, list(codes), cancel
        self.results, self.statuses, self.hints, self.errors = [], [], [], []

    def __call__(self, seconds):
        p = ig._read_pending(self.account.pk)
        if p.get("state") != "needs_code" or p.get("code"):
            return
        acc = InstagramEngineAccount.objects.get(pk=self.account.pk)
        self.statuses.append((acc.status, acc.status_reason))
        self.hints.append(p.get("hint"))
        self.errors.append(p.get("error"))
        if self.cancel:
            self.results.append(ig.cancel_login(acc))
        elif self.codes:
            self.results.append(ig.submit_code(acc, self.codes.pop(0)))


@override_settings(INSTAGRAM_LOGIN_IN_BACKGROUND=False, INSTAGRAM_DEFAULT_PROXY_URL="socks5h://xray:1080",
                   INSTAGRAM_ACCOUNTS_PER_CONFIG=1)
class IgAccountsTestBase(TestCase):
    def setUp(self):
        FakeIG.instances, FakeIG.script = [], {}
        patches = [mock.patch("instagrapi.Client", FakeIG),
                   mock.patch.object(ig, "port_open", side_effect=lambda host, port, timeout=1.5: port not in self.closed)]
        self.closed = set()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def acc(self, username="acc1", **kw):
        kw.setdefault("password", "pw")
        kw.setdefault("role", "SENDER")
        kw.setdefault("tenant_id", "tenant_9")
        return InstagramEngineAccount.objects.create(username=username, **kw)

    def cfg(self, port, status="ACTIVE", **kw):
        return ProxyConfig.objects.create(raw_url=f"vless://u@h{port}.example:443?type=tcp#c{port}", protocol="vless",
                                          server=f"h{port}.example", port=443, local_port=port, status=status, **kw)

    def kinds(self, acc):
        return list(InstagramAccountEvent.objects.filter(account=acc).order_by("created_at", "id")
                    .values_list("kind", flat=True))

    def sleep_as(self, operator):
        p = mock.patch.object(ig, "_sleep", operator)
        p.start()
        self.addCleanup(p.stop)
        return operator


class LoginWithCodeTests(IgAccountsTestBase):
    def test_plain_login_saves_the_session_and_activates(self):
        a = self.acc(status="CHALLENGED", status_reason="session_expired")
        res = ig.start_login(a)
        a.refresh_from_db()
        self.assertTrue(res.ok, res)
        self.assertEqual(res.state, "ok")
        self.assertEqual((a.status, a.status_reason), ("ACTIVE", ""))
        self.assertEqual(a.session_json["cookies"]["sessionid"], "sid-new")
        self.assertEqual(a.pending_login["state"], "ok")
        self.assertEqual(FakeIG.instances[0].proxy, "socks5h://xray:1080")   # never the server's own IP
        self.assertIn("login_ok", self.kinds(a))

    def test_challenge_code_goes_into_the_same_login(self):
        a = self.acc(status="CHALLENGED")
        FakeIG.script = {"mode": "challenge"}
        op = self.sleep_as(Operator(a, codes=["۱۲۳۴۵۶"]))   # Persian digits are fine
        res = ig.start_login(a)
        a.refresh_from_db()
        self.assertTrue(res.ok, res)
        self.assertEqual(len(FakeIG.instances), 1)
        self.assertEqual(len(FakeIG.instances[0].logins), 1)     # one login: no second request, no new code
        self.assertEqual(FakeIG.instances[0].codes, [CODE])
        self.assertEqual(op.statuses, [("CHALLENGED", "needs_code")])
        self.assertEqual(op.hints, ["ایمیل j***@gmail.com"])
        self.assertTrue(op.results[0].ok)
        self.assertEqual(op.results[0].state, "verifying")
        self.assertEqual((a.status, a.status_reason), ("ACTIVE", ""))
        self.assertIsNone(a.pending_login.get("code"))
        self.assertEqual(self.kinds(a)[-2:], ["code_requested", "code_ok"])

    def test_wrong_challenge_code_reports_and_asks_for_a_new_login(self):
        a = self.acc()
        FakeIG.script = {"mode": "challenge"}
        self.sleep_as(Operator(a, codes=["999999"]))
        res = ig.start_login(a)
        a.refresh_from_db()
        self.assertFalse(res.ok)
        self.assertEqual(res.state, "failed")
        self.assertEqual((a.status, a.status_reason), ("CHALLENGED", "needs_code"))
        self.assertIn("نپذیرفت", a.status_message)
        self.assertIn("code_failed", self.kinds(a))

    def test_no_code_in_time_ends_the_login(self):
        a = self.acc()
        FakeIG.script = {"mode": "challenge"}
        with mock.patch.object(ig, "LOGIN_WAIT_SECONDS", 0):
            res = ig.start_login(a)
        a.refresh_from_db()
        self.assertEqual(res.state, "failed")
        self.assertEqual(a.status_reason, "needs_code")
        self.assertIn("زمان", a.status_message)

    def test_cancel_while_waiting_for_the_code(self):
        a = self.acc(status="ACTIVE")
        FakeIG.script = {"mode": "challenge"}
        self.sleep_as(Operator(a, cancel=True))
        res = ig.start_login(a)
        a.refresh_from_db()
        self.assertEqual(res.state, "cancelled")
        self.assertEqual(a.pending_login["state"], "cancelled")
        self.assertEqual((a.status, a.status_reason), ("CHALLENGED", "session_expired"))
        self.assertIsNone(a.session_json)

    def test_two_factor_retries_the_code_in_the_same_login(self):
        a = self.acc()
        FakeIG.script = {"mode": "two_factor", "code": "654321"}
        op = self.sleep_as(Operator(a, codes=["000000", "654321"]))
        res = ig.start_login(a)
        a.refresh_from_db()
        self.assertTrue(res.ok, res)
        cl = FakeIG.instances[0]
        self.assertEqual(len(cl.logins), 1)
        sent = [d for ep, d in cl.private_calls if ep == "accounts/two_factor_login/"]
        self.assertEqual([d["verification_code"] for d in sent], ["000000", "654321"])
        self.assertEqual({(d["two_factor_identifier"], d["verification_method"], d["trust_this_device"]) for d in sent},
                         {("tf-id", "3", "0")})
        self.assertEqual(op.hints[0], "پیامک به **12")
        self.assertTrue(op.errors[1])   # the second prompt says the first code was rejected
        self.assertEqual(a.status, "ACTIVE")
        self.assertIn("code_ok", self.kinds(a))

    def test_bad_password_and_network_errors(self):
        a = self.acc("bp")
        FakeIG.script = {"mode": "bad_password"}
        ig.start_login(a)
        a.refresh_from_db()
        self.assertEqual((a.status, a.status_reason), ("CHALLENGED", "bad_password"))
        b = self.acc("net")
        FakeIG.script = {"mode": "network"}
        ig.start_login(b)
        b.refresh_from_db()
        self.assertEqual((b.status, b.status_reason), ("COOLDOWN", "proxy_down"))
        self.assertIsNotNone(b.cooldown_until)

    def test_code_without_a_waiting_login_is_refused(self):
        a = self.acc()
        res = ig.submit_code(a, CODE)
        self.assertFalse(res.ok)
        self.assertFalse(ig.submit_code(a, "12").ok)

    def test_no_password_no_login(self):
        a = self.acc(password="session_only")
        self.assertFalse(ig.start_login(a).ok)
        self.assertEqual(FakeIG.instances, [])

    def test_paused_account_stays_paused_after_a_login(self):
        a = self.acc(status="DISABLED", status_reason="paused")
        ig.start_login(a)
        a.refresh_from_db()
        self.assertEqual(a.status, "DISABLED")
        self.assertTrue(a.session_json)


class SessionAndCheckTests(IgAccountsTestBase):
    def test_sessionid_login(self):
        a = self.acc(status="CHALLENGED", status_message="x")
        res = ig.login_with_sessionid(a, "sessionid=ABC%3A1; csrftoken=zz")
        a.refresh_from_db()
        self.assertTrue(res.ok)
        self.assertEqual(FakeIG.instances[0].sessionids, ["ABC%3A1"])
        self.assertEqual((a.status, a.status_message), ("ACTIVE", ""))
        self.assertIn("session_set", self.kinds(a))

    def test_rejected_sessionid_and_network_error(self):
        a = self.acc(status="ACTIVE")
        FakeIG.script = {"sessionid_error": LoginRequired("login_required")}
        self.assertFalse(ig.login_with_sessionid(a, "S1").ok)
        a.refresh_from_db()
        self.assertEqual((a.status, a.status_reason), ("CHALLENGED", "session_expired"))
        b = self.acc("b", status="ACTIVE")
        FakeIG.script = {"sessionid_error": ClientConnectionError("timed out")}
        self.assertFalse(ig.login_with_sessionid(b, "S2").ok)
        b.refresh_from_db()
        self.assertEqual(b.status, "ACTIVE")

    def test_check_uses_the_saved_session_without_logging_in(self):
        a = self.acc(status="CHALLENGED", session_json={"cookies": {"sessionid": "S"}})
        res = ig.check(a)
        a.refresh_from_db()
        self.assertTrue(res.ok, res)
        cl = FakeIG.instances[0]
        self.assertEqual(cl.logins, [])
        self.assertEqual(cl.timeline_calls, 1)
        self.assertEqual(a.status, "ACTIVE")
        self.assertIn("check_ok", self.kinds(a))

    def test_check_with_an_unreachable_own_proxy_rests_the_account(self):
        a = self.acc(proxy_url="socks5://1.2.3.4:9999", session_json={"cookies": {"sessionid": "S"}})
        self.closed = {9999}
        res = ig.check(a)
        a.refresh_from_db()
        self.assertFalse(res.ok)
        self.assertEqual((a.status, a.status_reason), ("COOLDOWN", "proxy_down"))
        self.assertEqual(FakeIG.instances, [])

    def test_check_without_session_logs_in(self):
        a = self.acc(status="CHALLENGED")
        ig.check(a)
        a.refresh_from_db()
        self.assertEqual(a.status, "ACTIVE")
        self.assertEqual(len(FakeIG.instances[0].logins), 1)

    def test_pause_resume_and_cooldown(self):
        a = self.acc(status="ACTIVE", session_json={"cookies": {"sessionid": "S"}})
        ig.pause(a)
        a.refresh_from_db()
        self.assertEqual((a.status, a.status_reason), ("DISABLED", "paused"))
        ig.resume(a)
        a.refresh_from_db()
        self.assertEqual(a.status, "ACTIVE")
        b = self.acc("b", status="CHALLENGED", status_reason="needs_code", cooldown_until=timezone.now())
        ig.clear_cooldown(b)
        b.refresh_from_db()
        self.assertEqual(b.status, "CHALLENGED")   # still needs its code; only the rest ends
        self.assertIsNone(b.cooldown_until)
        ig.resume(self.acc("c", status="DISABLED"))
        self.assertEqual(InstagramEngineAccount.objects.get(username="c").status_reason, "session_expired")


class ConnectionTests(IgAccountsTestBase):
    def test_custom_proxy_from_provider_format(self):
        a = self.acc()
        res = ig.set_proxy(a, "custom", url="1.2.3.4:8000:user:pass")
        a.refresh_from_db()
        self.assertTrue(res.ok, res)
        self.assertEqual(ig.proxy_mode(a), "custom")
        self.assertEqual(a.clean_proxy, "http://user:pass@1.2.3.4:8000")
        self.assertGreater(a.next_outreach_at, timezone.now())   # a new IP rests before cold DMs
        self.assertIn("proxy_changed", self.kinds(a))
        self.assertFalse(ig.set_proxy(a, "custom", url="nonsense").ok)

    def test_config_is_kept_by_its_account(self):
        c1 = self.cfg(1091)
        a = self.acc()
        self.assertTrue(ig.set_proxy(a, "config", config=c1).ok)
        a.refresh_from_db()
        c1.refresh_from_db()
        self.assertEqual((a.proxy_config_id, a.proxy_url), (c1.pk, "socks5h://xray:1091"))
        self.assertEqual((c1.assigned_platform, c1.assigned_to), ("INSTAGRAM", "acc1"))
        self.assertTrue(ig.set_proxy(a, "system").ok)            # automatic: keeps its live config
        a.refresh_from_db()
        self.assertEqual(a.proxy_config_id, c1.pk)
        c2 = self.cfg(1092)
        self.assertTrue(ig.set_proxy(a, "new_config").ok)        # explicit: another free config
        a.refresh_from_db()
        self.assertEqual(a.proxy_config_id, c2.pk)
        self.assertTrue(ig.set_proxy(a, "pool").ok)
        a.refresh_from_db()
        self.assertEqual((a.proxy_config_id, a.proxy_url, a.clean_proxy), (None, None, "socks5h://xray:1080"))

    def test_config_not_loaded_in_xray_is_refused(self):
        c = self.cfg(1093)
        self.closed = {1093}
        a = self.acc()
        self.assertFalse(ig.set_proxy(a, "config", config=c).ok)
        a.refresh_from_db()
        self.assertIsNone(a.proxy_config_id)

    def test_heal_keeps_live_configs_splits_shared_ones_and_replaces_dead_ones(self):
        c1, c2, c3, c4 = self.cfg(1091), self.cfg(1092, status="DEAD"), self.cfg(1093), self.cfg(1094)
        a = self.acc("a", status="ACTIVE", proxy_config=c1, proxy_url="socks5h://xray:1091")
        b = self.acc("b", status="ACTIVE", proxy_config=c1, proxy_url="socks5h://xray:1091")   # old round-robin
        c = self.acc("c", status="ACTIVE", proxy_config=c2, proxy_url="socks5h://xray:1092")
        d = self.acc("d", proxy_url="http://u:p@5.6.7.8:3128")
        e = self.acc("e", status="DISABLED")
        out = ig.ensure_system_configs()
        for x in (a, b, c, d, e):
            x.refresh_from_db()
        self.assertEqual(out["healed_count"], 2)
        self.assertEqual(a.proxy_config_id, c1.pk)
        self.assertEqual({b.proxy_config_id, c.proxy_config_id}, {c3.pk, c4.pk})
        self.assertEqual(d.proxy_url, "http://u:p@5.6.7.8:3128")
        self.assertIsNone(e.proxy_config_id)
        self.assertIn("proxy_down", self.kinds(c))
        self.assertEqual(ig.ensure_system_configs()["healed_count"], 0)   # stable: nothing moves twice

    def test_forced_rotation_off_a_live_config_moves_only_that_account(self):
        # the scraper saw the proxy fail once, but xray still serves the config: other accounts keep using it
        c1, c2 = self.cfg(1091), self.cfg(1092)
        a = self.acc("a", status="ACTIVE", proxy_config=c1, proxy_url="socks5h://xray:1091")
        ig._heal_account(a, force_rotate=True, failed_config_id=c1.pk)
        a.refresh_from_db()
        c1.refresh_from_db()
        self.assertEqual(a.proxy_config_id, c2.pk)
        self.assertEqual(c1.status, "ACTIVE")
        self.assertNotIn("proxy_down", self.kinds(a))

    def test_forced_rotation_off_a_dead_config_marks_it_dead(self):
        c1, c2 = self.cfg(1091), self.cfg(1092)
        a = self.acc("a", status="ACTIVE", proxy_config=c1, proxy_url="socks5h://xray:1091")
        self.closed = {1091}
        ig._heal_account(a, force_rotate=True, failed_config_id=c1.pk)
        a.refresh_from_db()
        c1.refresh_from_db()
        self.assertEqual(a.proxy_config_id, c2.pk)
        self.assertEqual(c1.status, "DEAD")
        self.assertIn("proxy_down", self.kinds(a))

    def test_legacy_port_is_linked_to_its_config(self):
        c = self.cfg(1095)
        a = self.acc(proxy_url="socks5h://xray:1095")
        ig.ensure_system_configs()
        a.refresh_from_db()
        self.assertEqual(a.proxy_config_id, c.pk)
        self.assertEqual(self.kinds(a), [])   # same IP: no move, no settle time

    def test_nothing_moves_while_xray_is_down(self):
        self.cfg(1091)
        a = self.acc()
        self.closed = {1080}
        self.assertEqual(ig.ensure_system_configs()["skipped"], "xray_down")
        a.refresh_from_db()
        self.assertIsNone(a.proxy_config_id)

    def test_deleted_config_moves_its_account(self):
        c1, c2 = self.cfg(1091), self.cfg(1092)
        a = self.acc(proxy_config=c1, proxy_url="socks5h://xray:1091")
        c1.delete()
        a.refresh_from_db()
        self.assertEqual(a.proxy_config_id, c2.pk)
        self.assertIn("proxy_down", self.kinds(a))

    def test_add_account_takes_a_free_config_and_logs_in(self):
        c = self.cfg(1091)
        acc, res = ig.add_account(username="@New.User", password="pw", tenant_id="tenant_9")
        self.assertTrue(res.ok, res)
        self.assertEqual((acc.username, acc.proxy_config_id, acc.status), ("new.user", c.pk, "ACTIVE"))
        self.assertEqual(self.kinds(acc)[:2], ["created", "proxy_changed"])
        again, res2 = ig.add_account(username="new.user", password="pw")
        self.assertFalse(res2.ok)
        self.assertTrue(res2.extra.get("exists"))

    def test_rename_moves_the_leads_with_the_account(self):
        from apps.discovery.models import InstagramEngineLead

        a = self.acc("old_name")
        lead = InstagramEngineLead.objects.create(username="lead1", tenant_id="tenant_9", sender_account="Old_Name")
        self.assertFalse(ig.update_settings(a, username="OLD_NAME").extra["changed"])   # only the case differs
        res = ig.update_settings(a, username="new_name", daily_limit="۲۵")
        lead.refresh_from_db()
        a.refresh_from_db()
        self.assertTrue(res.ok)
        self.assertEqual((a.username, a.daily_limit, lead.sender_account), ("new_name", 25, "new_name"))
        self.assertFalse(ig.update_settings(self.acc("taken"), username="new_name").ok)

    def test_add_account_refuses_bad_input(self):
        self.assertIsNone(ig.add_account(username="bad name!", password="pw")[0])
        self.assertIsNone(ig.add_account(username="ok_name", password="")[0])
        self.assertIsNone(ig.add_account(username="ok_name", password="pw", proxy="custom", proxy_url="x")[0])


class ExportTests(IgAccountsTestBase):
    def test_config_export_link_and_qr(self):
        c = self.cfg(1091)
        a = self.acc(proxy_config=c, proxy_url="socks5h://xray:1091")
        exp = ig.export_config(a)
        self.assertEqual((exp["kind"], exp["link"], exp["shared_with"]), ("v2ray", c.raw_url, []))
        self.assertIn("<svg", ig.qr_svg(exp["link"]))
        self.assertIn(c.raw_url, ig.export_text([a]))

    def test_custom_proxy_export(self):
        a = self.acc(proxy_url="socks5://u:p@1.2.3.4:1080#rotate=http://x")
        exp = ig.export_config(a)
        self.assertEqual((exp["kind"], exp["link"], exp["host"], exp["port"]), ("proxy", "socks5://u:p@1.2.3.4:1080",
                                                                                "1.2.3.4", 1080))


class TenantPitchTests(IgAccountsTestBase):
    def test_profile_is_used_by_every_sender_of_the_customer(self):
        from apps.discovery.outreach_engine import _pitch_for

        a, b = self.acc("s1", tenant_id="tenant_9"), self.acc("s2", tenant_id="tenant_9")
        other = self.acc("s3", tenant_id="tenant_10")
        ig.save_tenant_profile("tenant_9", consultant_name="سارا", pitch_template="سلام {consultant_name}")
        for s in (a, b):
            self.assertEqual(_pitch_for({}, s), ("سارا", "سلام {consultant_name}"))
        self.assertEqual(_pitch_for({}, other)[0], "s3")
        self.assertEqual(_pitch_for({}, "s1")[0], "s1")   # a bare username keeps the old lookup
        ig.save_tenant_profile("tenant_9", consultant_name="مینا", only_empty=True)
        self.assertEqual(OutreachTenantProfile.objects.get(tenant_id="tenant_9").consultant_name, "سارا")

    def test_legacy_pitch_is_the_fallback_and_aliases_share_a_profile(self):
        self.acc("old_sender", tenant_id="sarv")
        cfg = {"sender_pitches": {"old_sender": {"consultant_name": "رها", "template": "متن قدیمی"}}}
        with mock.patch("apps.discovery.outreach_engine.load_outreach_config", return_value=cfg):
            p = ig.tenant_pitch("tenant_4")
            self.assertEqual((p["consultant_name"], p["pitch_template"], p["source"]), ("رها", "متن قدیمی", "legacy"))
            ig.save_tenant_profile("sarv", pitch_template="متن تازه")
            p = ig.tenant_pitch("tenant_4")
        self.assertEqual((p["consultant_name"], p["pitch_template"], p["source"]), ("رها", "متن تازه", "profile"))
        self.assertTrue(OutreachTenantProfile.objects.filter(tenant_id="tenant_4").exists())


class MigrationDataTests(IgAccountsTestBase):
    def test_links_xray_ports_and_drops_old_pending_keys(self):
        mig = importlib.import_module("apps.discovery.migrations.0037_instagram_account_management")
        c = self.cfg(1096)
        a = self.acc(proxy_url="socks5h://xray:1096#rotate=x")
        phone = dict(a.device_settings or {})
        InstagramEngineAccount.objects.filter(pk=a.pk).update(   # what an old "waiting for a code" row looked like
            device_settings={**phone, "settings": {"x": 1}, "last_json": {"y": 2}, "challenge_hint": "z"})
        b = self.acc("b", proxy_url="http://xray:8080")
        mig.link_configs_and_clean_pending(django_apps, None)
        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual(a.proxy_config_id, c.pk)
        self.assertEqual(a.device_settings, phone)
        self.assertIsNone(b.proxy_config_id)


@override_settings(INSTAGRAM_LOGIN_IN_BACKGROUND=True, INSTAGRAM_DEFAULT_PROXY_URL="socks5h://xray:1080")
class BackgroundLoginThreadTests(TransactionTestCase):
    """As in production: the login runs in its own thread and the code typed later (another request) reaches it."""

    def setUp(self):
        FakeIG.instances, FakeIG.script = [], {"mode": "challenge"}
        for p in (mock.patch("instagrapi.Client", FakeIG), mock.patch.object(ig, "port_open", return_value=True),
                  mock.patch.object(ig, "CODE_POLL_SECONDS", 0.05)):
            p.start()
            self.addCleanup(p.stop)
        self.acc = InstagramEngineAccount.objects.create(username="bg1", password="pw", role="SENDER")
        self.addCleanup(self._stop_thread)   # runs first (cleanups are LIFO), while FakeIG is still patched

    def _stop_thread(self):
        acc = InstagramEngineAccount.objects.filter(pk=self.acc.pk).first()
        if acc is not None:
            ig.cancel_login(acc)
        for t in threading.enumerate():
            if t.name == f"ig-login-{self.acc.pk}":
                t.join(10)
                self.assertFalse(t.is_alive(), "login thread did not stop")

    def fresh(self):
        return InstagramEngineAccount.objects.get(pk=self.acc.pk)

    def test_code_typed_later_goes_into_the_same_login(self):
        res = ig.start_login(self.acc, actor="boss", wait=15)
        self.assertEqual(res.state, "needs_code", res.message)
        self.assertEqual(ig.login_state(self.acc)["hint"], "ایمیل j***@gmail.com")
        self.assertEqual((self.fresh().status, self.fresh().status_reason), ("CHALLENGED", "needs_code"))
        res = ig.submit_code(self.fresh(), "۱۲۳۴۵۶", actor="boss", wait=15)   # Persian digits from the phone keyboard
        self.assertTrue(res.ok, res.message)
        self.assertEqual(res.state, "ok")
        acc = self.fresh()
        self.assertEqual((acc.status, acc.status_reason, acc.pending_login.get("state")), ("ACTIVE", "", "ok"))
        self.assertEqual(len(FakeIG.instances), 1)
        self.assertEqual((len(FakeIG.instances[0].logins), FakeIG.instances[0].codes), (1, [CODE]))   # one login
        self.assertEqual(acc.session_json["cookies"]["sessionid"], "sid-new")
        self.assertTrue(InstagramAccountEvent.objects.filter(account=acc, kind="code_ok").exists())

    def test_two_factor_wrong_code_then_right_code_in_the_same_login(self):
        FakeIG.script = {"mode": "two_factor"}
        res = ig.start_login(self.acc, wait=15)
        self.assertEqual((res.state, res.hint), ("needs_code", "پیامک به **12"), res.message)
        res = ig.submit_code(self.fresh(), "000000", wait=15)
        self.assertEqual(res.state, "needs_code", res.message)   # asked again, same login
        self.assertIn("پذیرفته نشد", ig.login_state(self.acc)["error"])
        res = ig.submit_code(self.fresh(), CODE, wait=15)
        self.assertTrue(res.ok, res.message)
        self.assertEqual(self.fresh().status, "ACTIVE")
        self.assertEqual((len(FakeIG.instances), len(FakeIG.instances[0].logins)), (1, 1))
        tries = [d["verification_code"] for e, d in FakeIG.instances[0].private_calls if e == "accounts/two_factor_login/"]
        self.assertEqual(tries, ["000000", CODE])

    def test_cancel_stops_the_waiting_login(self):
        self.assertEqual(ig.start_login(self.acc, wait=15).state, "needs_code")
        self.assertEqual(ig.cancel_login(self.fresh()).state, "cancelled")
        for t in threading.enumerate():
            if t.name == f"ig-login-{self.acc.pk}":
                t.join(10)
                self.assertFalse(t.is_alive())
        acc = self.fresh()
        self.assertEqual((acc.status, acc.status_reason, acc.pending_login["state"]),
                         ("CHALLENGED", "session_expired", "cancelled"))
        self.assertFalse(ig.submit_code(acc, CODE).ok)   # nobody waits for a code any more

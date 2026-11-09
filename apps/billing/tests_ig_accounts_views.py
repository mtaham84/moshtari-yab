"""/manage/instagram/accounts/ (the one place to manage Instagram accounts), the old dashboard endpoints that now
wrap the same service, and the customer outreach page rules (customers edit their pitch, never the accounts)."""
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from instagrapi.exceptions import ChallengeRequired

from apps.businesses.models import Business
from apps.discovery import ig_accounts as ig
from apps.discovery import ig_state
from apps.discovery.models import InstagramAccountEvent, InstagramEngineAccount, OutreachTenantProfile, ProxyConfig
from apps.discovery.proxies import health
from apps.discovery.proxies.urls import proxy_mode
from apps.discovery.tests_ig_accounts import CODE, FakeIG

XHR = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}


def _waiting(acc, state="needs_code"):
    """A login that waits for the operator's code right now (as the login thread leaves it)."""
    now = ig_state.now_iso()
    InstagramEngineAccount.objects.filter(pk=acc.pk).update(
        status="CHALLENGED", status_reason="needs_code",
        pending_login={"state": state, "token": "t1", "started_at": now, "heartbeat": now, "kind": "challenge",
                       "hint": "ایمیل j***@gmail.com"})


def _login_thread(pk):
    """Stands in for the login thread while a request waits: takes the typed code and finishes the login."""
    def sleep(seconds):
        p = ig._read_pending(pk)
        if p.get("state") == "verifying" and p.get("code") == CODE:
            InstagramEngineAccount.objects.filter(pk=pk).update(
                status="ACTIVE", status_reason="", pending_login={**p, "state": "ok", "code": None,
                                                                  "message": "کد تأیید پذیرفته شد و اکانت وارد شد."})
    return sleep


@override_settings(INSTAGRAM_LOGIN_IN_BACKGROUND=False, INSTAGRAM_DEFAULT_PROXY_URL="socks5h://xray:1080")
class IgAccountsPanelTests(TestCase):
    def setUp(self):
        FakeIG.instances, FakeIG.script = [], {}
        for p in (mock.patch("instagrapi.Client", FakeIG), mock.patch.object(health, "port_open", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        self.staff = get_user_model().objects.create_user("boss", "b@x.ir", "pw", is_staff=True)
        self.client.force_login(self.staff)
        self.cfg = ProxyConfig.objects.create(raw_url="vless://u@h1.example:443?type=tcp#de1", protocol="vless",
                                              server="h1.example", port=443, local_port=1091, status="ACTIVE")

    def acc(self, username="acc1", **kw):
        kw.setdefault("password", "pw")
        kw.setdefault("role", "SENDER")
        kw.setdefault("tenant_id", "tenant_9")
        return InstagramEngineAccount.objects.create(username=username, **kw)

    def do(self, acc, action, data=None, **extra):
        return self.client.post(reverse("manage:ig_account_action", args=[acc.pk, action]), data or {}, **extra)

    def test_pages_render(self):
        a = self.acc(proxy_config=self.cfg, proxy_url="socks5h://xray:1091", status="CHALLENGED",
                     status_reason="needs_code")
        ig_state.log_event(a, "code_requested", "کد خواست")
        for url in (reverse("manage:ig_accounts"), reverse("manage:ig_accounts") + "?status=CHALLENGED&mode=config&q=acc",
                    reverse("manage:ig_account_detail", args=[a.pk]), reverse("manage:ig_tenants")):
            res = self.client.get(url)
            self.assertEqual(res.status_code, 200, url)
            self.assertContains(res, "acc1" if "tenants" not in url else "tenant_9")
        self.assertContains(self.client.get(reverse("manage:ig_account_detail", args=[a.pk])), "کد خواست")
        self.assertNotContains(self.client.get(reverse("manage:ig_accounts") + "?mode=custom"), "@acc1")

    def test_customers_cannot_open_the_panel(self):
        user = get_user_model().objects.create_user("shop", "s@x.ir", "pw")
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("manage:ig_accounts")).status_code, 403)
        a = self.acc()
        self.assertEqual(self.do(a, "pause").status_code, 403)

    def test_add_then_log_in(self):
        res = self.client.post(reverse("manage:ig_account_add"), {"username": "New_One", "password": "pw",
                                                                  "role": "SENDER", "tenant_id": "tenant_9",
                                                                  "proxy": "system"})
        a = InstagramEngineAccount.objects.get(username="new_one")
        self.assertRedirects(res, reverse("manage:ig_account_detail", args=[a.pk]) + "#login",
                             fetch_redirect_response=False)
        self.assertEqual((a.status, a.proxy_config_id), ("ACTIVE", self.cfg.pk))
        bad = self.client.post(reverse("manage:ig_account_add"), {"username": "x y", "password": "pw"})
        self.assertRedirects(bad, reverse("manage:ig_accounts") + "#add", fetch_redirect_response=False)

    def test_detail_shows_the_code_box_while_a_login_waits(self):
        a = self.acc()
        _waiting(a)
        res = self.client.get(reverse("manage:ig_account_detail", args=[a.pk]))
        self.assertContains(res, 'data-live="1"')
        self.assertContains(res, "ایمیل j***@gmail.com")
        st = self.client.get(reverse("manage:ig_account_login_state", args=[a.pk])).json()
        self.assertEqual((st["state"], st["live"], st["requires_code"]), ("needs_code", True, True))
        self.assertEqual(st["reason_display"], "منتظر کد تأیید")

    def test_code_typed_on_the_page_goes_to_the_waiting_login(self):
        a = self.acc()
        _waiting(a)
        out = self.client.post(reverse("manage:ig_account_action", args=[a.pk, "code"]), {"code": "۱۲۳۴۵۶"},
                               content_type="application/json", **XHR).json()
        self.assertTrue(out["success"], out)
        self.assertEqual(out["state"], "verifying")
        a.refresh_from_db()
        self.assertEqual((a.pending_login["state"], a.pending_login["code"]), ("verifying", CODE))
        self.assertEqual(FakeIG.instances, [])   # the waiting login sends it: no second login from the panel

    def test_stale_login_cannot_take_a_code(self):
        a = self.acc()
        _waiting(a)
        old = (timezone.now() - timezone.timedelta(minutes=10)).isoformat()
        InstagramEngineAccount.objects.filter(pk=a.pk).update(
            pending_login={**ig._read_pending(a.pk), "heartbeat": old, "started_at": old})
        out = self.do(a, "code", {"code": CODE}, **XHR).json()
        self.assertFalse(out["success"])
        self.assertEqual(self.client.get(reverse("manage:ig_account_login_state", args=[a.pk])).json()["state"], "stale")

    def test_connection_and_status_actions(self):
        a = self.acc()
        res = self.do(a, "proxy", {"mode": "custom", "proxy_url": "socks5://u:p@9.9.9.9:1080"})
        self.assertRedirects(res, reverse("manage:ig_account_detail", args=[a.pk]) + "#connection",
                             fetch_redirect_response=False)
        a.refresh_from_db()
        self.assertEqual(proxy_mode(a), "custom")
        out = self.do(a, "proxy", {"mode": "config", "config_id": str(self.cfg.pk)}, **XHR).json()
        self.assertTrue(out["success"], out)
        self.assertFalse(self.do(a, "proxy", {"mode": "config", "config_id": ""}, **XHR).json()["success"])
        self.do(a, "pause")
        a.refresh_from_db()
        self.assertEqual(a.status, "DISABLED")
        self.do(a, "resume")
        a.refresh_from_db()
        self.assertEqual((a.status, a.status_reason), ("CHALLENGED", "session_expired"))   # no session yet
        out = self.do(a, "settings", {"daily_limit": "30", "role": "SENDER"}, **XHR).json()
        self.assertTrue(out["success"])
        self.assertEqual(InstagramEngineAccount.objects.get(pk=a.pk).daily_limit, 30)
        self.assertEqual(self.do(a, "nope", **XHR).status_code, 404)

    def test_sessionid_and_check_actions(self):
        a = self.acc(status="CHALLENGED")
        self.assertTrue(self.do(a, "session", {"sessionid": "SID"}, **XHR).json()["success"])
        out = self.do(a, "check", **XHR).json()
        self.assertTrue(out["success"], out)
        self.assertEqual(InstagramEngineAccount.objects.get(pk=a.pk).status, "ACTIVE")

    def test_config_export_page_and_files(self):
        a = self.acc(proxy_config=self.cfg, proxy_url="socks5h://xray:1091")
        res = self.client.get(reverse("manage:ig_account_config", args=[a.pk]))
        self.assertContains(res, self.cfg.raw_url)
        self.assertContains(res, "<svg")
        txt = self.client.get(reverse("manage:ig_account_config_txt", args=[a.pk]))
        self.assertIn("attachment", txt["Content-Disposition"])
        self.assertIn(self.cfg.raw_url, txt.content.decode())
        allc = self.client.get(reverse("manage:ig_accounts_configs_txt") + "?tenant=tenant_9")
        self.assertIn("@acc1", allc.content.decode())
        self.assertEqual(InstagramAccountEvent.objects.filter(account=a, kind="config_exported").count(), 3)

    def test_heal_and_delete(self):
        a = self.acc()
        self.assertEqual(self.client.post(reverse("manage:ig_accounts_heal")).status_code, 302)
        a.refresh_from_db()
        self.assertEqual(a.proxy_config_id, self.cfg.pk)
        res = self.do(a, "delete")
        self.assertRedirects(res, reverse("manage:ig_accounts"), fetch_redirect_response=False)
        self.assertFalse(InstagramEngineAccount.objects.filter(pk=a.pk).exists())
        self.cfg.refresh_from_db()
        self.assertEqual(self.cfg.assigned_to, "")

    def test_tenant_pitch_page_saves_the_profile(self):
        self.acc()
        res = self.client.post(reverse("manage:ig_tenants"), {"tenant_id": "tenant_9", "consultant_name": "سارا",
                                                              "consultant_role": "مشاور", "pitch_template": "سلام"})
        self.assertEqual(res.status_code, 302)
        prof = OutreachTenantProfile.objects.get(tenant_id="tenant_9")
        self.assertEqual((prof.consultant_name, prof.pitch_template), ("سارا", "سلام"))


@override_settings(INSTAGRAM_LOGIN_IN_BACKGROUND=False, INSTAGRAM_DEFAULT_PROXY_URL="socks5h://xray:1080")
class OldDashboardEndpointTests(TestCase):
    """The dashboard's scripts keep working: same URLs and JSON, now through the one service."""

    def setUp(self):
        FakeIG.instances, FakeIG.script = [], {}
        for p in (mock.patch("instagrapi.Client", FakeIG), mock.patch.object(health, "port_open", return_value=True)):
            p.start()
            self.addCleanup(p.stop)
        self.client.force_login(get_user_model().objects.create_user("boss", "b@x.ir", "pw", is_superuser=True,
                                                                     is_staff=True))

    def test_add_returns_the_old_json(self):
        out = self.client.post("/manage/accounts/add/", {"username": "dash_acc", "password": "pw", "role": "SENDER",
                                                         "tenant_id": "tenant_9", "consultant_name": "رها",
                                                         "proxy_url": "http://xray:8080"}, **XHR).json()
        self.assertTrue(out["success"], out)
        self.assertFalse(out["requires_code"])
        acc = InstagramEngineAccount.objects.get(pk=out["account_id"])
        self.assertEqual((out["username"], out["status"], acc.status), ("dash_acc", "ACTIVE", "ACTIVE"))
        self.assertEqual(OutreachTenantProfile.objects.get(tenant_id="tenant_9").consultant_name, "رها")

    def test_old_code_endpoint_feeds_the_waiting_login(self):
        acc = InstagramEngineAccount.objects.create(username="c1", password="pw", role="SENDER")
        _waiting(acc)
        with mock.patch.object(ig, "_sleep", _login_thread(acc.pk)):
            out = self.client.post(f"/manage/accounts/{acc.pk}/code/", {"code": CODE}, content_type="application/json",
                                   **XHR).json()
        self.assertTrue(out["success"], out)
        self.assertEqual((out["state"], out["status"]), ("ok", "ACTIVE"))
        self.assertFalse(self.client.post(f"/manage/accounts/{acc.pk}/code/", {}, **XHR).json()["success"])

    def test_sender_edit_and_clear_cooldown(self):
        acc = InstagramEngineAccount.objects.create(username="s1", password="pw", role="SENDER", tenant_id="tenant_9",
                                                    status="CHALLENGED", status_reason="needs_code",
                                                    cooldown_until=timezone.now() + timezone.timedelta(hours=1))
        out = self.client.post(f"/manage/senders/{acc.pk}/edit/", {
            "username": "s1", "tenant_id": "tenant_9", "consultant_name": "پنیا", "account_tier": "MEDIUM",
            "daily_limit": "40", "proxy_url": "1.2.3.4:8000:u:p", "status": "DISABLED"}, **XHR).json()
        self.assertTrue(out["success"], out)
        acc.refresh_from_db()
        self.assertEqual((acc.account_tier, acc.daily_limit, acc.status), ("MEDIUM", 40, "DISABLED"))
        self.assertEqual(acc.clean_proxy, "http://u:p@1.2.3.4:8000")
        self.assertEqual(OutreachTenantProfile.objects.get(tenant_id="tenant_9").consultant_name, "پنیا")
        out = self.client.post(f"/manage/senders/{acc.pk}/clear-cooldown/", **XHR).json()
        acc.refresh_from_db()
        self.assertEqual((out["status"], acc.cooldown_until), ("DISABLED", None))   # a paused account stays paused

    def test_old_test_endpoint_runs_the_check(self):
        acc = InstagramEngineAccount.objects.create(username="t1", password="pw", role="SCRAPER",
                                                    session_json={"cookies": {"sessionid": "S"}})
        out = self.client.post(f"/manage/accounts/{acc.pk}/test/").json()
        self.assertTrue(out["success"], out)
        self.assertEqual((out["status"], out["challenge_required"]), ("ACTIVE", False))
        FakeIG.script = {"feed_error": ChallengeRequired("challenge_required")}
        out = self.client.post(f"/manage/accounts/{acc.pk}/test/").json()   # session refused -> a fresh login
        self.assertEqual(len(FakeIG.instances[-1].logins), 1)

    def test_dashboard_still_renders(self):
        InstagramEngineAccount.objects.create(username="d1", password="pw", role="SCRAPER")
        InstagramEngineAccount.objects.create(username="d2", password="pw", role="SENDER", tenant_id="tenant_9")
        for tab in ("", "?tab=outreach"):
            res = self.client.get("/manage/" + tab)
            self.assertEqual(res.status_code, 200)
            self.assertContains(res, reverse("manage:ig_accounts"))


class CustomerOutreachRulesTests(TestCase):
    def setUp(self):
        FakeIG.instances, FakeIG.script = [], {}
        p = mock.patch("instagrapi.Client", FakeIG)
        p.start()
        self.addCleanup(p.stop)
        User = get_user_model()
        self.owner = User.objects.create_user("owner", "o@x.ir", "pw")
        self.biz = self._business(self.owner, "فروشگاه یک")
        self.other_owner = User.objects.create_user("owner2", "o2@x.ir", "pw")
        self.other = self._business(self.other_owner, "فروشگاه دو")
        self.mine = InstagramEngineAccount.objects.create(username="mine", password="pw", role="SENDER",
                                                          tenant_id=f"tenant_{self.biz.pk}")
        self.theirs = InstagramEngineAccount.objects.create(username="theirs", password="pw", role="SENDER",
                                                            tenant_id=f"tenant_{self.other.pk}")
        self.client.force_login(self.owner)

    def _business(self, user, name):
        fields = {f.name for f in Business._meta.get_fields()}
        kw = {"name": name}
        for owner_field in ("owner", "user"):
            if owner_field in fields:
                kw[owner_field] = user
        return Business.objects.create(**kw)

    def test_customer_edits_only_its_own_pitch(self):
        res = self.client.post(reverse("accounts:save_pitch"), {"username": "theirs", "consultant_name": "مریم",
                                                                "template": "سلام از ما"})
        self.assertEqual(res.status_code, 302)
        mine = OutreachTenantProfile.objects.get(tenant_id=f"tenant_{self.biz.pk}")
        self.assertEqual((mine.consultant_name, mine.pitch_template), ("مریم", "سلام از ما"))
        self.assertFalse(OutreachTenantProfile.objects.filter(tenant_id=f"tenant_{self.other.pk}").exists())

    def test_customer_cannot_add_or_edit_accounts(self):
        self.client.post(reverse("accounts:add_outreach_account"), {"username": "sneaky", "password": "pw"})
        self.assertFalse(InstagramEngineAccount.objects.filter(username="sneaky").exists())
        res = self.client.post(reverse("accounts:edit_outreach_account", args=[self.mine.pk]),
                               {"password": "new", "sessionid": "S"})
        self.assertEqual(res.status_code, 403)
        res = self.client.post(reverse("accounts:clear_outreach_cooldown", args=[self.mine.pk]))
        self.assertEqual(res.status_code, 403)
        self.mine.refresh_from_db()
        self.assertEqual(self.mine.password, "pw")
        self.assertEqual(FakeIG.instances, [])

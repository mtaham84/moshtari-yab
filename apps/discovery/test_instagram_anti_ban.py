"""Instagram anti-ban safeguards: one stable phone per account, saved sessions reused as they are, inbox checks only
in shift hours, varied pre-DM journeys, no fallback to the server's own IP, 4G IP rotation only after errors."""
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase

from apps.discovery import device_emulator as dev
from apps.discovery import outreach_engine as engine
from apps.discovery import outreach_service
from apps.discovery.models import InstagramEngineAccount
from apps.discovery.proxy_service import prepare_sender_proxy_before_dm

TENANT = "tenant_ab"
SESSIONID = "123:" + "a" * 40


def _instagrapi_client():
    try:
        from instagrapi import Client
    except Exception:
        return None
    return Client() if isinstance(Client, type) else None


def _phone(uuid="u-1", phone_id="p-1", model="SM-S928B") -> dict:
    return {
        "uuids": {"uuid": uuid, "phone_id": phone_id, "client_session_id": "c-1", "advertising_id": "a-1",
                  "android_device_id": "android-0123456789abcdef"},
        "device_settings": {"model": model, "manufacturer": "Samsung", "device": "e3q", "android_version": 34,
                            "android_release": "14", "dpi": "500dpi", "resolution": "1440x3120", "cpu": "qcom",
                            "app_version": "449.0.0.52.84", "version_code": "1079242191"},
    }


def _account(username="phone", **kw) -> InstagramEngineAccount:
    kw.setdefault("role", "SENDER")
    return InstagramEngineAccount.objects.create(username=username, password="x", tenant_id=TENANT, **kw)


def _saved_session(cl, account) -> dict:
    """What a successful login stores: the client's settings on the account's phone plus a session cookie."""
    dev.apply_device_profile_to_client(cl, account)
    saved = cl.get_settings()
    saved["cookies"] = {"sessionid": SESSIONID}
    saved["authorization_data"] = {"ds_user_id": "123", "sessionid": SESSIONID, "should_use_header_over_cookies": True}
    account.session_json = saved
    account.save(update_fields=["session_json"])
    return saved


class StableDeviceProfileTests(TestCase):
    def test_pending_login_keeps_the_phone_it_was_started_on(self):
        acc = _account(device_settings={"settings": {**_phone(), "cookies": {}}, "last_json": {"challenge": {}},
                                        "challenge_hint": "code"})
        self.assertEqual(acc.device_settings["uuids"]["uuid"], "u-1")
        self.assertEqual(acc.device_settings["device_settings"]["model"], "SM-S928B")
        self.assertIn("last_json", acc.device_settings)  # still needed by the verification-code step

    def test_phone_of_the_saved_session_is_reused(self):
        acc = _account(session_json={**_phone("s-9", "sp-9"), "cookies": {"sessionid": SESSIONID}})
        self.assertEqual(acc.device_settings["uuids"]["uuid"], "s-9")

    def test_a_new_profile_is_created_once_and_keeps_proxy_meta(self):
        acc = _account(device_settings={"proxy_meta": {"rotation_url": "http://r"}})
        first = acc.device_settings["uuids"]["uuid"]
        self.assertEqual(acc.device_settings["proxy_meta"], {"rotation_url": "http://r"})
        acc.status_message = "x"
        acc.save(update_fields=["status_message"])
        acc.refresh_from_db()
        self.assertEqual(acc.device_settings["uuids"]["uuid"], first)

    def test_partial_save_also_stores_the_resolved_profile(self):
        acc = _account()
        InstagramEngineAccount.objects.filter(pk=acc.pk).update(device_settings={"settings": _phone("w-1", "wp-1")})
        acc = InstagramEngineAccount.objects.get(pk=acc.pk)
        acc.status = "ACTIVE"
        acc.save(update_fields=["status"])
        acc.refresh_from_db()
        self.assertEqual(acc.device_settings["uuids"]["uuid"], "w-1")

    def test_stored_device_follows_the_logged_in_client_and_drops_pending_keys(self):
        acc = _account(device_settings={"settings": _phone("old", "old-p"), "challenge_hint": "x", "proxy_meta": {"isp": "MCI"}})
        ds = dev.account_device_settings(acc, client_settings={**_phone("new", "new-p"), "cookies": {"sessionid": SESSIONID}})
        self.assertEqual(ds["uuids"]["uuid"], "new")
        self.assertEqual(ds["proxy_meta"], {"isp": "MCI"})
        self.assertNotIn("settings", ds)
        self.assertNotIn("challenge_hint", ds)
        bare = dev.account_device_settings(acc, client_settings={"cookies": {"sessionid": SESSIONID}})
        self.assertEqual(bare["uuids"]["uuid"], "old")  # a bare cookie never replaces the phone


class SavedSessionClientTests(TestCase):
    def setUp(self):
        self.cl = _instagrapi_client()
        if self.cl is None:
            self.skipTest("instagrapi is not installed")

    def test_profile_survives_the_reinit_done_by_login_by_sessionid(self):
        acc = _account("dev_a")
        profile = acc.device_settings
        dev.apply_device_profile_to_client(self.cl, acc)
        self.cl.init()  # login_by_sessionid() re-reads cl.settings through init()
        self.assertEqual(self.cl.device_settings["model"], profile["device_settings"]["model"])
        self.assertEqual(self.cl.uuid, profile["uuids"]["uuid"])
        self.assertEqual(self.cl.phone_id, profile["uuids"]["phone_id"])

    def test_saved_session_is_restored_without_a_new_login(self):
        acc = _account("dev_b")
        saved = _saved_session(self.cl, acc)
        fresh = _instagrapi_client()
        with patch.object(type(fresh), "login_by_sessionid", side_effect=AssertionError("no new login")):
            self.assertFalse(dev.connect_saved_session(fresh, acc))
        self.assertEqual(fresh.uuid, saved["uuids"]["uuid"])
        self.assertEqual(fresh.device_settings["model"], saved["device_settings"]["model"])
        self.assertEqual(fresh.user_agent, saved["user_agent"])
        self.assertEqual(fresh.private.cookies.get("sessionid"), SESSIONID)

    def test_sender_client_reuses_the_session_and_keeps_its_timeouts(self):
        engine._CLIENTS.clear()
        self.addCleanup(engine._CLIENTS.clear)
        acc = _account("dev_c", proxy_url="socks5h://xray:1095#rotate=http://r")
        saved = _saved_session(self.cl, acc)
        with patch.object(type(self.cl), "login_by_sessionid", side_effect=AssertionError("no new login")):
            cl = engine.get_sender_client(acc)
        self.assertEqual(cl.uuid, saved["uuids"]["uuid"])
        self.assertEqual(type(cl.private.get_adapter("https://i.instagram.com")).__name__, "TimeoutHTTPAdapter")
        self.assertEqual(cl.private.proxies.get("https"), "socks5h://xray:1095")

    def test_connection_test_keeps_the_phone_and_the_session(self):
        user = get_user_model().objects.create_user("boss", "b@x.ir", "pw", is_staff=True, is_superuser=True)
        self.client.force_login(user)
        acc = _account("dev_t", status="ACTIVE")
        saved = _saved_session(self.cl, acc)
        with patch.object(type(self.cl), "login_by_sessionid", side_effect=AssertionError("no new login")), \
                patch.object(type(self.cl), "get_timeline_feed", return_value={}) as feed, \
                patch("apps.discovery.ig_accounts.port_open", return_value=True):   # xray's pool port, as in docker
            res = self.client.post(f"/manage/accounts/{acc.pk}/test/").json()
        self.assertTrue(res["success"], res)
        feed.assert_called_once()
        acc.refresh_from_db()
        self.assertEqual(acc.device_settings["uuids"]["uuid"], saved["uuids"]["uuid"])
        self.assertEqual(acc.session_json["uuids"]["uuid"], saved["uuids"]["uuid"])
        self.assertEqual(acc.session_json["device_settings"]["model"], saved["device_settings"]["model"])


class BareSessionTests(TestCase):
    def test_bare_sessionid_gets_the_account_phone_then_one_login(self):
        acc = _account("bare", session_json={"cookies": {"sessionid": SESSIONID}})
        cl = MagicMock()
        with patch.object(dev, "apply_device_profile_to_client") as apply:
            self.assertTrue(dev.connect_saved_session(cl, acc))
        apply.assert_called_once_with(cl, acc)
        cl.login_by_sessionid.assert_called_once_with(SESSIONID)
        cl.set_settings.assert_not_called()

    def test_missing_session_is_login_required(self):
        acc = _account("nosession")
        with self.assertRaisesMessage(RuntimeError, "login_required"):
            dev.connect_saved_session(MagicMock(), acc)


class ReplySyncScheduleTests(TestCase):
    def test_delay_is_random_between_five_and_ten_minutes(self):
        delays = [engine.next_reply_sync_delay() for _ in range(40)]
        self.assertTrue(all(300 <= d <= 600 for d in delays))
        self.assertGreater(len({round(d) for d in delays}), 1)

    def test_no_inbox_checks_outside_the_shift(self):
        with patch.object(engine.HumanShiftPlanner, "is_shift_active", return_value=(False, "استراحت شبانه", 600)):
            self.assertEqual(engine.reply_sync_allowed(), (False, "استراحت شبانه"))
            self.assertTrue(engine.reply_sync_allowed(ignore_shift=True)[0])

    def test_outreach_command_does_not_check_the_inbox_outside_the_shift(self):
        mod = "apps.discovery.management.commands.run_instagram_outreach"
        last = {"action": "paused", "reason": "استراحت شبانه", "wait_seconds": 600}
        with patch(f"{mod}.run_outreach_batch", return_value={"sent": 0, "last": last}), \
                patch(f"{mod}.reply_sync_allowed", return_value=(False, "استراحت شبانه")), \
                patch(f"{mod}.check_and_sync_lead_replies") as sync:
            call_command("run_instagram_outreach", "--once", stdout=StringIO())
        sync.assert_not_called()

    def test_compose_keeps_ig_outreach_inside_shift_hours(self):
        compose = (Path(settings.BASE_DIR) / "docker-compose.yml").read_text(encoding="utf-8")
        line = next(ln for ln in compose.splitlines() if "run_instagram_outreach" in ln)
        self.assertNotIn("--ignore-circadian", line)
        self.assertNotIn("--ignore-shift", line)


@patch("apps.discovery.outreach_service.time.sleep")
class OrganicJourneyTests(SimpleTestCase):
    def test_quiet_journey_skips_optional_steps_but_keeps_the_target(self, _sleep):
        cl = MagicMock()
        with patch("apps.discovery.outreach_service.random.random", return_value=0.99):
            res = outreach_service.execute_organic_journey(cl, "buyer", target_pk=42)
        self.assertEqual(res["target_pk"], 42)
        for call in (cl.get_timeline_feed, cl.search_users, cl.user_medias, cl.user_stories, cl.media_like):
            call.assert_not_called()

    def test_unknown_pk_is_always_searched(self, _sleep):
        cl = MagicMock()
        cl.search_users.return_value = [SimpleNamespace(username="buyer", pk="77")]
        with patch("apps.discovery.outreach_service.random.random", return_value=0.99):
            res = outreach_service.execute_organic_journey(cl, "buyer", target_pk=None)
        self.assertEqual(res["target_pk"], 77)

    def test_a_like_only_goes_to_a_post_seen_on_the_profile(self, _sleep):
        cl = MagicMock()
        cl.get_timeline_feed.return_value = {"feed_items": []}
        cl.user_stories.return_value = []
        cl.user_medias.return_value = [SimpleNamespace(id="m1")]
        with patch("apps.discovery.outreach_service.random.random", return_value=0.0):
            res = outreach_service.execute_background_organic_warmup(cl, "buyer", target_pk=42)
        cl.user_medias.assert_called_once()  # the profile view's posts are reused, no second request
        cl.media_like.assert_called_once_with("m1")
        self.assertTrue(res["target_post_liked"])

    def test_likes_and_story_views_are_occasional(self, _sleep):
        self.assertLessEqual(outreach_service.ORGANIC_JOURNEY_ODDS["like"], 0.25)
        self.assertLess(outreach_service.ORGANIC_JOURNEY_ODDS["story"], 1.0)


class ProxyRotationTests(SimpleTestCase):
    def _acc(self):
        return SimpleNamespace(username="a", proxy_url="socks5h://xray:1095#rotate=http://r", device_settings={})

    def test_no_ip_rotation_before_a_normal_dm(self):
        with patch("apps.discovery.proxy_service.rotate_account_proxy_ip") as rotate, \
                patch("apps.discovery.proxy_service.inspect_proxy_egress", return_value={"ip": "5.6.7.8"}):
            res = prepare_sender_proxy_before_dm(self._acc())
        rotate.assert_not_called()
        self.assertFalse(res["rotated"])
        self.assertEqual(res["egress_ip"], "5.6.7.8")

    def test_rotation_when_explicitly_asked(self):
        with patch("apps.discovery.proxy_service.rotate_account_proxy_ip", return_value={"rotated": True, "new_ip": "9.9.9.9"}) as rotate, \
                patch("apps.discovery.proxy_service.inspect_proxy_egress"):
            res = prepare_sender_proxy_before_dm(self._acc(), rotate=True)
        rotate.assert_called_once()
        self.assertTrue(res["rotated"])


class ScraperProxyTests(TestCase):
    def test_unreachable_proxy_rests_the_account_instead_of_going_direct(self):
        from apps.discovery.ig_scraper.clients import client_or_rest
        from apps.discovery.ig_scraper.context import Run

        acc = _account("scr", role="SCRAPER", proxy_url="socks5h://xray:1081")
        run = Run(stdout=StringIO(), stderr=StringIO())
        with patch("apps.discovery.ig_accounts.proxy_reachable", return_value=(False, "refused")), \
                patch("instagrapi.Client") as client_cls:
            self.assertIsNone(client_or_rest(run, acc))
        client_cls.return_value.set_proxy.assert_not_called()
        acc.refresh_from_db()
        self.assertEqual(acc.status, "COOLDOWN")
        self.assertIsNotNone(acc.cooldown_until)


class SenderSessionPersistTests(TestCase):
    def test_rotated_cookies_are_kept_on_the_same_phone(self):
        engine._CLIENTS.clear()
        self.addCleanup(engine._CLIENTS.clear)
        acc = _account("persist", session_json={**_phone(), "cookies": {"sessionid": SESSIONID}})
        cl = MagicMock()
        cl.get_settings.return_value = {**_phone(), "cookies": {"sessionid": SESSIONID, "rur": "new"}}
        engine._CLIENTS[acc.username] = cl
        engine.persist_sender_session(acc)
        acc.refresh_from_db()
        self.assertEqual(acc.session_json["cookies"]["rur"], "new")
        self.assertEqual(acc.session_json["uuids"]["uuid"], "u-1")

    def test_settings_without_a_phone_are_not_stored(self):
        engine._CLIENTS.clear()
        self.addCleanup(engine._CLIENTS.clear)
        acc = _account("persist2", session_json={**_phone(), "cookies": {"sessionid": SESSIONID}})
        cl = MagicMock()
        cl.get_settings.return_value = {"cookies": {"sessionid": SESSIONID}}
        engine._CLIENTS[acc.username] = cl
        engine.persist_sender_session(acc)
        acc.refresh_from_db()
        self.assertIn("uuids", acc.session_json)


class OperatingWindowTests(SimpleTestCase):
    """BUG-10: the dashboard and the anti-ban manager use the same configurable window."""

    def test_window_follows_configured_start_hour(self):
        from datetime import datetime

        from apps.discovery import anti_ban

        with patch.object(anti_ban, "INSTAGRAM_WINDOW_START_HOUR", 7), patch.object(anti_ban, "INSTAGRAM_WINDOW_END_HOUR", 23):
            self.assertTrue(anti_ban.in_operating_window(8))
            self.assertFalse(anti_ban.in_operating_window(6))
            self.assertFalse(anti_ban.in_operating_window(23))
            self.assertEqual(anti_ban.next_window_start(datetime(2026, 1, 1, 5, 30)), datetime(2026, 1, 1, 7, 0))
            self.assertEqual(anti_ban.next_window_start(datetime(2026, 1, 1, 23, 30)), datetime(2026, 1, 2, 7, 0))

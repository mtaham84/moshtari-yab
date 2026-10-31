import sys
from unittest import mock

sys.modules.setdefault("instagrapi", mock.MagicMock())

try:
    from requests.exceptions import Timeout, SSLError
except ImportError:
    class Timeout(Exception):
        pass

    class SSLError(Exception):
        pass

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.billing.manage_views import (
    TWITTER_SETTINGS_ENDPOINT,
    XSessionValidationResult,
    _call_twitter_settings_endpoint,
    _get_or_generate_ct0,
    _interpret_twitter_response,
    _test_twitter_account_connection,
)
from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount


class LoginRequired(Exception):
    pass


class ClientConnectionError(Exception):
    pass


class SessionReplacementTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("boss", "b@x.ir", "pw", is_staff=True, is_superuser=True)
        self.client.force_login(user)
        # Ensure HTTP layer tests do not attempt to invoke host twitter CLI executable
        self._which_patcher = mock.patch("shutil.which", return_value=None)
        self._which_patcher.start()
        self.addCleanup(self._which_patcher.stop)

    def _ig(self):
        return InstagramEngineAccount.objects.create(username="ig_a", password="pw", status="CHALLENGED",
                                                     session_json={"cookies": {"sessionid": "old"}})

    def _post_ig(self, acc, exc=None):
        fake = mock.MagicMock()
        fake.login_by_sessionid.side_effect = exc
        fake.get_settings.return_value = {"cookies": {"sessionid": "new"}}
        fake.device_settings = {"model": "Pixel"}
        with mock.patch("instagrapi.Client", return_value=fake):
            return self.client.post(f"/manage/accounts/{acc.pk}/session/", {"sessionid": "NEW"}, HTTP_X_REQUESTED_WITH="XMLHttpRequest").json()

    def test_ig_valid_session_reactivates_and_clears_message(self):
        acc = self._ig()
        acc.status_message = "x"
        acc.save()
        out = self._post_ig(acc)
        acc.refresh_from_db()
        self.assertTrue(out["success"])
        self.assertEqual((acc.status, acc.status_message), ("ACTIVE", ""))
        self.assertEqual(acc.session_json["cookies"]["sessionid"], "new")

    def test_ig_rejected_session_keeps_account_as_challenged(self):
        acc = self._ig()
        out = self._post_ig(acc, LoginRequired("login_required"))
        self.assertFalse(out["success"])
        self.assertFalse(out["deleted"])
        self.assertTrue(InstagramEngineAccount.objects.filter(pk=acc.pk).exists())
        acc.refresh_from_db()
        self.assertEqual(acc.status, "CHALLENGED")

    def test_ig_network_error_keeps_account(self):
        acc = self._ig()
        out = self._post_ig(acc, ClientConnectionError("timed out"))
        self.assertFalse(out["success"])
        self.assertFalse(out["deleted"])
        self.assertTrue(InstagramEngineAccount.objects.filter(pk=acc.pk).exists())

    def _x_post(self, acc, status_message, ok):
        def fake_test(account):
            account.status_message = status_message
            return ok, "CHALLENGED" if not ok else "ACTIVE", status_message
        with mock.patch("apps.billing.manage_views._test_twitter_account_connection", side_effect=fake_test):
            return self.client.post(f"/manage/twitter-accounts/{acc.pk}/session/", {"auth_token": "t" * 40, "ct0": "c" * 32}).json()

    def test_x_rejected_session_keeps_account_as_challenged(self):
        acc = TwitterEngineAccount.objects.create(username="x_a", auth_token="old", ct0="c", proxy_url="")
        out = self._x_post(acc, "سشن نامعتبر (HTTP 401)", False)
        self.assertFalse(out["success"])
        self.assertFalse(out["deleted"])
        self.assertTrue(TwitterEngineAccount.objects.filter(pk=acc.pk).exists())
        acc.refresh_from_db()
        self.assertEqual(acc.status, "CHALLENGED")

    def test_x_valid_session_is_kept(self):
        acc = TwitterEngineAccount.objects.create(username="x_b", auth_token="old", ct0="c", proxy_url="")
        out = self._x_post(acc, "متصل و فعال", True)
        self.assertTrue(out["success"])
        acc.refresh_from_db()
        self.assertEqual(acc.auth_token, "t" * 40)

    # -------------------------------------------------------------------------
    # HTTP Layer Mocked Tests
    # -------------------------------------------------------------------------

    def _x_acc(self, **kwargs):
        defaults = {"status": "ACTIVE", "auth_token": "old_t", "ct0": "old_c", "proxy_url": ""}
        defaults.update(kwargs)
        return TwitterEngineAccount.objects.create(**defaults)

    def _post_x_session(self, acc_pk, auth_token, ct0):
        return self.client.post(
            f"/manage/twitter-accounts/{acc_pk}/session/",
            {"auth_token": auth_token, "ct0": ct0},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        ).json()

    def test_x_http_200_valid_session_replaces_and_activates(self):
        acc = self._x_acc(username="x_http_200", status="CHALLENGED")
        resp = mock.MagicMock(status_code=200, text='{"screen_name": "x_http_200"}')
        resp.json.return_value = {"screen_name": "x_http_200"}
        with mock.patch("apps.billing.manage_views.requests.get", return_value=resp) as mock_get:
            out = self._post_x_session(acc.pk, "new_auth_token_40_chars_long_12345678", "new_ct0_32_chars_long_1234567890")
        self.assertTrue(out["success"])
        self.assertEqual(out["status"], "ACTIVE")
        acc.refresh_from_db()
        self.assertEqual(acc.status, "ACTIVE")
        self.assertEqual(acc.auth_token, "new_auth_token_40_chars_long_12345678")
        self.assertEqual(acc.ct0, "new_ct0_32_chars_long_1234567890")
        self.assertTrue(TwitterEngineAccount.objects.filter(pk=acc.pk).exists())
        mock_get.assert_called_once()

    def test_x_http_401_invalid_session_rejects_and_challenges(self):
        acc = self._x_acc(username="x_http_401", status="ACTIVE")
        resp = mock.MagicMock(status_code=401, text='{"errors":[{"code":32,"message":"Could not authenticate you."}]}')
        resp.json.return_value = {"errors": [{"code": 32, "message": "Could not authenticate you."}]}
        with mock.patch("apps.billing.manage_views.requests.get", return_value=resp) as mock_get:
            out = self._post_x_session(acc.pk, "bad_auth_token_40_chars_long_12345678", "bad_ct0_32_chars_long_1234567890")
        self.assertFalse(out["success"])
        self.assertFalse(out["deleted"])
        acc.refresh_from_db()
        self.assertEqual(acc.status, "CHALLENGED")
        self.assertTrue(TwitterEngineAccount.objects.filter(pk=acc.pk).exists())
        mock_get.assert_called_once()

    def test_x_http_403_forbidden_unknown_keeps_previous_state(self):
        acc = self._x_acc(username="x_http_403", status="ACTIVE")
        resp = mock.MagicMock(status_code=403, text='{"errors":[{"code":200,"message":"Forbidden"}]}')
        resp.json.return_value = {"errors": [{"code": 200, "message": "Forbidden"}]}
        with mock.patch("apps.billing.manage_views.requests.get", return_value=resp) as mock_get:
            out = self._post_x_session(acc.pk, "new_auth_token_40_chars_long_12345678", "new_ct0_32_chars_long_1234567890")
        self.assertFalse(out["success"])
        self.assertFalse(out["deleted"])
        self.assertTrue(out.get("inconclusive", False))
        acc.refresh_from_db()
        self.assertEqual(acc.status, "ACTIVE")
        self.assertEqual(acc.auth_token, "old_t")
        self.assertEqual(acc.ct0, "old_c")
        self.assertTrue(TwitterEngineAccount.objects.filter(pk=acc.pk).exists())
        mock_get.assert_called_once()

    def test_x_http_404_endpoint_error_keeps_previous_state(self):
        acc = self._x_acc(username="x_http_404", status="ACTIVE")
        resp = mock.MagicMock(status_code=404, text="404 Not Found")
        resp.json.side_effect = Exception("Not JSON")
        with mock.patch("apps.billing.manage_views.requests.get", return_value=resp) as mock_get:
            out = self._post_x_session(acc.pk, "new_auth_token_40_chars_long_12345678", "new_ct0_32_chars_long_1234567890")
        self.assertFalse(out["success"])
        self.assertFalse(out["deleted"])
        acc.refresh_from_db()
        self.assertEqual(acc.status, "ACTIVE")
        self.assertEqual(acc.auth_token, "old_t")
        self.assertEqual(acc.ct0, "old_c")
        self.assertTrue(TwitterEngineAccount.objects.filter(pk=acc.pk).exists())
        mock_get.assert_called_once()

    def test_x_http_429_rate_limited_keeps_previous_state(self):
        acc = self._x_acc(username="x_http_429", status="ACTIVE")
        resp = mock.MagicMock(status_code=429, text="Rate limit exceeded")
        with mock.patch("apps.billing.manage_views.requests.get", return_value=resp) as mock_get:
            out = self._post_x_session(acc.pk, "new_auth_token_40_chars_long_12345678", "new_ct0_32_chars_long_1234567890")
        self.assertFalse(out["success"])
        self.assertFalse(out["deleted"])
        acc.refresh_from_db()
        self.assertEqual(acc.status, "ACTIVE")
        self.assertEqual(acc.auth_token, "old_t")
        self.assertEqual(acc.ct0, "old_c")
        self.assertTrue(TwitterEngineAccount.objects.filter(pk=acc.pk).exists())
        mock_get.assert_called_once()

    def test_x_http_timeout_network_error_keeps_previous_state(self):
        acc = self._x_acc(username="x_http_timeout", status="ACTIVE")
        with mock.patch("apps.billing.manage_views.requests.get", side_effect=Timeout("Read timed out")) as mock_get:
            out = self._post_x_session(acc.pk, "new_auth_token_40_chars_long_12345678", "new_ct0_32_chars_long_1234567890")
        self.assertFalse(out["success"])
        self.assertFalse(out["deleted"])
        acc.refresh_from_db()
        self.assertEqual(acc.status, "ACTIVE")
        self.assertEqual(acc.auth_token, "old_t")
        self.assertEqual(acc.ct0, "old_c")
        self.assertTrue(TwitterEngineAccount.objects.filter(pk=acc.pk).exists())
        mock_get.assert_called_once()

    def test_x_http_ssl_error_network_error_keeps_previous_state(self):
        acc = self._x_acc(username="x_http_ssl", status="ACTIVE")
        with mock.patch("apps.billing.manage_views.requests.get", side_effect=SSLError("SSL verification failed")) as mock_get:
            out = self._post_x_session(acc.pk, "new_auth_token_40_chars_long_12345678", "new_ct0_32_chars_long_1234567890")
        self.assertFalse(out["success"])
        self.assertFalse(out["deleted"])
        acc.refresh_from_db()
        self.assertEqual(acc.status, "ACTIVE")
        self.assertEqual(acc.auth_token, "old_t")
        self.assertEqual(acc.ct0, "old_c")
        self.assertTrue(TwitterEngineAccount.objects.filter(pk=acc.pk).exists())
        mock_get.assert_called_once()

    def test_x_missing_ct0_with_auth_token_makes_request_with_matching_ct0_and_header(self):
        import re
        acc = self._x_acc(username="x_gen_ct0", status="CHALLENGED", auth_token="old_t", ct0="")
        resp = mock.MagicMock(status_code=200, text='{"screen_name": "x_gen_ct0"}')
        resp.json.return_value = {"screen_name": "x_gen_ct0"}
        with mock.patch("apps.billing.manage_views.requests.get", return_value=resp) as mock_get:
            out = self._post_x_session(acc.pk, "new_auth_token_40_chars_long_12345678", "")
        self.assertTrue(out["success"])
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args.kwargs if hasattr(mock_get.call_args, "kwargs") else mock_get.call_args[1]
        headers = call_kwargs.get("headers", {})
        cookie_header = headers.get("Cookie", "")
        csrf_header = headers.get("x-csrf-token") or headers.get("X-Csrf-Token")

        m = re.search(r"ct0=([^;]+)", cookie_header)
        self.assertIsNotNone(m, "Cookie ct0 not found in request Cookie header")
        cookie_ct0 = m.group(1).strip()
        self.assertEqual(cookie_ct0, csrf_header)
        self.assertEqual(csrf_header, "a" * 32)

    def test_x_missing_auth_token_makes_no_http_request(self):
        acc = self._x_acc(username="x_no_auth", status="ACTIVE", auth_token="old_t", ct0="old_c")
        with mock.patch("apps.billing.manage_views.requests.get") as mock_get:
            out = self._post_x_session(acc.pk, "", "some_ct0")
            mock_get.assert_not_called()
        self.assertFalse(out["success"])
        acc.refresh_from_db()
        self.assertEqual(acc.auth_token, "old_t")

        # Also direct call to _test_twitter_account_connection with empty auth_token
        acc.auth_token = ""
        with mock.patch("apps.billing.manage_views.requests.get") as mock_get:
            ok, status, _ = _test_twitter_account_connection(acc)
            mock_get.assert_not_called()
        self.assertFalse(ok)
        self.assertEqual(status, "CHALLENGED")

    def test_x_generated_ct0_saved_only_when_valid(self):
        # 1. VALID (200) -> generated ct0 is saved to DB
        acc_valid = self._x_acc(username="x_val_ct0", status="CHALLENGED", auth_token="old_t", ct0="")
        resp_200 = mock.MagicMock(status_code=200, text='{"screen_name": "x_val_ct0"}')
        resp_200.json.return_value = {"screen_name": "x_val_ct0"}
        with mock.patch("apps.billing.manage_views.requests.get", return_value=resp_200):
            out = self._post_x_session(acc_valid.pk, "new_auth", "")
        self.assertTrue(out["success"])
        acc_valid.refresh_from_db()
        self.assertEqual(acc_valid.ct0, "a" * 32)
        self.assertEqual(acc_valid.status, "ACTIVE")

        # 2. INVALID (401) -> generated ct0 is NOT saved to DB
        acc_invalid = self._x_acc(username="x_inval_ct0", status="CHALLENGED", auth_token="old_t", ct0="")
        resp_401 = mock.MagicMock(status_code=401, text='{"errors":[{"code":32}]}')
        with mock.patch("apps.billing.manage_views.requests.get", return_value=resp_401):
            out = self._post_x_session(acc_invalid.pk, "new_auth", "")
        self.assertFalse(out["success"])
        acc_invalid.refresh_from_db()
        self.assertEqual(acc_invalid.ct0, "")
        self.assertEqual(acc_invalid.status, "CHALLENGED")

        # 3. FORBIDDEN (403) -> generated ct0 is NOT saved to DB
        acc_403 = self._x_acc(username="x_403_ct0", status="ACTIVE", auth_token="old_t", ct0="")
        resp_403 = mock.MagicMock(status_code=403, text='{"errors":[{"code":200}]}')
        with mock.patch("apps.billing.manage_views.requests.get", return_value=resp_403):
            out = self._post_x_session(acc_403.pk, "new_auth", "")
        self.assertFalse(out["success"])
        acc_403.refresh_from_db()
        self.assertEqual(acc_403.ct0, "")
        self.assertEqual(acc_403.status, "ACTIVE")

    def test_x_response_sets_new_ct0_cookie_is_used(self):
        acc = self._x_acc(username="x_cookie_renew", status="CHALLENGED", auth_token="tok_123", ct0="")
        resp = mock.MagicMock(status_code=200, text='{"screen_name": "x_cookie_renew"}')
        resp.json.return_value = {"screen_name": "x_cookie_renew"}
        resp.cookies = {"ct0": "fresh_server_ct0_cookie_value"}
        with mock.patch("apps.billing.manage_views.requests.get", return_value=resp):
            out = self._post_x_session(acc.pk, "new_auth_token", "")
        self.assertTrue(out["success"])
        acc.refresh_from_db()
        self.assertEqual(acc.ct0, "fresh_server_ct0_cookie_value")

    def test_x_403_with_generated_ct0_stays_forbidden_unknown_and_logs(self):
        acc = self._x_acc(username="x_403_log", status="ACTIVE", auth_token="old_t", ct0="")
        resp = mock.MagicMock(status_code=403, text='{"errors":[{"code":200,"message":"Forbidden"}]}')
        with self.assertLogs("management", level="WARNING") as cm:
            with mock.patch("apps.billing.manage_views.requests.get", return_value=resp):
                out = self._post_x_session(acc.pk, "new_auth", "")
        self.assertFalse(out["success"])
        self.assertTrue(out.get("inconclusive", False))
        acc.refresh_from_db()
        self.assertEqual(acc.status, "ACTIVE")
        self.assertEqual(acc.ct0, "")
        self.assertTrue(any("generated ct0" in log for log in cm.output))

    def test_none_of_the_statuses_deletes_account(self):
        scenarios = [
            ("200_ok", mock.MagicMock(status_code=200, text='{"screen_name":"u"}', json=mock.MagicMock(return_value={"screen_name":"u"}))),
            ("401_invalid", mock.MagicMock(status_code=401, text='{"errors":[{"code":32}]}')),
            ("403_forbidden", mock.MagicMock(status_code=403, text='{"errors":[{"code":200}]}')),
            ("404_endpoint", mock.MagicMock(status_code=404, text='Not Found')),
            ("429_ratelimit", mock.MagicMock(status_code=429, text='Rate limited')),
            ("500_server", mock.MagicMock(status_code=500, text='Internal Server Error')),
            ("timeout_exc", Timeout("Timeout error")),
            ("ssl_exc", SSLError("SSL error")),
        ]

        for uname, outcome in scenarios:
            acc = self._x_acc(username=uname, status="ACTIVE")
            if isinstance(outcome, Exception):
                with mock.patch("apps.billing.manage_views.requests.get", side_effect=outcome):
                    out = self._post_x_session(acc.pk, "auth_x", "ct0_x")
            else:
                with mock.patch("apps.billing.manage_views.requests.get", return_value=outcome):
                    out = self._post_x_session(acc.pk, "auth_x", "ct0_x")
            self.assertFalse(out.get("deleted", False))
            self.assertTrue(
                TwitterEngineAccount.objects.filter(pk=acc.pk).exists(),
                f"Account was deleted under outcome {uname}!",
            )

    def test_twitter_cli_success_marks_active(self):
        acc = self._x_acc(username="cli_ok", status="CHALLENGED")
        completed = mock.MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
        with mock.patch("shutil.which", return_value="/usr/local/bin/twitter"), \
             mock.patch("subprocess.run", return_value=completed):
            ok, st, msg = _test_twitter_account_connection(acc)
        self.assertTrue(ok)
        self.assertEqual(st, "ACTIVE")
        acc.refresh_from_db()
        self.assertEqual(acc.status, "ACTIVE")

    def test_twitter_cli_auth_failed_marks_challenged(self):
        acc = self._x_acc(username="cli_fail", status="ACTIVE")
        completed = mock.MagicMock(returncode=1, stdout='{"ok": false, "error": {"code": "not_authenticated"}}', stderr="")
        with mock.patch("shutil.which", return_value="/usr/local/bin/twitter"), \
             mock.patch("subprocess.run", return_value=completed):
            ok, st, msg = _test_twitter_account_connection(acc)
        self.assertFalse(ok)
        self.assertEqual(st, "CHALLENGED")
        acc.refresh_from_db()
        self.assertEqual(acc.status, "CHALLENGED")

    def test_twitter_cli_rate_limited_marks_cooldown(self):
        acc = self._x_acc(username="cli_rate", status="ACTIVE")
        completed = mock.MagicMock(returncode=1, stdout='{"ok": false, "error": {"code": "rate_limited"}}', stderr="")
        with mock.patch("shutil.which", return_value="/usr/local/bin/twitter"), \
             mock.patch("subprocess.run", return_value=completed):
            ok, st, msg = _test_twitter_account_connection(acc)
        self.assertTrue(ok)
        self.assertEqual(st, "COOLDOWN")
        acc.refresh_from_db()
        self.assertEqual(acc.status, "COOLDOWN")

    # -------------------------------------------------------------------------
    # Unit Tests for Interpretation & Constants
    # -------------------------------------------------------------------------

    def test_endpoint_constant(self):
        self.assertEqual(TWITTER_SETTINGS_ENDPOINT, "https://x.com/i/api/1.1/account/settings.json")

    def test_interpret_twitter_response_unit(self):
        # 200 -> VALID
        res, msg, u = _interpret_twitter_response(status_code=200, response_data={"screen_name": "bob"})
        self.assertEqual(res, XSessionValidationResult.VALID)
        self.assertEqual(u, "bob")

        # 401 -> INVALID
        res, msg, _ = _interpret_twitter_response(status_code=401)
        self.assertEqual(res, XSessionValidationResult.INVALID)

        # 403 -> FORBIDDEN_UNKNOWN
        res, msg, _ = _interpret_twitter_response(status_code=403, response_text="Forbidden snippet")
        self.assertEqual(res, XSessionValidationResult.FORBIDDEN_UNKNOWN)

        # 404 -> ENDPOINT_ERROR
        res, msg, _ = _interpret_twitter_response(status_code=404)
        self.assertEqual(res, XSessionValidationResult.ENDPOINT_ERROR)

        # 429 -> RATE_LIMITED
        res, msg, _ = _interpret_twitter_response(status_code=429)
        self.assertEqual(res, XSessionValidationResult.RATE_LIMITED)

        # Timeout -> NETWORK_ERROR
        res, msg, _ = _interpret_twitter_response(error=Timeout("timed out"))
        self.assertEqual(res, XSessionValidationResult.NETWORK_ERROR)

        # SSLError -> NETWORK_ERROR
        res, msg, _ = _interpret_twitter_response(error=SSLError("bad cert"))
        self.assertEqual(res, XSessionValidationResult.NETWORK_ERROR)

        # 500 -> ENDPOINT_ERROR
        res, msg, _ = _interpret_twitter_response(status_code=500)
        self.assertEqual(res, XSessionValidationResult.ENDPOINT_ERROR)

    def test_get_or_generate_ct0_unit(self):
        ct0, was_generated = _get_or_generate_ct0("")
        self.assertEqual(ct0, "a" * 32)
        self.assertTrue(was_generated)

        ct0, was_generated = _get_or_generate_ct0(None)
        self.assertEqual(ct0, "a" * 32)
        self.assertTrue(was_generated)

        ct0, was_generated = _get_or_generate_ct0("  ")
        self.assertEqual(ct0, "a" * 32)
        self.assertTrue(was_generated)

        ct0, was_generated = _get_or_generate_ct0("my_real_ct0_token")
        self.assertEqual(ct0, "my_real_ct0_token")
        self.assertFalse(was_generated)

    def test_interpret_twitter_response_was_generated_403(self):
        with self.assertLogs("management", level="WARNING") as cm:
            res, msg, _ = _interpret_twitter_response(status_code=403, was_generated=True, username="alice")
        self.assertEqual(res, XSessionValidationResult.FORBIDDEN_UNKNOWN)
        self.assertTrue(any("generated ct0" in log for log in cm.output))

    def test_ig_raw_cookie_string_cleaned(self):
        acc = self._ig()
        fake = mock.MagicMock()
        fake.get_settings.return_value = {"cookies": {"sessionid": "clean_val"}}
        with mock.patch("instagrapi.Client", return_value=fake):
            res = self.client.post(
                f"/manage/accounts/{acc.pk}/session/",
                {"sessionid": "sessionid=clean_val; ds_user_id=12345;"},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest"
            ).json()
            self.assertTrue(res["success"])
            fake.login_by_sessionid.assert_called_with("clean_val")

    def test_device_info_property(self):
        acc = InstagramEngineAccount(username="test_dev", device_settings={
            "device_name": "Samsung Galaxy S24 Ultra",
            "device_settings": {
                "manufacturer": "Samsung",
                "model": "SM-S928B",
                "device": "e3q",
                "android_release": "14",
                "resolution": "1440x3120"
            }
        })
        info = acc.device_info
        self.assertEqual(info["name"], "Samsung Galaxy S24 Ultra")
        self.assertEqual(info["model"], "SM-S928B")
        self.assertEqual(info["android_release"], "14")
        self.assertIn("1440x3120", info["resolution"])

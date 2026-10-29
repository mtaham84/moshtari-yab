from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount


class LoginRequired(Exception):
    pass


class ClientConnectionError(Exception):
    pass


class SessionReplacementTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("boss", "b@x.ir", "pw", is_staff=True, is_superuser=True)
        self.client.force_login(user)

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
        acc = TwitterEngineAccount.objects.create(username="x_a", auth_token="old", ct0="c")
        out = self._x_post(acc, "سشن نامعتبر (HTTP 401)", False)
        self.assertFalse(out["success"])
        self.assertFalse(out["deleted"])
        self.assertTrue(TwitterEngineAccount.objects.filter(pk=acc.pk).exists())
        acc.refresh_from_db()
        self.assertEqual(acc.status, "CHALLENGED")

    def test_x_valid_session_is_kept(self):
        acc = TwitterEngineAccount.objects.create(username="x_b", auth_token="old", ct0="c")
        out = self._x_post(acc, "متصل و فعال", True)
        self.assertTrue(out["success"])
        acc.refresh_from_db()
        self.assertEqual(acc.auth_token, "t" * 40)

import os
import json
from unittest.mock import MagicMock, patch

import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ.setdefault("DJANGO_DEBUG", "True")
django.setup()

from django.test import RequestFactory, SimpleTestCase

from apps.billing.manage_views import _ping_and_evaluate_proxy_config, manage_proxy_test


class ProxyLiveTestUnitTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.user = MagicMock()
        self.user.is_authenticated = True
        self.user.is_staff = True
        self.user.is_superuser = True

    def test_ping_and_evaluate_active(self):
        proxy = MagicMock()
        proxy.id = 1
        proxy.tag = "Test Proxy"
        proxy.server = "127.0.0.1"
        proxy.port = 1081
        proxy.local_port = 1081
        proxy.protocol = "vless"
        proxy.raw_url = "vless://test"
        proxy.save = MagicMock()

        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            mock_sock_cls.return_value = mock_sock

            res = _ping_and_evaluate_proxy_config(proxy)
            self.assertEqual(res["status"], "ACTIVE")
            self.assertEqual(res["status_label"], "فعال")
            self.assertGreaterEqual(res["ping_ms"], 0)
            self.assertEqual(proxy.status, "ACTIVE")
            proxy.save.assert_called_once()

    def test_ping_and_evaluate_dead(self):
        proxy = MagicMock()
        proxy.id = 2
        proxy.tag = "Dead Proxy"
        proxy.server = "192.0.2.1"
        proxy.port = 443
        proxy.local_port = None
        proxy.protocol = "trojan"
        proxy.raw_url = ""
        proxy.save = MagicMock()

        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 111  # Connection refused
            mock_sock_cls.return_value = mock_sock

            res = _ping_and_evaluate_proxy_config(proxy)
            self.assertEqual(res["status"], "DEAD")
            self.assertEqual(res["status_label"], "قطع")
            self.assertEqual(res["ping_ms"], -1.0)
            self.assertEqual(proxy.status, "DEAD")
            proxy.save.assert_called_once()

    @patch("apps.discovery.models.ProxyConfig.objects")
    def test_manage_proxy_test_single(self, mock_qs):
        proxy = MagicMock()
        proxy.id = 10
        proxy.tag = "Proxy 10"
        proxy.server = "1.2.3.4"
        proxy.port = 443
        proxy.local_port = None
        proxy.protocol = "ss"
        proxy.raw_url = ""
        proxy.save = MagicMock()

        mock_qs.get.return_value = proxy

        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            mock_sock.connect_ex.return_value = 0
            mock_sock_cls.return_value = mock_sock

            req = self.rf.post("/manage/proxies/10/test/")
            req.user = self.user
            resp = manage_proxy_test(req, pk=10)
            data = json.loads(resp.content)

            self.assertTrue(data["success"])
            self.assertEqual(data["proxy"]["id"], 10)
            self.assertEqual(data["proxy"]["status"], "ACTIVE")

    @patch("apps.discovery.models.ProxyConfig.objects")
    def test_manage_proxy_test_all(self, mock_qs):
        p1 = MagicMock(id=1, tag="P1", server="1.1.1.1", port=443, local_port=None, protocol="vless", raw_url="", save=MagicMock())
        p2 = MagicMock(id=2, tag="P2", server="2.2.2.2", port=443, local_port=None, protocol="vmess", raw_url="", save=MagicMock())

        mock_qs.all.return_value.order_by.return_value = [p1, p2]

        with patch("socket.socket") as mock_sock_cls:
            mock_sock = MagicMock()
            # P1 succeeds, P2 fails
            mock_sock.connect_ex.side_effect = [0, 111]
            mock_sock_cls.return_value = mock_sock

            req = self.rf.post("/manage/proxies/test/", {"all": "1"})
            req.user = self.user
            resp = manage_proxy_test(req)
            data = json.loads(resp.content)

            self.assertTrue(data["success"])
            self.assertEqual(data["total"], 2)
            self.assertEqual(data["active_count"], 1)
            self.assertEqual(data["dead_count"], 1)

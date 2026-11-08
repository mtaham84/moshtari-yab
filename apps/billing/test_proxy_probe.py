"""Real-network tests for xray.multi_proxy_manager.probe_socks5_http (no mocks).

A local HTTP server plays the proxy: requests sends it the absolute probe URL and it answers like the probe target
would (or like a filtering box would).
"""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.test import SimpleTestCase

from xray.multi_proxy_manager import probe_socks5_http

PROBE_URL = "http://api.telegram.org/"


class _FakeTarget(BaseHTTPRequestHandler):
    status = 204
    location = ""

    def do_GET(self):
        self.send_response(self.status)
        if self.location:
            self.send_header("Location", self.location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class ProbeSocks5HttpTests(SimpleTestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeTarget)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.proxy_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def _probe(self, status: int, location: str = "") -> tuple[bool, float, str]:
        _FakeTarget.status, _FakeTarget.location = status, location
        return probe_socks5_http(self.proxy_url, probe_url=PROBE_URL, timeout_sec=2.0)

    def test_204_is_alive(self):
        ok, ping_ms, err = self._probe(204)
        self.assertTrue(ok, err)
        self.assertGreater(ping_ms, 0)

    def test_redirect_within_probe_domain_is_alive(self):
        # api.telegram.org answers 302 -> https://core.telegram.org/bots
        ok, _, err = self._probe(302, "https://core.telegram.org/bots")
        self.assertTrue(ok, err)

    def test_redirect_to_other_host_is_dead(self):
        # Iranian filtering redirects to a block page on another host
        ok, ping_ms, _ = self._probe(302, "http://10.10.34.36/?type=Invalid")
        self.assertFalse(ok)
        self.assertEqual(ping_ms, -1.0)

    def test_redirect_to_lookalike_domain_is_dead(self):
        ok, _, _ = self._probe(302, "https://eviltelegram.org/")
        self.assertFalse(ok)

    def test_forbidden_is_dead(self):
        ok, _, _ = self._probe(403)
        self.assertFalse(ok)

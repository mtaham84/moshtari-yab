"""State one scraper process keeps between calls: output streams, warm clients, counters."""
import sys

from django.core.management.base import OutputWrapper
from django.core.management.color import color_style

from apps.discovery.ig_session import Policy

PROXY_DOWN_REST_HOURS = 0.5  # account rest when its proxy cannot be reached (never scrape without it)


class Run:
    """Passed to every ig_scraper function. Defaults write to stdout/stderr like a management command."""

    def __init__(self, stdout=None, stderr=None, style=None):
        self.stdout = stdout or OutputWrapper(sys.stdout)
        self.stderr = stderr or OutputWrapper(sys.stderr)
        self.style = style or color_style()
        self.client_pool = {}       # account id -> warm instagrapi client
        self.renew_attempts = {}    # account id -> automatic re-logins in the current incident
        self.api_requests = 0       # Instagram requests made for the current source
        self.page_search_error = ""
        self.account_offset = 0     # rotates which scraper account starts each cycle
        self._policy = None

    @property
    def policy(self) -> Policy:
        if self._policy is None:
            self._policy = Policy.from_env()
        return self._policy

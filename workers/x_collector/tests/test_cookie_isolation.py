import json
import os
import sys
from types import SimpleNamespace

import pytest

from workers.x_collector.account_pool import TwitterAccount
from workers.x_collector.client import XCliClient
from workers.x_collector.tests.test_search_errors import install_client


@pytest.mark.parametrize('native', [True, False])
def test_two_accounts_do_not_change_process_environment(monkeypatch, native):
    install_client(monkeypatch, iter([[], []]))
    module = sys.modules['twitter_cli.client']
    sessions = []
    class Session:
        def __init__(self, **kwargs):
            self.proxies = kwargs['proxies']
            self.closed = False
            sessions.append(self)
        def close(self):
            self.closed = True
    module._cffi_requests = SimpleNamespace(Session=Session)
    module._best_chrome_target = lambda: 'chrome'
    module.sync_chrome_version = lambda _: None
    previous = object()
    module._cffi_session = previous
    cookies = []
    if native:
        original = module.TwitterClient
        class Native(original):
            def __init__(self, auth_token, ct0):
                cookies.append((auth_token, ct0))
        module.TwitterClient = Native
    else:
        monkeypatch.setitem(sys.modules, 'twitter_cli.client', None)
        monkeypatch.setattr('workers.x_collector.client.shutil.which', lambda _: '/bin/twitter')
        def run(args, **kwargs):
            cookies.append((kwargs['env']['TWITTER_AUTH_TOKEN'], kwargs['env']['TWITTER_CT0']))
            return SimpleNamespace(returncode=0, stdout=json.dumps({'ok': True, 'data': []}), stderr='')
        monkeypatch.setattr('workers.x_collector.client.subprocess.run', run)
    before = dict(os.environ)
    for i in (1, 2):
        account = TwitterAccount(i, str(i), '', f'token-{i}', f'cookie-{i}', f'http://u:p{i}@proxy:8080', 'ACTIVE')
        XCliClient().search_top_past_week('لپتاپ', account=account)
        assert dict(os.environ) == before
    assert cookies == [('token-1', 'cookie-1'), ('token-2', 'cookie-2')]
    if native:
        assert module._cffi_session is previous
        assert [s.proxies for s in sessions] == [
            {'https': 'http://u:p1@proxy:8080', 'http': 'http://u:p1@proxy:8080'},
            {'https': 'http://u:p2@proxy:8080', 'http': 'http://u:p2@proxy:8080'},
        ]
        assert all(s.closed for s in sessions)

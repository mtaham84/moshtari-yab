from types import SimpleNamespace

from workers.x_collector.native_session import native_session


def test_transaction_bootstrap_has_more_time_without_changing_other_requests():
    calls = []
    class Session:
        def __init__(self, **kwargs):
            pass
        def get(self, url, **kwargs):
            calls.append(kwargs['timeout'])
        def close(self):
            pass
    module = SimpleNamespace(_cffi_session=None, _cffi_requests=SimpleNamespace(Session=Session),
                             _best_chrome_target=lambda: 'chrome', sync_chrome_version=lambda _: None)
    with native_session(module, None):
        module._cffi_session.get('https://x.com', timeout=10)
        module._cffi_session.get('https://x.com/api', timeout=30)
    assert calls == [30, 30]
    assert module._cffi_session is None

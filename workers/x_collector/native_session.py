"""Scope twitter-cli's shared transport to one account without changing process environment."""
from contextlib import contextmanager
from threading import RLock

_session_lock = RLock()


@contextmanager
def native_session(module, proxy):
    # twitter-cli uses a module-global transport, even when cookies are constructor arguments.
    with _session_lock:
        previous = getattr(module, '_cffi_session', None)
        session = None
        try:
            if hasattr(module, '_cffi_requests'):
                target = module._best_chrome_target()
                module.sync_chrome_version(target)
                session = module._cffi_requests.Session(
                    impersonate=target, proxies={'https': proxy, 'http': proxy} if proxy else None,
                )
            module._cffi_session = session
            yield
        finally:
            module._cffi_session = previous
            if session is not None:
                session.close()

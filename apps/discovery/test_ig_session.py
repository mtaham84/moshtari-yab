"""Pure tests for the Instagram session lifecycle helpers (no network, no DB)."""
import json
import os
import stat

from apps.discovery import ig_session as s
from apps.discovery.ig_session import ErrorKind, Policy


def _policy(tmp_path, **kw):
    base = dict(session_dir=tmp_path, auto_reauth=False, max_attempts=1, recovery_cooldown_s=60,
                net_retries=3, net_initial_s=2, net_max_s=10)
    return Policy(**{**base, **kw})


class LoginRequired(Exception): pass
class ChallengeRequired(Exception): pass
class TwoFactorRequired(Exception): pass
class FeedbackRequired(Exception): pass
class PleaseWaitFewMinutes(Exception): pass
class BadPassword(Exception): pass
class ClientConnectionError(Exception): pass


def test_classification():
    assert s.classify(LoginRequired("login_required")) is ErrorKind.LOGIN_REQUIRED
    assert s.classify(ChallengeRequired("x")) is ErrorKind.CHALLENGE
    assert s.classify(TwoFactorRequired("x")) is ErrorKind.CHALLENGE
    assert s.classify(FeedbackRequired("x")) is ErrorKind.RATE_LIMIT
    assert s.classify(PleaseWaitFewMinutes("x")) is ErrorKind.RATE_LIMIT
    assert s.classify("429 Too Many Requests") is ErrorKind.RATE_LIMIT
    assert s.classify(BadPassword("x")) is ErrorKind.BAD_CREDENTIALS
    assert s.classify(ClientConnectionError("timed out")) is ErrorKind.NETWORK
    assert s.classify(ValueError("boom")) is ErrorKind.OTHER
    # a challenge that mentions login_required is still a challenge
    assert s.classify("challenge_required ... login_required") is ErrorKind.CHALLENGE


def test_recovery_only_for_rejected_session_and_bounded(tmp_path):
    off, on = _policy(tmp_path), _policy(tmp_path, auto_reauth=True)
    assert not off.may_recover(ErrorKind.LOGIN_REQUIRED, 0)
    assert on.may_recover(ErrorKind.LOGIN_REQUIRED, 0)
    assert not on.may_recover(ErrorKind.LOGIN_REQUIRED, 1)
    for kind in (ErrorKind.CHALLENGE, ErrorKind.RATE_LIMIT, ErrorKind.NETWORK, ErrorKind.BAD_CREDENTIALS, ErrorKind.OTHER):
        assert not on.may_recover(kind, 0)


def test_backoff_is_bounded(tmp_path):
    p = _policy(tmp_path)
    assert all(0 < p.backoff(a) <= p.net_max_s for a in range(12))


def test_atomic_save_permissions_and_isolation(tmp_path):
    p = _policy(tmp_path)
    a, b = s.session_path(1, p), s.session_path(2, p)
    assert a != b and a.parent == tmp_path
    assert s.save_atomic(a, {"cookies": {"sessionid": "AAA"}})
    assert s.save_atomic(b, {"cookies": {"sessionid": "BBB"}})
    assert stat.S_IMODE(os.stat(a).st_mode) == 0o600
    assert s.load_file(a)[0]["cookies"]["sessionid"] == "AAA"
    assert s.load_file(b)[0]["cookies"]["sessionid"] == "BBB"
    assert not list(tmp_path.glob("*.tmp"))


def test_failed_write_keeps_last_good_file(tmp_path, monkeypatch):
    path = s.session_path(1, _policy(tmp_path))
    s.save_atomic(path, {"v": 1})
    monkeypatch.setattr(s.json, "dump", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    assert s.save_atomic(path, {"v": 2}) is False
    monkeypatch.undo()
    assert s.load_file(path)[0] == {"v": 1}
    assert not list(tmp_path.glob("*.tmp"))


def test_missing_and_corrupt_files_are_distinguished_and_untouched(tmp_path):
    path = s.session_path(3, _policy(tmp_path))
    assert s.load_file(path) == (None, "missing")
    path.write_text("{not json")
    assert s.load_file(path) == (None, "corrupt")
    assert path.read_text() == "{not json"


def test_account_lock_is_exclusive_per_account(tmp_path):
    p = _policy(tmp_path)
    with s.account_lock(1, p) as first:
        assert first
        with s.account_lock(1, p) as second:
            assert not second               # same account: refused
        with s.account_lock(2, p) as other:
            assert other                    # another account is not blocked
    with s.account_lock(1, p) as again:
        assert again                        # released after exit


def test_redact_hides_secrets():
    out = s.redact('sessionid=12345%3AabcDEF password: hunter2 "csrftoken": "ZZTOP9"')
    assert "abcDEF" not in out and "hunter2" not in out and "ZZTOP9" not in out


def test_compose_mounts_persistent_volume_for_ig():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text(encoding="utf-8")
    ig = text[text.index("\n  ig:"):text.index("\n  retention:")]
    assert "appdata:/app/data" in ig
    assert "INSTAGRAM_SESSION_DIR" in text or "env_file" in text

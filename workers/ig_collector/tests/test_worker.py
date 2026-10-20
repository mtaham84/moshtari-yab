from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest

from workers.ig_collector.backends.base import (
    AuthFailed, BackendCapabilities, Fatal, InstagramBackend, InstagramComment,
    InstagramPost, RateLimited, Transient,
)
from workers.ig_collector.backends.mock import MockInstagramBackend
from workers.ig_collector.cli import main
from workers.ig_collector.lexicon import PersianIntentLexicon, normalize_fa
from workers.ig_collector.worker import (
    InstagramCollector, SeenStore, is_persian_caption, normalize_timestamp,
    validate_record, validate_seeds,
)

FIXTURE = Path(__file__).parent / "fixtures" / "instagram.json"
SEEDS = [{"kind": "account", "value": "demo_demand", "limit": 12}]
COMMENTS = {"mode": "signals", "max_posts_per_run": 5, "max_per_post": 50}


def collector(tmp_path, backend=None, **kwargs):
    return InstagramCollector(
        backend or MockInstagramBackend(FIXTURE), output_dir=tmp_path / "out",
        state_path=tmp_path / "state.sqlite3", sleep_min=0, sleep_max=0,
        sleeper=lambda _: None, jitter=lambda low, high: 0, **kwargs,
    )


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("text, expected", [
    ("دنبال آسیاب قهوه هستم", 3),
    ("کسی آسیاب خوب می شناسه؟", 3),
    ("از کجا کفش بخرم؟", 3),
    ("پیشنهاد بدید برای لپ‌تاپ", 2),
    ("قیمت این چنده؟", 1),
    ("جهت سفارش دایرکت بزنید؛ موجود است", 0),
    ("قرعه‌کشی! کامنت بذار و جایزه ببر", 0),
    ("دایرکت بده", 0),
    ("١٢٣", 0),
])
def test_persian_intent_lexicon_table(text, expected):
    result = PersianIntentLexicon.load().classify(text)
    assert result.score == expected


def test_lexicon_normalizes_arabic_letters_digits_and_zwnj():
    assert normalize_fa("ميخواهم كالا ۱۲۳") == "می خواهم کالا 123"
    assert PersianIntentLexicon.load().classify("دنبال آسیاب قهوه هستم").score == 3


@pytest.mark.parametrize("text, expected", [
    ("این کالا را می‌خواهم", True), ("I need a product", False),
    ("man donbale kharid hastam", False), ("قیمت coffee grinder چنده", False),
    ("", False),
])
def test_only_persian_led_captions_pass(text, expected):
    assert is_persian_caption(text) is expected


@pytest.mark.parametrize("value, expected", [
    (1790848800, "2026-10-01T10:00:00+00:00"),
    (1790848800000, "2026-10-01T10:00:00+00:00"),
    ("2026-10-01T13:30:00+03:30", "2026-10-01T10:00:00+00:00"),
    ("garbage", None), (None, None),
])
def test_timestamp_normalization(value, expected):
    assert normalize_timestamp(value, warn=False) == expected


def test_seed_validation_skips_invalid_and_defaults_comments_off(caplog):
    seeds, comments = validate_seeds({"accounts": [{"username": "buyer", "limit": 2}, {}, None],
                                      "hashtags": [{"tag": "درخواست"}, {"tag": "bad", "limit": 0}]})
    assert seeds == [{"kind": "account", "value": "buyer", "limit": 2},
                     {"kind": "hashtag", "value": "درخواست", "limit": 20}]
    assert comments["mode"] == "off"
    assert "Skipping invalid" in caplog.text


def test_seller_post_policy_uses_all_comment_mode():
    _, comments = validate_seeds({"comments": {"mode": "signals", "policy": "seller_post"}})

    assert comments["mode"] == "all"
    assert comments["policy"] == "seller_post"


def test_run_selects_request_only_and_only_fetches_its_comments(tmp_path):
    backend = MockInstagramBackend(FIXTURE)
    result = collector(tmp_path, backend).run(SEEDS, COMMENTS)
    output = rows(Path(result["output"]))
    assert result["status"] == "COMPLETED"
    assert [row["id"] for row in output] == ["ig:IGReq100", "ig:IGReq100:cc-1", "ig:IGReq100:cc-2"]
    assert all(row["source"] == "instagram" and "آسیاب" in output[0]["text"] for row in output[:1])
    assert result["filtered_out"] >= 4  # giveaway, ad, English, Finglish


def test_mock_rerun_deduplicates_every_record(tmp_path):
    worker = collector(tmp_path)
    first = worker.run(SEEDS, COMMENTS)
    second = worker.run(SEEDS, COMMENTS)
    assert first["collected"] == 3
    assert second["collected"] == 0
    assert len(rows(Path(second["output"]))) == 3


def test_rerun_does_not_refetch_comments_after_parent_post_was_seen(tmp_path):
    class Counting(MockInstagramBackend):
        def __init__(self):
            super().__init__(FIXTURE)
            self.calls = 0

        def post_comments(self, post_ref, limit):
            self.calls += 1
            return super().post_comments(post_ref, limit)

    backend = Counting()
    worker = collector(tmp_path, backend)
    worker.run(SEEDS, COMMENTS)
    worker.run(SEEDS, COMMENTS)
    assert backend.calls == 1


def test_daily_cap_is_hard_even_inside_a_batch(tmp_path):
    result = collector(tmp_path, daily_cap=2).run(SEEDS, COMMENTS)
    assert result["collected"] == 2
    assert result["status"] == "DAILY_CAP_REACHED"
    assert len(rows(Path(result["output"]))) == 2


def test_failed_state_commit_rolls_back_both_jsonl_files(tmp_path, monkeypatch):
    worker = collector(tmp_path)
    original = SeenStore.add_many

    def broken(_self, _records):
        raise OSError("simulated state commit failure")

    monkeypatch.setattr(SeenStore, "add_many", broken)
    result = worker.run(SEEDS, {"mode": "off"})
    assert result["status"] == "FAILED"
    assert not (worker.output_dir / "latest.jsonl").exists() or (worker.output_dir / "latest.jsonl").stat().st_size == 0
    dated = worker.output_dir / f"{datetime.now().date().isoformat()}.jsonl"
    assert not dated.exists() or dated.stat().st_size == 0
    monkeypatch.setattr(SeenStore, "add_many", original)


def test_failed_comment_output_commit_does_not_mark_comments_as_fetched(tmp_path, monkeypatch):
    worker = collector(tmp_path)
    original = SeenStore.add_many

    def broken(_self, _records):
        raise OSError("simulated state commit failure")

    monkeypatch.setattr(SeenStore, "add_many", broken)
    result = worker.run(SEEDS, COMMENTS)
    assert result["status"] == "FAILED"
    store = SeenStore(worker.state_path)
    assert not store.comments_were_fetched("IGReq100")
    monkeypatch.setattr(SeenStore, "add_many", original)


def test_persisted_cooldown_prevents_second_backend_call(tmp_path):
    class Limited(MockInstagramBackend):
        def __init__(self):
            super().__init__(FIXTURE)
            self.calls = 0

        def user_posts(self, username, limit, since):
            self.calls += 1
            raise RateLimited("429")

    backend = Limited()
    worker = collector(tmp_path, backend, cooldown_seconds=600)
    assert worker.run(SEEDS)["status"] == "RATE_LIMITED"
    assert worker.run(SEEDS)["status"] == "RATE_LIMITED"
    assert backend.calls == 1


def test_transient_retry_and_circuit_breaker(tmp_path):
    class Broken(MockInstagramBackend):
        def user_posts(self, username, limit, since):
            raise Transient("down")

    result = collector(tmp_path, Broken(FIXTURE), max_retries=0, circuit_breaker=2).run(SEEDS * 2)
    assert result["status"] == "CIRCUIT_OPEN"
    assert result["failures"] == 2


def test_fatal_failure_stops_collection_without_leaking_error(tmp_path, caplog):
    class Rejected(MockInstagramBackend):
        def user_posts(self, username, limit, since):
            raise Fatal("private-response-secret")

    result = collector(tmp_path, Rejected(FIXTURE), max_retries=0).run(SEEDS)
    assert result["status"] == "FAILED"
    assert "private-response-secret" not in caplog.text


def test_comments_all_includes_selected_zero_intent_posts(tmp_path):
    class Counting(MockInstagramBackend):
        def __init__(self):
            super().__init__(FIXTURE)
            self.comment_shortcodes = []

        def post_comments(self, post_ref, limit):
            self.comment_shortcodes.append(post_ref.shortcode)
            return super().post_comments(post_ref, limit)

    backend = Counting()
    result = collector(tmp_path, backend).run(SEEDS, {"mode": "all", "max_posts_per_run": 10, "max_per_post": 10})
    assert "IGReq100" in backend.comment_shortcodes
    assert "IGGive101" in backend.comment_shortcodes
    assert "IGAd102" in backend.comment_shortcodes
    assert "ig:IGReq100" in [row["id"] for row in rows(Path(result["output"]))]


def test_comments_signals_never_fetch_giveaway_or_ad(tmp_path):
    class Counting(MockInstagramBackend):
        def __init__(self):
            super().__init__(FIXTURE)
            self.comment_shortcodes = []

        def post_comments(self, post_ref, limit):
            self.comment_shortcodes.append(post_ref.shortcode)
            return super().post_comments(post_ref, limit)

    backend = Counting()
    collector(tmp_path, backend).run(SEEDS, COMMENTS)
    assert backend.comment_shortcodes == ["IGReq100"]


def test_comment_caps_apply_to_posts_and_per_post_items(tmp_path):
    class Counting(MockInstagramBackend):
        def __init__(self):
            super().__init__(FIXTURE)
            self.comment_calls = []

        def post_comments(self, post_ref, limit):
            self.comment_calls.append((post_ref.shortcode, limit))
            return super().post_comments(post_ref, limit)

    backend = Counting()
    result = collector(tmp_path, backend).run(SEEDS, {"mode": "all", "max_posts_per_run": 1, "max_per_post": 1})
    assert len(backend.comment_calls) == 1
    assert backend.comment_calls[0][1] == 1
    assert result["collected"] <= 2


def test_comments_all_fetches_low_signal_posts_but_does_not_write_them(tmp_path):
    class Counting(MockInstagramBackend):
        def __init__(self):
            super().__init__(FIXTURE)
            self.comment_shortcodes = []

        def post_comments(self, post_ref, limit):
            self.comment_shortcodes.append(post_ref.shortcode)
            return super().post_comments(post_ref, limit)

    backend = Counting()
    result = collector(tmp_path, backend).run(SEEDS, {"mode": "all", "max_posts_per_run": 10, "max_per_post": 10})
    output = rows(Path(result["output"]))
    assert backend.comment_shortcodes == ["IGReq100", "IGGive101", "IGAd102"]
    assert "ig:IGGive101" not in [row["id"] for row in output]
    assert "ig:IGAd102" not in [row["id"] for row in output]
    assert "ig:IGAd102:cc-5" in [row["id"] for row in output]


def test_dry_run_creates_no_output_or_state(tmp_path):
    worker = collector(tmp_path)
    result = worker.run(SEEDS, COMMENTS, dry_run=True)
    assert result["status"] == "DRY_RUN"
    assert not worker.output_dir.exists()
    assert not worker.state_path.exists()


def test_dry_run_cli_prints_plan_without_credentials(tmp_path, monkeypatch, capsys):
    from workers.ig_collector.cli import _main

    seed_path = tmp_path / "seeds.json"
    seed_path.write_text(json.dumps({"accounts": [{"username": "demo_demand", "limit": 3}]}), encoding="utf-8")
    monkeypatch.setenv("HIKERAPI_KEY", "never-print-this")
    assert _main(["--backend", "hikerapi", "--seeds", str(seed_path), "--dry-run"], project_root=tmp_path) == 0
    output = capsys.readouterr().out
    assert '"status": "DRY_RUN"' in output
    assert "never-print-this" not in output
    assert not (tmp_path / "output").exists()


def test_keep_rejected_stores_reasons_without_sensitive_content(tmp_path):
    result = collector(tmp_path).run(SEEDS, COMMENTS, keep_rejected=True)
    rejected = Path(result["output"]).with_name(f"{datetime.now().date().isoformat()}.rejected.jsonl")
    content = rejected.read_text(encoding="utf-8")
    assert "IGGive101" not in content and "promo_fa" not in content and "قرعه" not in content
    assert "intent_below_threshold" in content or "invalid_or_non_persian" in content


def test_mock_cli_isolated_and_repeatable(tmp_path, monkeypatch, capsys):
    from workers.ig_collector.cli import _main

    assert _main(["--mock", "--once"], project_root=tmp_path) == 0
    output_dir = tmp_path / "data" / "ig_collected_mock"
    state_path = tmp_path / "output" / "ig_collector_mock_state.sqlite3"
    assert output_dir.exists() and state_path.exists()
    assert not (tmp_path / "data" / "ig_collected").exists()
    result = json.loads(capsys.readouterr().out)
    assert result["collected"] == 3
    assert _main(["--mock", "--once"], project_root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out)["collected"] == 0


def test_no_write_methods_on_contract_and_backends():
    forbidden = {"follow", "like", "comment", "send_dm", "save", "direct_message"}
    classes = [InstagramBackend, MockInstagramBackend]
    from workers.ig_collector.backends.instaloader_backend import InstaloaderBackend
    from workers.ig_collector.backends.hikerapi_backend import HikerAPIBackend
    classes += [InstaloaderBackend, HikerAPIBackend]
    for cls in classes:
        assert forbidden.isdisjoint(set(dir(cls)))
        assert not any(any(word in method.casefold() for word in ("follow", "like", "send_dm", "direct_message"))
                       for method in dir(cls))


def test_instaloader_maps_fake_exception_types_without_importing_package():
    from workers.ig_collector.backends.instaloader_backend import InstaloaderBackend

    class TooManyRequestsException(Exception):
        pass

    class Exceptions:
        pass

    Exceptions.TooManyRequestsException = TooManyRequestsException
    backend = InstaloaderBackend(module=type("Module", (), {"exceptions": Exceptions}), loader=object())
    assert isinstance(backend._map_error(TooManyRequestsException()), RateLimited)


@pytest.mark.parametrize("name, expected", [
    ("LoginRequiredException", AuthFailed), ("ConnectionException", Transient),
    ("InvalidArgumentException", Fatal),
])
def test_instaloader_maps_fake_auth_transient_and_fatal_errors(name, expected):
    from workers.ig_collector.backends.instaloader_backend import InstaloaderBackend

    exception_type = type(name, (Exception,), {})
    exceptions = type("Exceptions", (), {name: exception_type})
    backend = InstaloaderBackend(module=type("Module", (), {"exceptions": exceptions}), loader=object())
    assert isinstance(backend._map_error(exception_type()), expected)


def test_instaloader_session_load_failure_does_not_expose_path(monkeypatch, caplog):
    import sys
    from types import SimpleNamespace
    from workers.ig_collector.backends.instaloader_backend import InstaloaderBackend

    secret_path = "C:/private/session-cookie-secret"

    class Loader:
        def __init__(self, **kwargs):
            self.context = object()

        def load_session_from_file(self, username, **kwargs):
            assert username == "session-cookie-secret"
            raise RuntimeError(secret_path)

    fake = SimpleNamespace(Instaloader=Loader, exceptions=SimpleNamespace())
    monkeypatch.setitem(sys.modules, "instaloader", fake)
    monkeypatch.setitem(sys.modules, "instaloader.exceptions", fake.exceptions)
    monkeypatch.setenv("IG_INSTALOADER_SESSION_FILE", secret_path)
    with pytest.raises(AuthFailed) as error:
        InstaloaderBackend()._ensure()
    assert secret_path not in str(error.value) + caplog.text


def test_hikerapi_hashtag_mapping_parses_documented_response_shape():
    from workers.ig_collector.backends.hikerapi_backend import HikerAPIBackend
    from urllib.parse import urlparse, parse_qs

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"response": {"sections": [{"layout_content": {"medias": [{"media": {
                "id": "990", "code": "HashtagPost990", "caption": {"text": "دنبال کتاب خوب هستم"},
                "user": {"username": "book_buyer", "pk": "991"}, "taken_at": 1790848800,
            }}]}}]}, "next_page_id": None}).encode()

    class Opener:
        def open(self, request, timeout):
            assert parse_qs(urlparse(request.full_url).query)["name"] == ["درخواست"]
            assert request.get_header("X-access-key") == "key-in-header"
            return Response()

    posts = HikerAPIBackend("key-in-header", opener=Opener()).hashtag_posts("#درخواست", 5)
    assert len(posts) == 1
    assert posts[0].shortcode == "HashtagPost990"
    assert posts[0].username == "book_buyer"
    assert posts[0].caption == "دنبال کتاب خوب هستم"


def test_hikerapi_http_mapping_and_key_never_logged_or_in_url(caplog):
    from workers.ig_collector.backends.hikerapi_backend import HikerAPIBackend
    from urllib.error import HTTPError
    from io import BytesIO

    class Opener:
        def open(self, request, timeout):
            assert "secret-key" not in request.full_url
            assert request.get_header("X-access-key") == "secret-key"
            raise HTTPError(request.full_url, 429, "rate", {}, BytesIO(b""))

    backend = HikerAPIBackend("secret-key", opener=Opener())
    with pytest.raises(RateLimited):
        backend._request("/a2/user", {"username": "buyer"})
    assert "secret-key" not in caplog.text


@pytest.mark.parametrize("status, error", [(401, AuthFailed), (429, RateLimited), (503, Transient), (400, Fatal)])
def test_hikerapi_maps_http_errors(status, error):
    from workers.ig_collector.backends.hikerapi_backend import HikerAPIBackend

    with pytest.raises(error):
        HikerAPIBackend._raise_http(status)


def test_hikerapi_maps_fake_json_response_without_network():
    from workers.ig_collector.backends.hikerapi_backend import HikerAPIBackend

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"response":{"sections":[]}}'

    class Opener:
        def open(self, request, timeout):
            assert timeout == 20
            return Response()

    waits = []
    backend = HikerAPIBackend("fake-key", opener=Opener(), sleep_min=0, sleep_max=0,
                              sleeper=waits.append, jitter=lambda low, high: 0)
    assert backend._request("/fixture", {}) == {"response": {"sections": []}}
    backend._request("/fixture", {})
    assert waits == [0]


def test_unsupported_capability_is_a_logged_skip(tmp_path, caplog):
    class Partial(MockInstagramBackend):
        capabilities = BackendCapabilities(user_posts=True, hashtag_posts=False, post_comments=False)

    result = collector(tmp_path, Partial(FIXTURE)).run([{"kind": "hashtag", "value": "ignored", "limit": 2}])
    assert result["status"] == "COMPLETED"
    assert "does not support hashtag_posts" in caplog.text


def test_secret_and_session_path_never_appear_in_status_or_logs(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("HIKERAPI_KEY", "very-secret-key")
    monkeypatch.setenv("IG_INSTALOADER_SESSION_FILE", "C:/private/session.secret")
    result = collector(tmp_path).run(SEEDS, COMMENTS)
    status = (Path(result["output"]).parent / "status.json").read_text(encoding="utf-8")
    assert "very-secret-key" not in status + caplog.text
    assert "session.secret" not in status + caplog.text


def test_status_output_path_is_relative_when_outside_project(tmp_path):
    worker = collector(tmp_path)
    result = worker.run(SEEDS, {"mode": "off"})
    status = (worker.output_dir / "status.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in status
    assert Path(result["output"]).name in status


def test_persian_text_round_trips_jsonl(tmp_path):
    result = collector(tmp_path).run(SEEDS, {"mode": "off"})
    first = rows(Path(result["output"]))[0]
    assert first["text"] == "کسی آسیاب قهوه خوب می‌شناسه؟ قیمت مناسب باشه لطفا"
    assert "\\u06a9" not in Path(result["output"]).read_text(encoding="utf-8")
    assert validate_record(first)


def test_error_types_and_capabilities_are_explicit():
    assert [RateLimited.kind, AuthFailed.kind, Transient.kind, Fatal.kind] == ["rate_limit", "auth", "transient", "fatal"]
    assert BackendCapabilities(user_posts=False, hashtag_posts=True, post_comments=False).hashtag_posts

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from workers.ig_collector.backends.base import (
    AuthFailed, BackendCapabilities, Fatal, InstagramBackend, InstagramComment, NotFound,
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


@pytest.fixture(autouse=True)
def collector_tests_do_not_write_database_heartbeats(monkeypatch):
    monkeypatch.setattr("workers.ig_collector.worker.beat_ok", lambda *args: None)
    monkeypatch.setattr("workers.ig_collector.worker.beat_error", lambda *args: None)


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


def test_seed_validation_keeps_hashtag_category_contract_and_account_shape():
    seeds, comments = validate_seeds({
        "accounts": [{"username": "seller", "limit": 3}],
        "hashtags": [{
            "tag": "کفش", "limit": 8, "window_days": 14,
            "business_id": 2, "category_id": 7, "category": "کفش",
            "category_path": "پوشاک > کفش", "product_ids": [10],
            "products": ["کفش چرمی"], "terms": ["سایز"],
        }],
        "comments": {"mode": "all", "policy": "seller_post", "window_days": 14},
    })

    assert seeds == [
        {"kind": "account", "value": "seller", "limit": 3},
        {
            "kind": "hashtag", "value": "کفش", "limit": 8, "window_days": 14,
            "business_id": 2, "category_id": 7, "category": "کفش",
            "category_path": "پوشاک > کفش", "product_ids": [10],
            "products": ["کفش چرمی"], "terms": ["سایز"],
        },
    ]
    assert comments["window_days"] == 14


def test_seed_validation_keeps_existing_hashtag_reads_enabled_by_default():
    seeds, comments = validate_seeds({"hashtags": [{"tag": "کفش", "limit": 4}]})

    assert seeds == [{"kind": "hashtag", "value": "کفش", "limit": 4}]
    assert comments["read_hashtag_posts"] is True


def test_disabled_hashtag_seed_does_not_call_backend(tmp_path):
    class Counting(MockInstagramBackend):
        def __init__(self):
            super().__init__(FIXTURE)
            self.calls = []

        def hashtag_posts(self, tag, limit):
            self.calls.append((tag, limit))
            return super().hashtag_posts(tag, limit)

    backend = Counting()
    result = collector(tmp_path, backend).run(
        [{"kind": "hashtag", "value": "کفش", "limit": 4}],
        {"mode": "all", "max_posts_per_run": 2, "max_per_post": 5,
         "read_hashtag_posts": False},
    )

    assert result["status"] == "COMPLETED"
    assert backend.calls == []


def test_hashtag_collection_passes_window_and_stores_source_and_suggestion(tmp_path):
    now = datetime.now(timezone.utc)
    fixture = {
        "hashtags": {"کفش": [
            {"shortcode": "FreshOne", "caption": "دنبال کفش چرمی هستم",
             "username": "buyer_one", "created_at": (now - timedelta(hours=1)).isoformat()},
            {"shortcode": "FreshTwo", "caption": "کفش مناسب می‌خواهم",
             "username": "buyer_two", "created_at": (now - timedelta(days=1)).isoformat()},
            {"shortcode": "FreshThree", "caption": "برای خرید کفش راهنمایی می‌خواهم",
             "username": "buyer_three", "created_at": (now - timedelta(days=1, hours=1)).isoformat()},
        ]},
    }

    class TrackingBackend(MockInstagramBackend):
        def __init__(self):
            super().__init__(fixture)
            self.calls = []

        def hashtag_posts(self, tag, limit, since=None):
            self.calls.append((tag, limit, since))
            return super().hashtag_posts(tag, limit, since)

    backend = TrackingBackend()
    result = collector(tmp_path, backend, min_intent_score=0).run(
        [{
            "kind": "hashtag", "value": "کفش", "limit": 2, "window_days": 2,
            "business_id": 7, "category_id": 11, "category": "کفش",
            "category_path": "پوشاک > کفش", "product_ids": [19], "products": ["کفش چرمی"],
        }],
        {"mode": "off", "policy": "buyer_request", "max_posts_per_run": 0, "max_per_post": 0},
    )

    posts = rows(Path(result["output"]))
    assert len(posts) == 2
    assert [post["id"] for post in posts] == ["ig:FreshOne", "ig:FreshTwo"]
    tag, limit, since = backend.calls[0]
    assert (tag, limit) == ("کفش", 2)
    assert since <= now - timedelta(days=2) + timedelta(seconds=1)
    assert since > now - timedelta(days=2, seconds=5)
    metadata = posts[0]["metadata"]
    assert metadata["collection_sources"] == [{
        "kind": "hashtag", "hashtag": "کفش", "business_id": 7,
        "category_id": 11, "category": "کفش",
    }]
    assert metadata["suggested_target"] == {
        "business_id": 7, "category_id": 11, "category": "کفش",
        "category_path": "پوشاک > کفش",
        "product_ids": [19], "products": ["کفش چرمی"],
    }
    assert posts[0]["url"] == "https://www.instagram.com/p/FreshOne/"


def test_hashtag_collection_discards_posts_outside_window_and_without_timestamp(tmp_path):
    now = datetime.now(timezone.utc)
    fixture = {
        "hashtags": {"کفش": [
            {"shortcode": "FreshOne", "caption": "دنبال کفش هستم", "username": "buyer_one",
             "created_at": (now - timedelta(hours=1)).isoformat()},
            {"shortcode": "OldOne", "caption": "کفش می‌خواهم", "username": "buyer_old",
             "created_at": (now - timedelta(days=8)).isoformat()},
            {"shortcode": "UnknownTime", "caption": "برای کفش راهنمایی می‌خواهم",
             "username": "buyer_unknown"},
        ]},
    }
    class UnfilteredBackend(MockInstagramBackend):
        def hashtag_posts(self, tag, limit, since=None):
            normalized = tag.lstrip("#").casefold()
            rows = (self.data.get("hashtags") or {}).get(normalized, [])
            return self._posts(rows, len(rows))

    result = collector(tmp_path, UnfilteredBackend(fixture), min_intent_score=0).run(
        [{"kind": "hashtag", "value": "کفش", "limit": 10, "window_days": 7}],
        {"mode": "off", "max_posts_per_run": 0, "max_per_post": 0},
    )

    assert [post["id"] for post in rows(Path(result["output"]))] == ["ig:FreshOne"]


def test_duplicate_account_and_hashtag_post_is_stored_once_with_both_sources(tmp_path):
    post = {
        "shortcode": "SharedPost", "caption": "دنبال کفش هستم", "username": "buyer_one",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    fixture = {"accounts": {"buyer_one": [post]}, "hashtags": {"کفش": [post]}}
    result = collector(tmp_path, MockInstagramBackend(fixture), min_intent_score=0).run(
        [
            {"kind": "account", "value": "buyer_one", "limit": 5},
            {"kind": "hashtag", "value": "کفش", "limit": 5, "window_days": 7,
             "business_id": 7, "category_id": 11, "category": "کفش"},
        ],
        {"mode": "off", "max_posts_per_run": 0, "max_per_post": 0},
    )

    posts = rows(Path(result["output"]))
    assert [post["id"] for post in posts] == ["ig:SharedPost"]
    assert posts[0]["metadata"]["collection_sources"] == [
        {"kind": "account", "username": "buyer_one"},
        {"kind": "hashtag", "hashtag": "کفش", "business_id": 7,
         "category_id": 11, "category": "کفش"},
    ]


def test_hashtag_post_analysis_routes_confident_posts_and_queues_uncertain_posts(tmp_path):
    now = datetime.now(timezone.utc).isoformat()
    fixture = {"hashtags": {"کفش": [
        {"shortcode": "ConfidentPost", "caption": "برای خرید کفش چرمی مردانه این مدل موجود است و برای انتخاب سایز پیام بدهید.",
         "username": "shop_one", "created_at": now, "hashtags": ["کفش_چرمی"]},
        {"shortcode": "ShortPost", "caption": "کفش چرمی", "username": "shop_one",
         "created_at": now, "hashtags": ["کفش_چرمی"]},
        {"shortcode": "OtherPost", "caption": "این هفته چند عکس از مسیرهای زیبای شمال گذاشتم و سفر خیلی خوبی بود.",
         "username": "shop_one", "created_at": now},
    ]}}
    seed = {"kind": "hashtag", "value": "کفش", "limit": 10, "window_days": 7,
            "business_id": 7, "category_id": 11, "category": "کفش",
            "category_path": "پوشاک > کفش", "product_ids": [19],
            "products": ["کفش چرمی مردانه"], "terms": ["کفش چرمی", "سایز"]}
    related_seed = {**seed, "value": "کفش_چرمی"}
    class CountingBackend(MockInstagramBackend):
        def __init__(self, data):
            super().__init__(data)
            self.comment_posts = []

        def post_comments(self, post_ref, limit):
            self.comment_posts.append(post_ref.shortcode)
            return super().post_comments(post_ref, limit)

    backend = CountingBackend(fixture)
    result = collector(tmp_path, backend, min_intent_score=0).run(
        [seed, related_seed], {"mode": "all", "policy": "seller_post", "max_posts_per_run": 10,
                 "max_per_post": 10, "read_hashtag_posts": True},
    )

    stored = {row["id"]: row for row in rows(Path(result["output"]))}
    review = rows(Path(result["output"]).with_name("needs-review.jsonl"))
    assert stored["ig:ConfidentPost"]["metadata"]["category_decision"]["result"] == "confident_match"
    assert stored["ig:ConfidentPost"]["metadata"]["category_decision"]["category_id"] == 11
    assert stored["ig:ConfidentPost"]["metadata"]["category_decision"]["reason"]["evidence"]["post_hashtags"] == ["کفش_چرمی"]
    assert stored["ig:ShortPost"]["metadata"]["decision_status"] == "human_review"
    assert stored["ig:ShortPost"]["metadata"]["suggested_category"] == "کفش"
    assert stored["ig:ShortPost"]["metadata"]["match_reason"]["code"] == "caption_too_short"
    assert stored["ig:OtherPost"]["metadata"]["decision_status"] == "rejected"
    assert [row["id"] for row in review] == ["ig:ShortPost"]
    assert backend.comment_posts == ["ConfidentPost"]


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

    seeds = [
        {"kind": "account", "value": "demo_demand", "limit": 1},
        {"kind": "account", "value": "another_shop", "limit": 1},
    ]
    result = collector(tmp_path, Broken(FIXTURE), max_retries=0, circuit_breaker=2).run(seeds)
    assert result["status"] == "CIRCUIT_OPEN"
    assert result["failures"] == 2


def test_fatal_failure_is_isolated_and_does_not_leak_error(tmp_path, caplog):
    class Rejected(MockInstagramBackend):
        def user_posts(self, username, limit, since):
            if username == "rejected_shop":
                raise Fatal("private-response-secret")
            return super().user_posts(username, limit, since)

    worker = collector(tmp_path, Rejected(FIXTURE), max_retries=0)
    result = worker.run([
        {"kind": "account", "value": "rejected_shop", "limit": 5},
        {"kind": "account", "value": "demo_demand", "limit": 5},
    ])
    assert result["status"] == "PARTIAL"
    assert result["handles"]["demo_demand"]["status"] == "ok"
    assert result["handles"]["rejected_shop"]["status"] == "error"
    assert result["collected"] > 0
    assert "private-response-secret" not in caplog.text


def test_successful_rerun_recovers_from_not_found(tmp_path):
    class SometimesMissing(MockInstagramBackend):
        missing = True

        def user_posts(self, username, limit, since):
            if self.missing:
                raise NotFound("profile missing")
            return super().user_posts(username, limit, since)

    backend = SometimesMissing(FIXTURE)
    worker = collector(tmp_path, backend, max_retries=0)
    first = worker.run([{"kind": "account", "value": "demo_demand", "limit": 1}])
    assert first["handles"]["demo_demand"]["status"] == "not_found"
    backend.missing = False
    second = worker.run([{"kind": "account", "value": "demo_demand", "limit": 1}])
    assert second["handles"]["demo_demand"]["status"] == "ok"


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


def test_configured_mock_backend_uses_seller_post_seed_file(tmp_path, monkeypatch, capsys):
    from workers.ig_collector.cli import _main

    seed_path = tmp_path / "data" / "ig_seeds.json"
    seed_path.parent.mkdir()
    seed_path.write_text(json.dumps({
        "accounts": [{"username": "demo_demand", "limit": 5}],
        "comments": {"mode": "all", "policy": "seller_post",
                       "max_posts_per_run": 5, "max_per_post": 30},
    }), encoding="utf-8")
    output_dir = tmp_path / "collected"
    monkeypatch.setenv("IG_COLLECT_STATE_FILE", str(tmp_path / "state.sqlite3"))

    assert _main(["--backend", "mock", "--once", "--seeds", str(seed_path),
                  "--output-dir", str(output_dir)], project_root=tmp_path) == 0
    records = rows(output_dir / "seller_leads.jsonl")
    assert [record["id"] for record in records] == ["ig:IGAd102:cc-5"]


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


def test_handle_outcome_is_written_to_status_json(tmp_path):
    worker = collector(tmp_path)
    result = worker.run(SEEDS, COMMENTS)
    status = json.loads((worker.output_dir / "status.json").read_text(encoding="utf-8"))
    assert status["handles"]["demo_demand"]["status"] == "ok"
    assert status["handles"]["demo_demand"]["posts"] == 5
    assert result["seller_leads"] == 0


def test_seller_post_policy_writes_only_lead_comments_to_separate_file(tmp_path):
    worker = collector(tmp_path)
    result = worker.run(SEEDS, {"mode": "all", "policy": "seller_post",
                                "max_posts_per_run": 10, "max_per_post": 10})

    records = rows(worker.output_dir / "seller_leads.jsonl")
    assert result["seller_leads"] == 1
    assert [record["id"] for record in records] == ["ig:IGAd102:cc-5"]
    assert records[0]["metadata"]["intent"]["label"] == "price_question"


def test_rate_limit_keeps_completed_handle_outcome(tmp_path):
    class Limited(MockInstagramBackend):
        def user_posts(self, username, limit, since):
            if username == "limited_shop":
                raise RateLimited("request limited")
            return super().user_posts(username, limit, since)

    worker = collector(tmp_path, Limited(FIXTURE), max_retries=0)
    result = worker.run([
        {"kind": "account", "value": "demo_demand", "limit": 1},
        {"kind": "account", "value": "limited_shop", "limit": 1},
        {"kind": "account", "value": "later_shop", "limit": 1},
    ])

    assert result["handles"]["demo_demand"]["status"] == "ok"
    assert result["handles"]["limited_shop"]["status"] == "rate_limited"
    assert result["handles"]["later_shop"]["status"] == "rate_limited"


def test_one_missing_handle_does_not_stop_the_other_handles(tmp_path):
    class OneMissing(MockInstagramBackend):
        def user_posts(self, username, limit, since):
            if username == "missing_shop":
                raise NotFound("profile missing")
            return super().user_posts(username, limit, since)

    worker = collector(tmp_path, OneMissing(FIXTURE), max_retries=0)
    result = worker.run([
        {"kind": "account", "value": "missing_shop", "limit": 5},
        {"kind": "account", "value": "demo_demand", "limit": 5},
    ])

    assert result["handles"]["missing_shop"]["status"] == "not_found"
    assert result["handles"]["demo_demand"]["status"] == "ok"
    assert result["collected"] > 0


def test_private_profile_cooldown_is_persisted_and_skips_next_request(tmp_path):
    from workers.ig_collector.backends.base import PrivateProfile

    class Private(MockInstagramBackend):
        def __init__(self, fixture):
            super().__init__(fixture)
            self.calls = 0

        def user_posts(self, username, limit, since):
            self.calls += 1
            raise PrivateProfile("private profile")

    backend = Private(FIXTURE)
    worker = collector(tmp_path, backend, max_retries=0)
    seed = [{"kind": "account", "value": "private_shop", "limit": 1}]

    first = worker.run(seed)
    cooldown = worker.store.get_seed_progress("private:account:private_shop")
    second = worker.run(seed)

    assert first["handles"]["private_shop"]["status"] == "private"
    assert cooldown is not None and cooldown.tzinfo is not None
    assert second["handles"]["private_shop"]["reason"] == "private_cooldown"
    assert backend.calls == 1


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
            self.context = type("Context", (), {"request_timeout": None, "_session": None,
                                                  "_graphql_session": None})()
            type(self).context = self.context

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
    assert Loader.context.request_timeout == 30


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
    assert json.loads(status)["output"] == Path(result["output"]).name


def test_persian_text_round_trips_jsonl(tmp_path):
    result = collector(tmp_path).run(SEEDS, {"mode": "off"})
    first = rows(Path(result["output"]))[0]
    assert first["text"] == "کسی آسیاب قهوه خوب می‌شناسه؟ قیمت مناسب باشه لطفا"
    assert "\\u06a9" not in Path(result["output"]).read_text(encoding="utf-8")
    assert validate_record(first)


def test_error_types_and_capabilities_are_explicit():
    assert [RateLimited.kind, AuthFailed.kind, Transient.kind, Fatal.kind] == ["rate_limit", "auth", "transient", "fatal"]
    assert BackendCapabilities(user_posts=False, hashtag_posts=True, post_comments=False).hashtag_posts

import json
from datetime import timezone
from pathlib import Path

import pytest

from shared.records import RecordError, SourceRecord, to_json_line
from workers.x_collector.worker import normalize_tweet
from x_ingest.__main__ import validate_record


FIXTURE = Path(__file__).parent / "fixtures" / "x_tweets.json"
LEGACY_JSONL = Path(__file__).parent / "fixtures" / "x_record_legacy.jsonl"


def test_old_x_fixture_line_round_trips_through_source_record():
    tweet, = json.loads(FIXTURE.read_text(encoding="utf-8"))[:1]
    old_record = normalize_tweet(tweet)

    parsed = SourceRecord.from_dict(old_record)
    line = to_json_line(parsed)

    assert "\n" not in line
    assert json.loads(line) == old_record
    assert parsed.id == "1990000000000001001"
    assert parsed.source == "x"
    assert parsed.created_at.tzinfo == timezone.utc


def test_legacy_x_jsonl_fixture_keeps_its_field_names():
    line, = LEGACY_JSONL.read_text(encoding="utf-8").splitlines()
    original = json.loads(line)

    parsed = SourceRecord.from_dict(original)

    assert json.loads(to_json_line(parsed)) == original
    assert set(original) == {"id", "source", "text", "author_handle", "author_name",
                             "author_id", "author_verified", "author_bio", "created_at",
                             "url", "metadata"}


def test_x_ingest_rejects_another_platform_record():
    tweet, = json.loads(FIXTURE.read_text(encoding="utf-8"))[:1]
    record = normalize_tweet(tweet)
    record["source"] = "instagram"

    with pytest.raises(ValueError, match="source must equal x"):
        validate_record(record)


def test_x_ingest_rejects_a_record_without_author_id():
    tweet, = json.loads(FIXTURE.read_text(encoding="utf-8"))[:1]
    record = normalize_tweet(tweet)
    del record["author_id"]

    with pytest.raises(ValueError, match="author_id"):
        validate_record(record)


def test_x_ingest_keeps_legacy_quote_kind():
    tweet, = json.loads(FIXTURE.read_text(encoding="utf-8"))[:1]
    record = normalize_tweet(tweet)
    record["kind"] = "quote"

    assert validate_record(record)["kind"] == "quote"


@pytest.mark.parametrize(
    "record, reason",
    [
        ([], "record must be an object"),
        ({"id": "1"}, "source must be x, instagram, or telegram"),
        ({"id": "1", "source": "x", "text": " "}, "text must be a non-empty string"),
        ({"id": "1", "source": "x", "text": "hello", "author_handle": "@buyer", "author_id": None, "created_at": "2026-01-01T00:00:00"},
         "created_at must include a timezone"),
    ],
)
def test_source_record_rejects_invalid_fields_with_short_reason(record, reason):
    with pytest.raises(RecordError, match=reason):
        SourceRecord.from_dict(record)


def test_retry_delay_does_not_allow_negative_jitter():
    from shared.collector_kit.limits import retry_delay

    delay = retry_delay(2, lambda low, high: -1.0)

    assert delay == 4.0

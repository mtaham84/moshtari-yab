import json
import logging
from pathlib import Path

import pytest

from workers.x_collector.worker import normalize_tweet
from x_ingest.__main__ import _load, load, validate_record


FIXTURE = Path(__file__).resolve().parents[2] / "workers" / "x_collector" / "tests" / "fixtures" / "x_tweets.json"


def fixture_records():
    tweets = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return [normalize_tweet(tweet, "fixture query") for tweet in tweets]


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def row_count(dsn, schema):
    import psycopg

    with psycopg.connect(dsn) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {schema}.x_posts").fetchone()[0]


def test_loading_same_jsonl_file_twice_keeps_row_count(tmp_path, pg_dsn, pg_schema, monkeypatch):
    schema = pg_schema("x_ingest")
    monkeypatch.setenv("NE_CRAWLER_SCHEMA", schema)
    path = tmp_path / "posts.jsonl"
    records = fixture_records()
    write_jsonl(path, records)

    assert load(path, pg_dsn) == len(records)
    first_count = row_count(pg_dsn, schema)
    assert load(path, pg_dsn) == len(records)

    assert first_count == len(records)
    assert row_count(pg_dsn, schema) == first_count


def test_malformed_jsonl_line_is_skipped_and_valid_records_are_counted(tmp_path, pg_dsn, pg_schema, monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    schema = pg_schema("x_ingest")
    monkeypatch.setenv("NE_CRAWLER_SCHEMA", schema)
    path = tmp_path / "posts.jsonl"
    records = fixture_records()
    path.write_text(
        json.dumps(records[0], ensure_ascii=False) + "\n{not json}\n" + json.dumps(records[1], ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    assert _load(path, pg_dsn) == (len(records), 1)
    assert row_count(pg_dsn, schema) == len(records)
    assert "Skipping" in caplog.text
    assert f"Skipped 1 invalid X record(s)" in caplog.text


def test_record_without_author_id_is_rejected_by_loader(tmp_path, pg_dsn, pg_schema, monkeypatch):
    schema = pg_schema("x_ingest")
    monkeypatch.setenv("NE_CRAWLER_SCHEMA", schema)
    record = fixture_records()[0]
    del record["author_id"]
    path = tmp_path / "posts.jsonl"
    write_jsonl(path, [record])

    assert load(path, pg_dsn) == 0
    assert row_count(pg_dsn, schema) == 0
    with pytest.raises(ValueError, match="author_id"):
        validate_record(record)


def test_persian_digits_and_zwnj_survive_jsonl_loading(tmp_path, pg_dsn, pg_schema, monkeypatch):
    import psycopg

    schema = pg_schema("x_ingest")
    monkeypatch.setenv("NE_CRAWLER_SCHEMA", schema)
    record = fixture_records()[0]
    record["text"] = "قیمت ۱۲۳٬۴۵۰ تومان است؛ می‌خواهم اطلاعات بگیرم"
    path = tmp_path / "posts.jsonl"
    write_jsonl(path, [record])

    assert load(path, pg_dsn) == 1
    with psycopg.connect(pg_dsn) as conn:
        saved_text = conn.execute(f"SELECT text FROM {schema}.x_posts WHERE tweet_id = %s", (int(record["id"]),)).fetchone()[0]

    assert saved_text == record["text"]

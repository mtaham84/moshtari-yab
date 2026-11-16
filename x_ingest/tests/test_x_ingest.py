import json

import pytest

from x_ingest.__main__ import validate_record


def test_validates_x_post_contract_and_persian_text():
    record = {"id": "1990000000000001001", "source": "x", "text": "دنبال دستگاه هستم",
              "author_id": "81001", "author_handle": "@buyer", "author_name": "خریدار",
              "created_at": "2026-10-01T10:00:00+00:00", "url": "https://x.com/buyer/status/1990000000000001001",
              "metadata": {"lang": "fa", "query": "دستگاه", "raw_cli": {}}}
    parsed = validate_record(json.loads(json.dumps(record, ensure_ascii=False)))
    assert parsed["tweet_id"] == 1990000000000001001
    assert parsed["text"] == "دنبال دستگاه هستم"
    assert parsed["query"] == "دستگاه"
    assert parsed["lang"] == "fa"


@pytest.mark.parametrize("field,value", [("source", "telegram"), ("author_id", ""), ("text", "")])
def test_rejects_invalid_records(field, value):
    record = {"id": "1990000000000001001", "source": "x", "text": "سلام", "author_id": "1",
              "author_handle": "@buyer", "metadata": {}}
    record[field] = value
    with pytest.raises(ValueError):
        validate_record(record)


def test_bad_database_record_rolls_back_only_that_record(pg_dsn, pg_schema, tmp_path, monkeypatch, caplog):
    import psycopg
    from x_ingest.__main__ import _load

    schema = pg_schema('ingest')
    monkeypatch.setenv('NE_CRAWLER_SCHEMA', schema)
    path = tmp_path / 'posts.jsonl'
    base = {'source': 'x', 'text': 'دنبال لپتاپ هستم', 'author_id': '1', 'author_handle': '@buyer',
            'product_id': 'p1', 'metadata': {}}
    # The middle numeric ID passes validation but cannot fit a PostgreSQL BIGINT.
    path.write_text('\n'.join(json.dumps({**base, 'id': tid}) for tid in ['101', '999999999999999999999999', '103']))
    assert _load(path, pg_dsn) == (2, 1)
    with psycopg.connect(pg_dsn) as conn:
        assert conn.execute(f'SELECT tweet_id FROM {schema}.x_posts ORDER BY tweet_id').fetchall() == [(101,), (103,)]
        assert conn.execute(f'SELECT tweet_id FROM {schema}.x_post_products ORDER BY tweet_id').fetchall() == [(101,), (103,)]
    assert 'line 2' in caplog.text

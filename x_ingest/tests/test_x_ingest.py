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

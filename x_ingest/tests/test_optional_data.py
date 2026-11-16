from copy import deepcopy

from shared.records import SourceRecord
from x_ingest.__main__ import validate_record


def old_record():
    return {'id': '101', 'source': 'x', 'text': 'دنبال لپتاپ هستم', 'author_id': '1',
            'author_handle': '@buyer', 'metadata': {'lang': 'fa', 'query': 'q'}}


def test_old_record_contract_is_unchanged():
    record = old_record()
    assert SourceRecord.from_dict(record).as_dict() == record
    assert validate_record(record)['tweet_id'] == 101


def test_optional_counts_dates_and_parent_are_normalized_with_counted_warnings():
    record = {**old_record(), 'schema_version': 2, 'observed_at': '2026-11-16T10:00:00Z',
              'author': {'id': '1', 'followers_count': '12', 'following_count': 'broken',
                         'account_created_at': 'Wed Oct 10 20:19:24 +0000 2018'},
              'metrics': {'likes': 0}, 'parent': {'kind': 'reply', 'in_reply_to_tweet_id': '99'}}
    before = deepcopy(record)
    parsed = validate_record(record)
    assert parsed['author']['followers_count'] == 12
    assert parsed['author']['following_count'] is None
    assert parsed['metrics']['likes'] == 0
    assert parsed['metrics']['views'] is None
    assert parsed['author']['account_created_at'] == '2018-10-10T20:19:24+00:00'
    assert parsed['field_warnings']['author.following_count'] == 1
    assert parsed['parent']['kind'] == 'reply'
    assert record == before


def test_credentials_are_removed_from_raw_including_nested_proxy_userinfo():
    record = old_record()
    record['metadata']['raw_cli'] = {'auth_token': 'PRIVATE_TOKEN', 'headers': {'Cookie': 'PRIVATE_COOKIE'},
                                     'nested': ['https://PRIVATE_USER:PRIVATE_PASSWORD@host/path']}
    raw = validate_record(record)['raw']
    assert 'PRIVATE_' not in str(raw)

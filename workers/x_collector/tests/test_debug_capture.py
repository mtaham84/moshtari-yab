"""Synthetic GraphQL envelopes follow installed twitter_cli.parser lines 200–315.

Candidate fields are probes requested in item 0b, not evidence of real X availability.
Only the transport and its eager network initialization are replaced.
"""
from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
from types import SimpleNamespace

import pytest

from workers.x_collector.client import XCliClient


@pytest.fixture
def page():
    user = {
        'rest_id': '801', 'core': {'name': 'PRIVATE_NAME', 'screen_name': 'PRIVATE_HANDLE'},
        'legacy': {'followers_count': 123, 'friends_count': '45', 'statuses_count': 0,
                   'description': 'PRIVATE_BIO', 'location': 'PRIVATE_LOCATION',
                   'created_at': 'Wed Oct 10 20:19:24 +0000 2018', 'verified': False},
        'is_blue_verified': True, 'location': 'OTHER_LOCATION',
        'auth_token': 'TOKEN_SECRET', 'cookies': {'session': 'COOKIE_SECRET'},
    }
    result = {
        'rest_id': '101', 'core': {'user_results': {'result': user}},
        'legacy': {'full_text': 'دنبال لپتاپ هستم PRIVATE_TEXT', 'lang': 'fa',
                   'created_at': '2026-11-14T12:00:00+00:00', 'favorite_count': '7',
                   'retweet_count': 2, 'reply_count': 0, 'bookmark_count': 1,
                   'conversation_id_str': '101', 'in_reply_to_screen_name': 'PRIVATE_PARENT'},
        'views': {'count': '900'}, 'headers': {'Authorization': 'AUTH_SECRET'},
        'proxy_url': 'http://PRIVATE_PROXY', 'rawQuery': 'PRIVATE_QUERY',
        'deep': {'one': {'two': {'three': {'four': {'beyond_limit': 'PRIVATE_DEEP'}}}}},
    }
    return {'data': {'search_by_raw_query': {'search_timeline': {'timeline': {'instructions': [
        {'entries': [
            {'content': {'itemContent': {'tweet_results': {'result': result}}}},
            {'content': {'cursorType': 'Bottom', 'value': 'PRIVATE_CURSOR'}},
        ]},
    ]}}}}}


@pytest.fixture
def native(monkeypatch):
    import twitter_cli.client as library
    from workers.x_collector import debug_capture

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 11, 15, 12, tzinfo=timezone.utc)

    monkeypatch.delenv('X_DEBUG_CAPTURE_PATH', raising=False)
    monkeypatch.setenv('TWITTER_SEARCH_METHOD', 'get')
    monkeypatch.setattr(debug_capture, '_attempted_pid', None)
    monkeypatch.setattr(library.TwitterClient, '_ensure_client_transaction', lambda _: None)
    monkeypatch.setattr('workers.x_collector.client.native_session', lambda *_: nullcontext())
    monkeypatch.setattr('workers.x_collector.client.time.sleep', lambda _: None)
    monkeypatch.setattr('workers.x_collector.client.datetime', Clock)
    calls = []
    pages = []

    def get(_self, operation, variables, features):
        calls.append((operation, dict(variables)))
        return deepcopy(pages[len(calls) - 1])

    monkeypatch.setattr(library.TwitterClient, '_graphql_get', get)
    account = SimpleNamespace(id=1, username='demo', proxy_url='', auth_token='TOKEN_SECRET', ct0='COOKIE_SECRET')
    return SimpleNamespace(client=XCliClient(), account=account, calls=calls, pages=pages)


def test_unset_capture_writes_nothing_and_enabled_capture_keeps_output_bytes(native, page, monkeypatch, tmp_path):
    native.pages.extend([page, page])
    baseline = native.client.search_top_past_week('PRIVATE_QUERY', target_posts=1, account=native.account)
    assert list(tmp_path.iterdir()) == []
    monkeypatch.setenv('X_DEBUG_CAPTURE_PATH', str(tmp_path / 'capture.json'))
    captured = native.client.search_top_past_week('PRIVATE_QUERY', target_posts=1, account=native.account)
    assert json.dumps(captured, ensure_ascii=False).encode() == json.dumps(baseline, ensure_ascii=False).encode()
    assert (tmp_path / 'capture.json').is_file()
    assert len(native.calls) == 2


def test_capture_uses_first_page_once_across_pages_queries_and_client_instances(native, page, monkeypatch, tmp_path):
    second = deepcopy(page)
    result = second['data']['search_by_raw_query']['search_timeline']['timeline']['instructions'][0]['entries'][0]['content']['itemContent']['tweet_results']['result']
    result['rest_id'] = '102'
    result['core']['user_results']['result']['legacy']['followers_count'] = 999
    native.pages.extend([page, second, page])
    path = tmp_path / 'capture.json'
    monkeypatch.setenv('X_DEBUG_CAPTURE_PATH', str(path))
    native.client.search_top_past_week('PRIVATE_QUERY', target_posts=2, account=native.account)
    before = path.read_bytes()
    XCliClient().search_top_past_week('OTHER_QUERY', target_posts=1, account=native.account)
    assert len(native.calls) == 3
    assert list(tmp_path.iterdir()) == [path]
    assert path.read_bytes() == before
    assert json.loads(before)['results'][0]['paths']['U.legacy.followers_count'] == {'present': True, 'number': 123}
    assert path.stat().st_mode & 0o777 == 0o600


def test_capture_has_only_types_presence_counts_and_lengths_without_secrets(native, page, monkeypatch, tmp_path):
    native.pages.append(page)
    path = tmp_path / 'capture.json'
    monkeypatch.setenv('X_DEBUG_CAPTURE_PATH', str(path))
    native.client.search_top_past_week('PRIVATE_QUERY', target_posts=1, account=native.account)
    raw = path.read_text()
    assert not re.search(r'cookie|token|auth|header|proxy|PRIVATE_', raw, re.IGNORECASE)

    def no_string_values(value):
        assert not isinstance(value, str)
        if isinstance(value, dict):
            for child in value.values():
                no_string_values(child)
        elif isinstance(value, list):
            for child in value:
                no_string_values(child)

    report = json.loads(raw)
    no_string_values(report)
    sample = report['results'][0]
    paths = sample['paths']
    assert paths['U.legacy.followers_count'] == {'present': True, 'number': 123}
    assert paths['U.legacy.friends_count'] == {'present': True, 'number': 45}
    assert paths['U.legacy.statuses_count'] == {'present': True, 'number': 0}
    assert paths['U.legacy.listed_count'] == {'present': False}
    assert paths['U.legacy.created_at'] == {'present': True}
    assert paths['U.core.created_at'] == {'present': False}
    assert paths['U.legacy.description'] == {'present': True, 'length': 11}
    assert paths['U.legacy.location'] == {'present': True, 'length': 16}
    assert paths['U.location'] == {'present': True, 'length': 14}
    assert paths['U.is_blue_verified'] == {'present': True}
    assert paths['U.legacy.verified'] == {'present': True}
    assert paths['R.legacy.conversation_id_str'] == {'present': True}
    assert paths['R.legacy.in_reply_to_screen_name'] == {'present': True}
    assert paths['R.legacy.in_reply_to_status_id_str'] == {'present': False}
    assert paths['R.legacy.is_quote_status'] == {'present': False}
    assert paths['R.legacy.favorite_count'] == {'present': True, 'number': 7}
    assert paths['R.legacy.quote_count'] == {'present': False}
    assert paths['R.views.count'] == {'present': True, 'number': 900}
    assert sample['R']['legacy']['full_text'] == {'$str': True}
    assert sample['R']['deep']['one']['two']['three']['four'] == {'$dict': True}


def test_capture_limits_results_and_supports_nested_visibility_wrappers(native, page, monkeypatch, tmp_path):
    result = page['data']['search_by_raw_query']['search_timeline']['timeline']['instructions'][0]['entries'][0]['content']['itemContent']['tweet_results']['result']
    nested = {'item': {'itemContent': {'tweet_results': {'result': {
        '__typename': 'TweetWithVisibilityResults', 'tweet': result,
    }}}}}
    page['data']['search_by_raw_query']['search_timeline']['timeline']['instructions'] = [
        {'entries': [{'content': {'items': [deepcopy(nested) for _ in range(8)]}}]},
    ]
    native.pages.append(page)
    path = tmp_path / 'capture.json'
    monkeypatch.setenv('X_DEBUG_CAPTURE_PATH', str(path))
    native.client.search_top_past_week('PRIVATE_QUERY', target_posts=1, account=native.account)
    samples = json.loads(path.read_text())['results']
    assert len(samples) == 5
    assert all(sample['paths']['U.legacy.followers_count']['present'] for sample in samples)


def test_capture_file_error_does_not_change_search_result(native, page, monkeypatch, tmp_path, caplog):
    native.pages.extend([page, page])
    baseline = native.client.search_top_past_week('PRIVATE_QUERY', target_posts=1, account=native.account)
    monkeypatch.setenv('X_DEBUG_CAPTURE_PATH', str(tmp_path / 'missing' / 'capture.json'))
    actual = native.client.search_top_past_week('PRIVATE_QUERY', target_posts=1, account=native.account)
    assert actual == baseline
    assert 'debug capture' in caplog.text.lower()


def test_capture_does_not_overwrite_existing_file(native, page, monkeypatch, tmp_path):
    path = tmp_path / 'capture.json'
    path.write_bytes(b'existing sample')
    monkeypatch.setenv('X_DEBUG_CAPTURE_PATH', str(path))
    native.pages.extend([page, page])
    native.client.search_top_past_week('PRIVATE_QUERY', target_posts=1, account=native.account)
    path.unlink()
    native.client.search_top_past_week('PRIVATE_QUERY', target_posts=1, account=native.account)
    assert not path.exists()

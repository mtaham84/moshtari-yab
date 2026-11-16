"""Synthetic search shapes: parser.py 200–315 plus user-specified item 0b candidate paths."""
from workers.x_collector.observations import cli_observation, native_observations


def test_native_observations_preserve_missing_zero_and_string_counts():
    result = {'rest_id': '101', 'legacy': {'favorite_count': '0', 'in_reply_to_status_id_str': '99'},
              'core': {'user_results': {'result': {'rest_id': '1', 'legacy': {'followers_count': '12',
                 'created_at': 'Wed Oct 10 20:19:24 +0000 2018'}, 'is_blue_verified': False}}}}
    data = {'data': {'search_by_raw_query': {'search_timeline': {'timeline': {'instructions': [
        {'entries': [{'content': {'itemContent': {'tweet_results': {'result': result}}}}]}]}}}}}
    stats = {}
    found = native_observations(data, stats)['101']
    assert found['author']['followers_count'] == 12
    assert found['author']['following_count'] is None
    assert found['author']['blue_verified'] is False
    assert found['metrics']['likes'] == 0
    assert found['metrics']['views'] is None
    assert found['parent']['kind'] == 'post'
    assert found['parent']['in_reply_to_tweet_id'] == '99'
    assert found['metrics_source'] == 'native_raw'
    assert stats['metrics_fields_missing']['views'] == 1
    assert found['author']['account_created_at'] == '2018-10-10T20:19:24+00:00'


def test_cli_observation_does_not_invent_separate_verification_or_missing_counts():
    details = cli_observation({'author': {'id': '1', 'screenName': 'buyer', 'verified': True},
                               'metrics': {'likes': '7'}, 'quotedTweet': {'id': '9'}}, {})
    assert details['metrics']['likes'] == 7
    assert details['metrics']['views'] is None
    assert details['author']['verified'] is None
    assert details['author']['blue_verified'] is None
    assert details['parent']['quoted_tweet_id'] == '9'
    assert details['parent']['kind'] == 'post'
    assert details['metrics_source'] == 'cli_parsed'


def timeline(result):
    return {'data': {'search_by_raw_query': {'search_timeline': {'timeline': {'instructions': [
        {'entries': [{'content': {'itemContent': {'tweet_results': {'result': result}}}}]}]}}}}}


def test_missing_candidate_paths_remain_null_and_do_not_classify_a_reply():
    found = native_observations(timeline({'rest_id': '1'}), {})['1']
    assert all(value is None for value in found['author'].values())
    assert all(value is None for value in found['metrics'].values())
    assert found['parent'] == {'kind': 'post', 'conversation_id': None, 'in_reply_to_tweet_id': None,
                               'in_reply_to_user_id': None, 'in_reply_to_handle': None, 'quoted_tweet_id': None}


def test_author_and_reply_values_use_only_the_documented_candidates():
    result = {'rest_id': '1', 'legacy': {'in_reply_to_status_id_str': '99',
              'in_reply_to_user_id_str': '7', 'in_reply_to_screen_name': 'parent', 'conversation_id_str': '88'},
              'core': {'user_results': {'result': {'rest_id': '2', 'location': 'Paris',
                'core': {'created_at': '2020-01-01T00:00:00Z'},
                'legacy': {'followers_count': '15', 'friends_count': 0, 'description': 'bio'}}}}}
    found = native_observations(timeline(result), {})['1']
    assert found['author']['followers_count'] == 15
    assert found['author']['following_count'] == 0
    assert found['author']['bio'] == 'bio'
    assert found['author']['location'] == 'Paris'
    assert found['author']['account_created_at'] == '2020-01-01T00:00:00+00:00'
    assert found['parent']['in_reply_to_user_id'] == '7'
    assert found['parent']['in_reply_to_handle'] == 'parent'
    assert found['parent']['conversation_id'] == '88'
    assert found['parent']['kind'] == 'post'


def test_injectable_clock_covers_native_and_cli(monkeypatch):
    from datetime import datetime, timezone
    from workers.x_collector import observations
    stamp = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(observations, 'now', lambda: stamp)
    native = native_observations(timeline({'rest_id': '1'}), {})['1']
    cli = cli_observation({}, {})
    assert native['observed_at'] == cli['observed_at'] == '2026-11-16T12:00:00+00:00'


def test_cli_parser_zero_defaults_are_marked_in_the_ingest_contract():
    from twitter_cli.parser import parse_tweet_result
    from twitter_cli.serialization import tweet_to_dict
    from x_ingest.__main__ import validate_record
    result = {'rest_id': '1', 'legacy': {'full_text': 'دنبال لپتاپ هستم'},
              'core': {'user_results': {'result': {'rest_id': '2', 'legacy': {'screen_name': 'buyer'}}}}}
    cli = tweet_to_dict(parse_tweet_result(result))
    details = cli_observation(cli, {})
    native = native_observations(timeline(result), {})['1']
    assert details['metrics']['views'] == 0
    assert native['metrics']['views'] is None
    assert details['metrics_source'] == 'cli_parsed'
    parsed = validate_record({'id': '1', 'source': 'x', 'author_id': '2', 'author_handle': 'buyer',
                              'text': 'دنبال لپتاپ هستم', 'metadata': {}, **details})
    assert parsed['metrics_source'] == 'cli_parsed'
    assert parsed['raw']['metrics_source'] == 'cli_parsed'

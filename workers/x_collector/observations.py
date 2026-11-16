"""Extract only fields present in existing search payloads; unknown counts stay NULL.

Raw candidates are the item 0b paths; their live presence remains unverified.
CLI supported keys follow twitter_cli.serialization.tweet_to_dict (0.8.5 lines 12–63).
"""
from datetime import datetime, timezone

from shared.x_record_data import optional_data
from .payload import _get, _results, _unwrap, raw_parts


def now():
    """Single injectable observation clock; no transport or file side effects."""
    return datetime.now(timezone.utc)


def _details(author, metrics, parent, stats, source, observed=None):
    result = optional_data({'schema_version': 2, 'author': author, 'metrics': metrics, 'parent': parent,
                            'observed_at': observed if observed is not None else now().isoformat(),
                            'metrics_source': source})
    for section in ('author', 'metrics'):
        counts = stats.setdefault(section + '_fields_missing', {})
        for key, value in result[section].items():
            if value is None:
                counts[key] = counts.get(key, 0) + 1
    return result


def native_observations(data, stats):
    observations = {}
    observed = now().isoformat()
    for entry in _results(data):
        result, user = raw_parts(entry)
        user = user if isinstance(user, dict) else {}
        legacy = user.get('legacy') if isinstance(user.get('legacy'), dict) else {}
        core = user.get('core') if isinstance(user.get('core'), dict) else {}
        post = result.get('legacy') if isinstance(result.get('legacy'), dict) else {}
        author = {'id': user.get('rest_id'), 'handle': core.get('screen_name') or legacy.get('screen_name') or user.get('screen_name'),
                  'name': core.get('name') or legacy.get('name') or user.get('name'),
                  'bio': legacy.get('description'), 'location': legacy.get('location') if legacy.get('location') is not None else user.get('location'),
                  'verified': legacy.get('verified'), 'blue_verified': user.get('is_blue_verified'),
                  'followers_count': legacy.get('followers_count'), 'following_count': legacy.get('friends_count'),
                  'tweets_count': legacy.get('statuses_count'), 'listed_count': legacy.get('listed_count'),
                  'account_created_at': legacy.get('created_at') or core.get('created_at')}
        metrics = {key: post.get(raw) for key, raw in [('likes','favorite_count'), ('retweets','retweet_count'),
                   ('replies','reply_count'), ('quotes','quote_count'), ('bookmarks','bookmark_count')]}
        metrics['views'] = _get(result, 'views', 'count')
        quoted = _get(result, 'quoted_status_result', 'result')
        quoted_id = post.get('quoted_status_id_str') or (_unwrap(quoted).get('rest_id') if isinstance(quoted, dict) else None)
        # Classification is deferred to item 7: keep the existing analysis eligibility.
        parent = {'kind': 'post',
                  'conversation_id': post.get('conversation_id_str'), 'in_reply_to_tweet_id': post.get('in_reply_to_status_id_str'),
                  'in_reply_to_user_id': post.get('in_reply_to_user_id_str'), 'in_reply_to_handle': post.get('in_reply_to_screen_name'),
                  'quoted_tweet_id': quoted_id}
        observations[str(result.get('rest_id') or '')] = _details(author, metrics, parent, stats, 'native_raw', observed)
    return observations


def cli_observation(item, stats):
    if item.get('schema_version') == 2:
        normalized = optional_data(item)
        normalized['metrics_source'] = 'cli_parsed'
        if 'parent' in normalized:
            normalized['parent']['kind'] = 'post'
        return normalized
    user = item.get('author') if isinstance(item.get('author'), dict) else {}
    author = {'id': user.get('id') or item.get('author_id'), 'handle': user.get('screenName') or item.get('username'),
              'name': user.get('name') or item.get('name')}
    # The CLI's verified flag combines legacy and blue status; neither separate flag can be recovered.
    metrics = item.get('metrics') if isinstance(item.get('metrics'), dict) else {}
    if 'likes' not in metrics and isinstance(item.get('public_metrics'), dict):
        metrics = {**metrics, 'likes': item['public_metrics'].get('like_count')}
    quoted = item.get('quotedTweet') if isinstance(item.get('quotedTweet'), dict) else {}
    parent = {'kind': 'post', 'quoted_tweet_id': quoted.get('id')}
    return _details(author, metrics, parent, stats, 'cli_parsed')

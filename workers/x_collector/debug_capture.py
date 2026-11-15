"""Opt-in, one-shot shape capture; never retain a response's string values.

Envelope traversal follows twitter_cli.parser.parse_timeline_response (0.8.5,
lines 278–315). Candidate paths are item 0b probes, not availability claims.
"""
import json
import logging
import os
import re
from threading import Lock

from .config import debug_capture_path

log = logging.getLogger('x_collector')
_lock = Lock()
_attempted_pid = None
_sensitive_key = re.compile(r'auth|cookie|token|header|proxy|query|password|secret', re.IGNORECASE)
_safe_key = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')
_counts = {
    'U.legacy.followers_count', 'U.legacy.friends_count', 'U.legacy.statuses_count',
    'U.legacy.listed_count', 'R.legacy.favorite_count', 'R.legacy.retweet_count',
    'R.legacy.reply_count', 'R.legacy.quote_count', 'R.legacy.bookmark_count', 'R.views.count',
}
_lengths = {'U.legacy.description', 'U.legacy.location', 'U.location'}
_paths = sorted(_counts | _lengths | {
    'U.legacy.created_at', 'U.core.created_at', 'U.is_blue_verified', 'U.legacy.verified',
    'R.legacy.conversation_id_str', 'R.legacy.in_reply_to_status_id_str',
    'R.legacy.in_reply_to_user_id_str', 'R.legacy.in_reply_to_screen_name',
    'R.legacy.is_quote_status', 'R.legacy.quoted_status_id_str',
})


def _get(value, *keys):
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _results(data):
    instructions = _get(data, 'data', 'search_by_raw_query', 'search_timeline', 'timeline', 'instructions')
    for instruction in instructions if isinstance(instructions, list) else []:
        if not isinstance(instruction, dict):
            continue
        entries = instruction.get('entries') or instruction.get('moduleItems') or []
        for entry in entries if isinstance(entries, list) else []:
            content = _get(entry, 'content')
            if not isinstance(content, dict):
                continue
            result = _get(content, 'itemContent', 'tweet_results', 'result')
            if isinstance(result, dict):
                yield result
            items = content.get('items')
            for item in items if isinstance(items, list) else []:
                result = _get(item, 'item', 'itemContent', 'tweet_results', 'result')
                if isinstance(result, dict):
                    yield result


def _unwrap(result):
    if result.get('__typename') == 'TweetWithVisibilityResults' and isinstance(result.get('tweet'), dict):
        return result['tweet']
    return result


def _type_tree(value, depth=0):
    if isinstance(value, dict):
        tree = {'$dict': True}
        if depth < 5:
            tree.update({key: _type_tree(child, depth + 1) for key, child in value.items()
                         if isinstance(key, str) and _safe_key.fullmatch(key) and not _sensitive_key.search(key)})
        return tree
    if isinstance(value, list):
        tree = {'$list': True}
        if depth < 5:
            tree['$items'] = [_type_tree(child, depth + 1) for child in value[:5]]
        return tree
    return {'$' + type(value).__name__: True}


def _probe(roots, path):
    value = roots
    for key in path.split('.'):
        if not isinstance(value, dict) or key not in value:
            return {'present': False}
        value = value[key]
    observation = {'present': True}
    if path in _lengths and isinstance(value, str):
        observation['length'] = len(value)
    if path in _counts and not isinstance(value, bool):
        if isinstance(value, int):
            observation['number'] = value
        elif isinstance(value, str) and value.strip().isascii() and value.strip().isdecimal():
            # Bound conversion of untrusted strings; count values need no arbitrary precision.
            if len(value.strip()) <= 20:
                observation['number'] = int(value.strip())
    return observation


def _sample(result):
    result = _unwrap(result)
    retweet = _get(result, 'legacy', 'retweeted_status_result', 'result')
    if isinstance(retweet, dict):
        retweet = _unwrap(retweet)
        if isinstance(retweet.get('legacy'), dict) and isinstance(retweet.get('core'), dict):
            result = retweet
    user = _get(result, 'core', 'user_results', 'result')
    roots = {'R': result, 'U': user}
    return {'R': _type_tree(result), 'U': _type_tree(user),
            'paths': {path: _probe(roots, path) for path in _paths}}


def capture_first_page(data):
    """Called only for native page one. A failed write also consumes this process's attempt."""
    path = debug_capture_path()
    if not path:
        return
    global _attempted_pid
    with _lock:
        pid = os.getpid()
        if _attempted_pid == pid:
            return
        _attempted_pid = pid
        samples = []
        for result in _results(data):
            samples.append(_sample(result))
            if len(samples) == 5:
                break
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump({'results': samples}, output, ensure_ascii=True, allow_nan=False)
        except OSError as exc:
            # Do not log paths or exception details; they can contain private configuration.
            log.warning('X debug capture not written (%s); search continues', type(exc).__name__)

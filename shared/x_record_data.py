"""Backward-compatible optional X observations and credential-free raw data."""
from datetime import datetime, timezone
import logging
import re

log = logging.getLogger(__name__)
AUTHOR_COUNTS = ('followers_count', 'following_count', 'tweets_count', 'listed_count')
METRICS = ('likes', 'retweets', 'replies', 'quotes', 'views', 'bookmarks')
_secret = re.compile(r'cookie|token|password|secret|authorization|headers|proxy|^ct0$|^auth$', re.I)
_userinfo = re.compile(r'(https?://)[^/\s@]+@', re.I)


def sanitize(value):
    if isinstance(value, dict):
        return {key: sanitize(child) for key, child in value.items() if not _secret.search(str(key))}
    if isinstance(value, list):
        return [sanitize(child) for child in value]
    if isinstance(value, str):
        return _userinfo.sub(r'\1', value)
    return value


def utc_date(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError('date must be a string')
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        date = datetime.strptime(value, '%a %b %d %H:%M:%S %z %Y')
    return date.replace(tzinfo=date.tzinfo or timezone.utc).astimezone(timezone.utc).isoformat()


def optional_data(record, warnings=None):
    """Invalid optional values become NULL; warnings count field names, never raw values."""
    warnings = warnings if warnings is not None else {}
    def bad(path):
        warnings[path] = warnings.get(path, 0) + 1
        log.warning('Invalid optional X field %s; stored as NULL', path)
        return None
    def scalar(value, kind, path):
        if value is None:
            return None
        if kind == 'count':
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 9223372036854775807:
                return value
            if isinstance(value, str) and value.isascii() and value.isdecimal() and len(value) <= 19:
                number = int(value)
                if number <= 9223372036854775807:
                    return number
            return bad(path)
        if kind == 'date':
            try:
                return utc_date(value)
            except (TypeError, ValueError, OverflowError):
                return bad(path)
        return value if isinstance(value, kind) else bad(path)
    result = {}
    if 'schema_version' in record:
        version = record['schema_version']
        if not isinstance(version, int) or isinstance(version, bool) or version < 1:
            raise ValueError('schema_version must be a positive integer')
        result['schema_version'] = version
    if 'metrics_source' in record:
        result['metrics_source'] = scalar(record['metrics_source'], str, 'metrics_source')
    if 'observed_at' in record:
        result['observed_at'] = scalar(record['observed_at'], 'date', 'observed_at')
    specs = {
        'author': {**dict.fromkeys(('id', 'handle', 'name', 'bio', 'location'), str),
                   **dict.fromkeys(AUTHOR_COUNTS, 'count'), 'verified': bool, 'blue_verified': bool,
                   'account_created_at': 'date'},
        'metrics': dict.fromkeys(METRICS, 'count'),
        'parent': dict.fromkeys(('kind', 'conversation_id', 'in_reply_to_tweet_id', 'in_reply_to_user_id',
                                 'in_reply_to_handle', 'quoted_tweet_id'), str),
    }
    for section, fields in specs.items():
        if section not in record:
            continue
        source = record[section]
        if not isinstance(source, dict):
            bad(section)
            source = {}
        result[section] = {key: scalar(source.get(key), kind, f'{section}.{key}') for key, kind in fields.items()}
        if section == 'parent' and result[section]['kind'] not in (None, 'post', 'reply', 'quote'):
            result[section]['kind'] = bad('parent.kind')
    if warnings:
        result['field_warnings'] = dict(warnings)
    return sanitize(result)

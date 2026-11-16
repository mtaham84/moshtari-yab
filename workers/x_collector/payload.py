"""Existing GraphQL timeline traversal shared by capture and extraction. No transport calls."""

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


def raw_parts(result):
    result = _unwrap(result)
    retweet = _get(result, 'legacy', 'retweeted_status_result', 'result')
    if isinstance(retweet, dict):
        retweet = _unwrap(retweet)
        if isinstance(retweet.get('legacy'), dict) and isinstance(retweet.get('core'), dict):
            result = retweet
    return result, _get(result, 'core', 'user_results', 'result')

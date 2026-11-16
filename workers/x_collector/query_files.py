"""Read plain-text, JSONL or JSON-list overrides with optional seller ownership."""
import json
from pathlib import Path


def load_query_file(path: Path) -> list:
    if not path.is_file():
        return []
    text = path.read_text(encoding='utf-8')
    if text.lstrip().startswith('['):
        items = json.loads(text)
    else:
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
        items = [json.loads(line) if line.startswith('{') else line for line in lines]
    result, seen = [], set()
    for item in items:
        if not isinstance(item, (str, dict)) or (isinstance(item, dict) and not isinstance(item.get('query'), str)):
            raise ValueError('X override entries must be query strings or objects with a query string')
        key = json.dumps(item, sort_keys=True)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result

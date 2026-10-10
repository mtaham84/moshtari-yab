"""Patch twitter-cli 0.8.5 for X's 2026-09 web changes (applied at image build; fails the build if a patch misses).

1. x.com's logged-out homepage no longer carries the webpack map: fetch /i/jf/ instead (XClientTransaction PR #48),
   both for the x-client-transaction-id and for scanning JS bundles.
2. Query ids rotate (the bundled SearchTimeline id and the community twitter-openapi one are both stale):
   resolve live from X's own bundles first (also for the first try) and only then fall back to GitHub / constants.
"""
import pathlib
import twitter_cli.client as client
import twitter_cli.graphql as graphql

PATCHES = {
    client.__file__: [
        ('"https://x.com", headers=ct_headers', '"https://x.com/i/jf/", headers=ct_headers'),
        # resolve query ids live (from bundles) instead of trying the stale bundled id first and eating a 404
        ("query_id = _resolve_query_id(operation_name, prefer_fallback=True, url_fetch_fn=_url_fetch)",
         "query_id = _resolve_query_id(operation_name, prefer_fallback=False, url_fetch_fn=_url_fetch)"),
    ],
    graphql.__file__: [
        ('url_fetch_fn("https://x.com", {', 'url_fetch_fn("https://x.com/i/jf/", {'),
        ("""    if url_fetch_fn:
        github_query_id = _fetch_from_github(url_fetch_fn, operation_name)
        if github_query_id:
            _cached_query_ids[operation_name] = github_query_id
            return github_query_id

        _scan_bundles(url_fetch_fn)
        cached = _cached_query_ids.get(operation_name)
        if cached:
            return cached
""", """    if url_fetch_fn:
        _scan_bundles(url_fetch_fn)
        cached = _cached_query_ids.get(operation_name)
        if cached:
            return cached

        github_query_id = _fetch_from_github(url_fetch_fn, operation_name)
        if github_query_id:
            _cached_query_ids[operation_name] = github_query_id
            return github_query_id
"""),
    ],
}

for path, edits in PATCHES.items():
    p = pathlib.Path(path)
    text = p.read_text(encoding="utf-8")
    for old, new in edits:
        if old not in text and new not in text:
            raise SystemExit(f"twitter-cli patch does not apply to {p.name}: {old[:60]!r}")
        text = text.replace(old, new)
    p.write_text(text, encoding="utf-8")
    print(f"patched {p}")

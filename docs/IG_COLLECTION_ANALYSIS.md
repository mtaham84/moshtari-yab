# Instagram collection analysis

## X collector patterns to mirror

- client.py: subprocess/HTTP boundaries, typed error kinds, strict output parsing, secret-safe errors.
- worker.py: X-specific collection, query scheduling, billing, and ingestion. Shared SQLite, JSONL, retry, cooldown, circuit-breaker, and status helpers live under `shared/collector_kit/`.
- __main__.py: --once / --loop / --dry-run / isolated --mock, status-based exit codes.
- tests/test_x_collector.py and tests/fixtures/x_tweets.json: fake clients, temp SQLite/JSONL, no external services.
- cli_mapping.py: isolate external contract names so they can be revised without changing worker logic.

Both workers use `shared/records.py` for normalized records and `shared/collector_kit/` for the matching safety utilities. Telegram currently writes directly to PostgreSQL and does not emit these JSONL records.

## Backend contracts and verification

All backends implement read methods only. No follow, like, comment, DM, save, or other mutation call exists in the interface.

| Backend | Calls | Auth and error mapping | Verification |
|---|---|---|---|
| Mock | Fixture lookup for account posts, hashtag posts, and comments | No auth/network; fixture miss is empty data | Verified locally with fixture-driven tests |
| Instaloader | Profile.from_username(context, username).get_posts(); Hashtag.from_name(context, tag).get_posts(); Post.from_shortcode(context, shortcode).get_comments() | Public reads by default; optional existing session file. TooManyRequestsException -> rate_limit; login/credentials exceptions -> auth; connection exceptions -> transient; invalid/missing profile/argument -> fatal. Hashtag access **may require login**. | API names follow official API docs. Not runtime-verified: package is not installed in this environment. Session-file loading and current Instagram behavior remain UNVERIFIED here. |
| HikerAPI | GET /a2/user?username=... for user and first timeline page; GET /v2/hashtag/medias/recent?name=...&page_id=...; GET /v2/media/comments?id=...&page_id=.... x-access-key header only. | API key required. HTTP 429 -> rate_limit; 401/403 -> auth; timeout/408/5xx/network -> transient; other 4xx/malformed response -> fatal. Optional proxy is passed through urllib only when IG_PROXY_URL is set. | UNVERIFIED in this implementation until docs and response shapes are checked against a current provider contract. No live API call was made; subscription/entitlements, real account responses, and provider policy remain UNVERIFIED. |

Instaloader references: [module API](https://instaloader.github.io/as-module.html), [structures](https://instaloader.github.io/module/structures.html), [exceptions](https://instaloader.github.io/module/exceptions.html). HikerAPI routes and response fields are centralized in the mapping module and must be checked against the provider's current docs before live use.

## Records and downstream integration

Each UTF-8 JSONL line follows the requested schema: id (ig:<shortcode> or ig:<shortcode>:c<comment_id>), source=instagram, kind, original text, contactable author_handle, author fields, UTC created_at or null, post url, and metadata (backend, seed, parent post, hashtags, counts, media type, lexicon intent). Records lacking a usable author handle or stable shortcode are rejected before writing. The lexicon is only a recall-oriented prefilter; need_engine remains the final decision-maker.

This collector is deliberately not ingested into need_engine. A follow-up must generalize platform selection and independent chat IDs: need_engine/windowing.py, need_engine/extract.py, and need_engine/engine.py hard-code the x: prefix and X platform handling. Instagram should use an ig: prefix and platform-aware source readers/schema before any loader is enabled.

## Go / No-Go

**GO for local mock and carefully capped experiments. NO-GO for unattended production collection or automatic contact.** The collector is read-only and throttled, but Instagram terms and enforcement can restrict unauthorized automated collection. Use only data you are entitled to access, keep request rates low, and review legal/ToS obligations. For production, prefer Meta's official Graph API for an account the customer controls or a provider whose contractual terms meet the use case. Never use collected handles for automated outreach.

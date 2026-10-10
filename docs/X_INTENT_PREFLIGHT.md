# X pipeline preflight

## Baseline and branch

Work is on `feature/x-intent-quality`, created from `feature/x-need-engine` in `.worktrees/x-need-engine`. The repo-root worktree was not modified. Baseline `python -m pytest -q` could not collect because `psycopg` is not installed (`ModuleNotFoundError`). `python manage.py test apps` discovered 40 tests but could not create its test database: PostgreSQL at `127.0.0.1:5432` refused the connection. No baseline test passed or failed assertions.

## Findings from the code

### a) X stream, windows, and authors

`XMessageSource.fetch_after()` assigns every post `chat_id="x:public"`; it does not assign one chat per tweet. `NeedEngine.ingest()` persists those messages into `need_engine.pending` under that one chat and advances the independent `fetch_cursor_x` using the source `row_id`. `process()` reads that stream, clips it to `x_max_per_run`, and calls the common window builder. `is_ready()` uses the shared count/silence/wait triggers; `build_windows()` chunks X posts by `window_size`, suppresses previous-message context and reply parents for X, and renders a warning that posts are independent. Thus different authors can appear in the same extraction request/window, but are not conversational context. `extract_window()` validates each X need against current-window evidence and requires every cited message's author id to equal that need's author id. It rejects cross-author evidence.

Consequence: X batching can amortize one request over posts from multiple authors, provided the prompt uses an explicit independent-post contract and code validates evidence ownership. State identity still uses `(chat_id, author_id)`, so user needs do not merge across authors.

### b) Message states and safe prefilter drop

The engine stores fetched messages in `need_engine.pending`, moves processed messages to bounded `need_engine.recent` via `mark_analysed()`, and increments `messages_analysed`. Cursor advancement happens during ingestion before extraction. A prefilter drop must therefore be persisted as handled in the engine-owned schema and removed/marked atomically with the same batch, not left pending (which causes retries) and not implemented by rewinding `fetch_cursor_x` (which risks duplicates). Current schema has no per-post decision ledger. A new engine-owned table keyed by `(chat_id, message_id)` is the suitable place for drop decisions/false-negative auditing.

### c) Costs and batching

`LLMClient.complete_json()` records each call in `need_engine.costs` through `Store.add_cost(stage, model, tokens, cached, USD, toman, ref)`. Extraction currently calls once per `Window`; `extract_window()` splits usage cost among returned cards, while `RunReport.cost_messages_toman` accumulates whole-call cost and `analysed_messages` counts consumed messages. `Store.totals()` reports total costs divided by `messages_analysed`; CLI `stats` prints the grouped ledger and totals. Batching reduces ledger call count, not necessarily token cost linearly; per-need cost should be allocated by the number of input posts (or valid needs, explicitly reported) while run-level message cost remains total extraction cost divided by processed posts.

### d) Reply controls

`NE_WRITE_REPLIES` defaults true; `NE_REPLY_TOP_N` defaults to 3 and applies to the sorted matched products. `_finish_and_emit()` drafts replies for the first configured matches only when `reply_draft` is empty. `draft_reply()` uses `NE_REPLY_MODEL`, `REPLY_SYSTEM`, and `NE_PRODUCT_URL_TEMPLATE`; it strips URLs/mentions/phone numbers from generated text, then inserts or appends the product URL. There is no platform distinction in this path today, so X opportunities currently get the same Telegram-oriented reply format.

### e) Django bridge and reply storage

`engine_bridge.import_opportunity()` upserts the seller-scoped `Opportunity` by `(business, engine_opportunity_id)`, updates `AIAnalysis.suggested_reply` from the top matched product's `reply_draft`, and currently deletes/recreates `OpportunityProductMatch` rows. Sync is logically idempotent for opportunity/customer identity, but match rows churn on every import, so adding reply variants must preserve data without relying on row identity. The Django match model has no `reply_variants` field today.

### f) Existing X tests and safety boundaries

X coverage exists in `need_engine/tests/test_need_engine.py` (source row mapping, product query generation, buyer evidence gate, independent cursors, X context suppression), `workers/x_collector/tests/test_x_collector.py` (normalization, JSONL, dedup, caps, retries, cooldown, CLI errors, marketing filter and query generation), `x_ingest/tests/test_x_ingest.py` (JSONL contract), and `apps/discovery/tests.py` (X pipeline bridge/list/sync integration where present). Do not weaken those tests or Telegram tests. Existing collector-to-X access is read-only CLI search. Reply-link generation must remain an intent link only; no X write API or server-side sending.

## Verification limits

`twitter --help`, `agent-reach doctor`, actual X search operators/output, X intent URL behavior, and X character-weight rules are not verifiable in this environment. Keep these assumptions isolated/configurable and marked UNVERIFIED in code/docs.

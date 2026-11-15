# X collector: what to fix

Plan for making the X (Twitter) collector reliable and better at finding leads. Written on 2026-11-10 from a read of
the code (`workers/x_collector/`); nothing was run against X and there are no production numbers yet, so every item
says how it was verified. Update this file in the PR that fixes an item; delete it when the list is empty.

How the pipeline works: `docs/X_COLLECTOR.md`. Related: phase 13 of `docs/CLEANUP_ROADMAP.md` (splitting the
collector's large functions). This file is about
behavior; that phase is about structure. Fix behavior first, with tests, then refactor.

## How it works today

1. `query_source.collector_queries` builds search queries per product family (short phrases from the product, the
   LLM-written terms and queries learned from confirmed buyers), merged across sellers and capped per seller.
2. `SeenStore.schedule` picks 12 queries per cycle: never-run queries first, then by staleness, priority, kind and
   verified leads per completed search (queries that keep producing non-buyers are dropped).
3. `XCliClient.search_top_past_week` searches the Top tab, Persian only, last 7 days, 20 posts per page, up to 10
   pages, until 40 valid posts.
4. The worker drops posts it has already seen, writes the rest to JSONL, and `x_ingest` loads them into the
   database, where need_engine filters them and asks the LLM which are real buying needs.

Decided (2026-11-10): the search stays on the **Top** tab. In the owner's experience Top gives better leads than
Latest, because X itself removes much of the spam and advertising. Top also means the 7-day window stays: a post
needs time to collect engagement before it shows up there, so a "since the last run" window would miss it.

## Fixes, in order

### 1. Errors must not look like "no results" — fixed in Phase 1

See the Phase 1 delivery table below for behavior and regression tests. Auth is now its own `auth` kind.

### 2. X search works for one seller only — fixed in Phase 1

The dental-only eligibility filter is removed. Funded sellers with X search enabled are eligible, with an optional
rollout allowlist. Built-in dental phrases remain outside this phase.

### 3. Posts seen on earlier days use up the 40-post target

- Today: the client is told only the ids written today (`worker.py:334`). A post collected yesterday is "valid"
  again, counts toward the 40, and is thrown away afterwards by the worker. After a query's first day most of its
  40 are old, so the search stops before it reaches the new posts further down.
- Fix: the client asks the seen store (and the crawled-post table) while paging, so known posts do not count toward
  the target and a page of only-known posts counts as an empty page.
- Effect: more new posts per query for the same number of requests, and exhausted queries stop after two pages.
  This lowers the load on the accounts; it does not raise any limit.
- Verified: read in code. Measure before and after: new posts per request, per query.

### 4. Decide between exact phrases and loose matching

- Today: the generator quotes every phrase (`intent_queries.py:413`), then the client removes all quotes before
  searching (`client.py:109`). One of the two is a leftover.
- Fix: run both forms on the same queries for a fixed period, compare leads per request, keep one and delete the
  other code path.

### 5. Account cookies in process-wide environment variables — fixed in Phase 1

Cookies stay in native constructor arguments or the CLI subprocess's private environment. See tests below.

### 6. One post is judged once per product of the family

- Today: a query belongs to a family, and every post it finds is stored once per product of that family
  (`worker.py:499`). The engine then asks the LLM about the post for each product separately, and the cache key
  includes the product id (`need_engine/extract.py:287`). A family of ten products pays for ten judgments of the
  same post, and the seller can get the same person as several opportunities.
- Fix (needs a design decision): judge a post once per family against the family's products together, and return
  the products that fit.
- Verified: read in code. How much it costs depends on family sizes in production; measure before changing.

### 7. Measure the funnel per query

- Needed before any change to volume: per query and per seller, posts fetched, new posts, passed the pre-filter,
  became an opportunity, marked relevant by the seller. The data exists (`XQueryDaily`, `x_filter_decisions`,
  `x_hit_results`, opportunity feedback); it is not shown together.

## Later, only after 1-3 and with numbers from 7

- Replies under high-intent posts ("where did you buy it?"). `X_THREAD_COLLECT` exists as a setting but the worker
  only logs a warning (`worker.py:272`).
- Latest tab as an experiment on a few of the strongest buying phrases, next to Top, compared by cost per real lead.
- More queries per seller or per cycle.

Each of these changes how often an account talks to X, so each needs the owner's explicit yes (`AGENTS.md`, Domain
notes).

## Not looked at

- The engine side of X: the pre-filter rules, the LLM batch extraction and reply drafts.
- The query-learning code (`proqe.py`) beyond its outline.
- The account pool's limits and cooldown values.

## Phase 1 delivery (2026-11-15, branch `taha3`)

Only the critical fixes and credit eligibility in this phase are implemented. There are no author/analysis tables,
comment collection, prompt changes, changes to wallet charging, or changes to account rate limits and delays.

| Item | Files changed | Behavior and regression tests |
|---|---|---|
| 1. Outcomes | `need_engine/engine.py`, `need_engine/tests/test_x_hit_outcomes.py` | A missing need is `not_customer`, even with a default/cached `fit="no"`. Extracted needs without an emitted opportunity are `not_fit`; emitted opportunities are `customer`. `test_product_hit_outcomes_including_cache` covers all three, `partly` below threshold, and fresh/cache paths. ProQE rewards/penalties are unchanged. |
| 2. Ingest | `x_ingest/__main__.py`, `x_ingest/tests/test_x_ingest.py` | One savepoint per record covers all its inserts. A PostgreSQL error rolls back that record, increments the skipped count and logs the line number. `test_bad_database_record_rolls_back_only_that_record` imports valid/overflow/valid numeric IDs with a real PostgreSQL transaction; `test_panel_insert_failure_rolls_back_record_and_keeps_neighbors` verifies a late panel insert rolls back the earlier crawler inserts too. |
| 3. Daily counters | `workers/x_collector/account_pool.py`, `tests/test_account_pool.py` in that package | Refresh persists a UTC reset; success uses a SQL date guard so the first action after midnight starts at one. `test_daily_counter_resets_in_database_at_utc_midnight` verifies availability, persistence and the next refresh. |
| 4. Search errors | `client.py`, `worker.py`, `cycle_support.py`, `tests/test_search_errors.py`, updated collector test fixtures | Errors propagate as `XCliError`; auth challenges/rotates, rate limit cools/rotates, transient errors retry, fatal errors stop. `test_native_errors_are_explicit_and_do_not_fall_back`, `test_cli_error_is_not_an_empty_search`, `test_worker_rotates_auth_and_rate_limited_accounts_then_handles_empty_and_good`. `search_errors` distinguishes failures from genuine empty successes. |
| 5. Dedup | `shared/x_dedup.py`, `worker.py`, `x_ingest/__main__.py`, `apps/discovery/models/x_twitter.py`, discovery migration `0044`, `tests/test_product_dedup.py` | Hashes include product ID; the model uniquely constrains `(tweet_id, product_id)`. Existing generic hashes are retained. `test_ingest_deduplicates_per_product_and_preserves_legacy_hash`, `test_native_search_keeps_same_tweet_for_two_products` cover both database and native pagination suppression. |
| 6. Promotions | `shared/x_filters.py`, `worker.py`, `tests/test_promotional_filter.py` | Generic buying/discount/link phrases are removed from hard rules. `test_buyer_sentences_are_not_promotional` and `test_explicit_advertisements_are_promotional` each have ten cases; `test_worker_counts_dropped_promotional_posts` verifies counters. Drops are logged/counted, not stored. |
| 7. Credit eligibility | `query_source.py`, `balance_gate.py`, `config.py`, `need_engine/registry.py`, `.env.example`, `tests/test_balance_queries.py` | All enabled products of funded sellers are eligible; no dental-only default. Tests cover funded non-dental, zero, negative, enforced minimum, failure, stale snapshot after failure and allowlist. A failed registry read invalidates wallet freshness. |
| 8. Cookies | `client.py`, `native_session.py`, `tests/test_cookie_isolation.py` | Cookies are constructor arguments or subprocess-only environment values. The native transport is scoped under a lock, restores its previous session, and applies each account's proxy directly. `test_two_accounts_do_not_change_process_environment` covers native and CLI searches with two accounts. |
| 9. Proxy redaction | `shared/tele_logging/formatter.py`, `client.py`, `account_pool.py`, `x_ingest/__main__.py`, `tests/test_proxy_redaction.py` | `mask_proxy` removes all userinfo; logs, pool status and nested stored search stats are redacted. Tests: `test_mask_proxy`, `test_search_logs_and_stats_hide_proxy_credentials`, `test_search_run_storage_redacts_stats`. |
| 10. Run guard/resume | `worker.py`, `cycle_support.py`, `query_source.py`, `query_files.py`, `balance_gate.py`, `config.py`, both shared state-store schedulers | Fresh wallet reads before every attempt (TTL zero). Shared query metadata retains funded owners/products only. Read-only scheduling prevents history or penalties for balance skips. Tests in `test_balance_run.py`: exhaustion, top-up, shared owners, missing wallets on/off, mid-run failure, owned/unowned static files and direct static entries. `test_read_only_schedule_does_not_register_skipped_queries` covers SQLite and PostgreSQL. |

### Shared-query delivery follow-up

A shared X query still runs once for its funded owners, and automatic query generation remains per seller/category
family (first three category levels), not per individual categorized product. Product-level fit judging is separate.
The existing category/family tests cover grouping, collapsing a manual fourth level and merging identical seller
queries. Products with no category retain their existing single-product fallback; classification uses the configured
model when needed after deterministic matching, rather than a model call on every product submission.

`apps/discovery/engine_delivery.py` adds a fresh, read-only eligibility check inside `engine_bridge.import_opportunity`.
It reuses the billing balance rule and locks eligible wallet rows until the panel transaction commits. A result made
while funded cannot create a new panel lead/customer after the owner reaches zero/negative balance; other funded
owners of that shared result still receive theirs. Missing wallets and enforced-minimum balances are blocked too.
The existing X import test now explicitly funds its seller. Billing debits and non-X imports are unchanged.

`apps/discovery/test_x_delivery_credit.py` covers shared funded/unfunded delivery, balance drained after analysis,
missing wallet without auto-creation, enforced minimum on/off, top-up recovery, preservation/closure of existing
leads, and an actual concurrent PostgreSQL debit that must wait for the delivery transaction's wallet lock.

### Assumptions and reproduction limits

- The README already uses `python -m x_ingest --path ...`. There is no `DATABASE_SCHEMA.sql` in this checkout and
  no `not_buyer`/`misfit` schema definition to update. The outcome names are documented in `docs/X_COLLECTOR.md`.
- A need below the opportunity threshold is `not_fit`; no extracted need is `not_customer`. Cache contents do not
  need rewriting because classification uses the extracted need rather than the truthiness of the cached fit.
- Legacy unscoped hashes stay in the database with `product_id=""`. They do not suppress a product-specific hit;
  an old tweet may therefore be re-collected once for an enabled product. The retention window is unchanged.
- Static overrides accept text, JSONL objects, or JSON lists. Owned entries use the gate; unowned entries explicitly
  bypass it with a warning. Generated and registry entries carry product-to-business ownership. Legacy custom
  multi-owner entries without that map gate owners but retain their supplied product IDs; the engine still gates
  each seller before analysis. Supply `product_business_ids` to filter those product IDs at collection time too.
- X/CLI network responses are faked at the transport boundary. Native tests replace the optional twitter-cli
  module to avoid X requests; PostgreSQL behavior, Django models/migrations, and state stores run for real on a
  throw-away test database. No live X search, real wallet debit or production migration is part of verification.
- Local verification uses a separate checkout with no developer `.env`, PostgreSQL 16 and pgvector. An early run
  of existing tests in the original checkout loaded local account settings and changed local account counters or
  statuses before isolation. Previous local values were not available to safely restore. The production server
  was not accessed. Collector tests now require an explicit DSN before connecting the account pool.
- Remaining work outside Phase 1: historical seen-post suppression across days, phrase quoting experiments,
  one LLM judgment per family, funnel reporting, built-in dental phrase data, and any later collection features.
  Items 3, 4, 6 and 7 above remain proposals, not implemented behavior. The PostgreSQL collector-state backend now supports the query feedback/evaluation methods used by X;
  the SQLite collector default is unchanged. Both backends exercise yield allocation and unique-lead credit.

### Deploy checklist (requires a reviewed merge first)

1. Discovery migration `0044_xcrawledpost_product_id_alter_xcrawledpost_tweet_id_and_more.py` is reviewed and
   tested. It adds `product_id`, removes tweet-only uniqueness, and adds `(tweet_id, product_id)` uniqueness.
   The web entrypoint applies pending migrations automatically. Compose starts X only after web is healthy;
   `test_x_startup_waits_for_migrated_web_service` checks the resolved startup graph. Stop an already-running X
   writer before updating the deployment, then deploy matching web/collector/ingest code together. Startup
   dependencies do not stop a writer that is already running. No data migration rewrites old hashes.
   A rollback to tweet-only uniqueness needs duplicate tweet rows consolidated first; do not reverse blindly.
2. No new environment setting is required: an unset `X_SELLER_ALLOWLIST` permits every funded seller, and
   `X_REQUIRE_WALLET` defaults to true. Set an allowlist only for staged rollout, or disable the wallet requirement
   only when deliberately allowing legacy businesses without wallet rows. Check funded
   businesses have wallet rows and that read failures generate an ops alert.
3. Inspect existing `x_accounts` counters. The code automatically resets rows whose last action is before today
   UTC (including null timestamps). A manual reset is optional, only to restore availability before the next UTC
   day when today's count is confirmed inflated. Do not blindly clear legitimate current-day usage; preserve
   lifetime counts and account status. The automatic daily reset needs no operator action.
4. Removing the dental eligibility filter increases query volume, LLM use and X account use in proportion to the
   number of funded tenants, within existing global/per-account caps. Start with an allowlist and observe costs
   and account health. Rate limits, daily caps, delays and anti-ban settings were not raised.
5. Restart the X collector after the migration, then check run stats, masked proxies, funded sellers' search logs,
   and that topping up a blocked seller resumes query eligibility on the next cycle. Do not add a paused flag.

### Category and continuous-allocation follow-up

- Product organization cannot create categories from `new_category` model output. Existing common taxonomy IDs
  are reused across products and sellers; category-family query generation and shared-query merging remain.
- X allocation uses unique verified query/tweet leads divided by completed searches. Failed requests are excluded;
  fixed 20%/5% yields allocate 80/20 in 100 attempts. A 5% exploration reserve prevents new queries starving.
  Allocation credits persist across cycles; no query is repeated within one cycle. Caps and account delays remain.
- Dry-run uses the same weights with read-only state access. The continuous loop retries temporary reload/DB
  errors and continues after empty successful searches. PostgreSQL feedback/evaluation now matches the X API.
- Existing lead counters are preserved, so historically duplicated rewards can initially bias weights. The new
  per-query/tweet ledger prevents future duplication; no blind historical reset is performed.
- Live X return volume has not been verified: all seven locally configured eligible account proxies failed the
  connectivity check, so no live X search was sent. Fix proxy connectivity before evaluating real results.
  The local engine also has `NE_X_ENABLED=false`; enable it in the intended deployment for X analysis after review.
  No production settings, account limits or deployment were changed by this follow-up.

Follow-up verification (2026-11-15): the complete local PostgreSQL 16 + pgvector suite passed with
933 tests, 57 subtests and 161 warnings, without skips. The allocation/automation regression run passed
18 tests, including SQLite and PostgreSQL yield, deduplication, dropped-query exclusion and read-only preview.
GitHub Actions run 381 also passed all 933 tests plus lint/baseline and commit checks. Live X result volume
remains unverified for the proxy-connectivity reason above.

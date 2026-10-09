# X Growth Preflight

## Branch and baseline

Worktree: .worktrees/x-need-engine; branch: feature/x-growth, based on feature/x-intent-quality. Repository-root checkout is not modified. Discovery migrations are through 0011_opportunityproductmatch_reply_variants; business migrations through 0006_engine_cleanup.

Baseline python -m pytest -q did not collect tests because psycopg is missing. python manage.py test apps found 40 tests but could not create its PostgreSQL test DB; 127.0.0.1:5432 refused connections. No baseline assertions passed.

## Findings

### a) X chat identity and contexts
XMessageSource assigns all tweets chat_id=x:public. Store groups pending rows by this key; process() sends them through build_windows() and is_ready(). X windows omit Telegram recent context and reply parents, and extraction requires evidence messages to have the need author's ID. Posts from different authors can share a request/window but are not conversational context. Reply/thread context is absent; collection must be a separate path.

### b) Ordering/caps
_ingest_source() requests X rows by row_id ASC with limit=x_max_per_run; process() slices pending rows with pending[:x_max_per_run], oldest first. Rows past the processing cap remain pending, while ingest cursor advances only over fetched rows. Pending can grow if processing is delayed; there is no explicit backlog limit.

### c) created_at
XMessageSource uses parse_dt(created_at or datetime.fromtimestamp(0,...)); NULL becomes 1970-01-01 UTC. This skews freshness, makes the post extremely stale, and can surface the epoch in the panel.

### d) TTL and priority
NEED_TTL_DAYS defaults to 7. NeedEngine._opportunity() sets expiry from n.updated_at + need_ttl_days; Store.expire_needs() sweeps by persisted need updated_at. NeedOut.priority is round(STRENGTH_WEIGHT[strength] * best_match_score, 3); no freshness decay exists. _close() emits resolved/expired changes. README describes the seller-contacted exception, but engine state itself has no contact status.

### e) Django data
Opportunity holds source platform/message ID/timestamp, status, expiry, and related Evidence. Customer stores a business-scoped source identity, username/profile URL and metadata. ProductOrder optionally references Opportunity through product-card ?ref= conversion. Product matches belong to seller opportunities. MonitoredCommunity is Telegram-only. Business maps one-to-one to the authenticated user.

### f) Panel conventions
Panel views use login_required, derive business from request.user.business, and scope data by that business. Mutations use POST/CSRF. URL names are under discovery:. Persian/RTL UI uses p-card, p-btn, p-badge; Jalali helpers are in apps/core/jalali.py.

### g) Bridge
sync_opportunities reads need_engine.opportunities after EngineSyncCursor sequence and calls import_opportunity(). Bridge splits by matched-product seller and upserts opportunity identity and match rows. Cursor updates make import incremental.

### h) Deployment
Docker services web, sync, engine, crawler, and profile-gated x share appdata at /app/data; x also mounts xdata at /app/output. A status file under /app/data/x_collected is shareable. sync imports engine opportunities; there is no x status-sync service.

### i) Telegram identities
crawler.tg_users includes user_id, username, and usernames array. Cross-platform links must be explicit self-declarations, never fuzzy matches.

### j) Previous-task dependencies
Present: SQLite query_runs; PostgreSQL x_filter_decisions and summary; x-prefilter-report CLI; NEED_SYSTEM_X; reply_variants in schema, bridge, panel and migration 0011. Not fully implemented/tested: false-negative opportunity linking and durable per-run funnel details.

## Unverified
Actual Agent Reach CLI response/flags, search operators, relative-time behavior, X intent-link behavior, and thread reply/quote CLI contract. Live collection and thread retrieval remain NO-GO until supervised verification and ToS review.

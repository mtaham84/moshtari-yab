# Refactor roadmap

The single source of truth for the cleanup/refactor of this repository. Any session (human or AI) working on the
refactor reads this first and updates it in the same PR that changes the status: tick the box, update "Current
status", and add anything learned to "Known issues".

Goal: make the codebase small, understandable and safe to change, without a rewrite and without stopping production.
Each step ships as a small PR to `main` from a short-lived `<type>/<description>` branch (see `AGENTS.md`), with CI
green, before the next one starts. Behavior must not change unless the step says so.

## Current status (2026-11-09)

- Phase 0 is done: AGENTS.md, skills, git conventions (PR #22), and CI guardrails (`ci/guardrail-checks`): file size
  and broad-except baseline, import-boundary test, commit message check, pinned ruff, pre-commit.
- Phase 1 is done (#26, #27, #28, plus the outreach tie-break fix #29 it uncovered).
- Phase 2 is done (#30-#34): 0 unreachable modules remain; one Instagram stack.
- Phase 3 is done (`refactor/one-instagram-stack`): the scraper and `ig_accounts.py` are split into focused modules,
  proxy handling has one owner (`apps/discovery/proxies/`), and every instagrapi client comes from one factory.
- Phase 4 is done as four PRs (#36 views/services, #37 templates, #38 CSS, #39 outreach/ops bot): of the 15 files
  over 800 lines only `apps/discovery/models/core.py` (848) is left, on purpose.
- Phase 5 is done (#40 bugs, #41 silent excepts, #42 boundaries, #43 helpers): broad excepts in the baseline
  328 → 70, no `except Exception: pass` left, every remaining broad catch at a boundary logs its traceback.
- **Next step:** Phase 6, step 1: run the audit below and write its report into this file. No code changes until the
  report is reviewed.
- Open PRs and other branches are out of scope for this plan.

## Baseline (main @ ada0d7f, 2026-11-08) - measure progress against this

| Metric | Value |
|---|---|
| Python lines (no migrations) | 67,170 |
| HTML template lines | 16,473 |
| CSS lines | 7,225 |
| Files over 800 lines (`.py`/`.html`/`.css`) | 23 |
| Broad `except Exception` blocks (non-test, grep) | 417 |
| Broad-except hits, ruff `BLE001` + `S110` (`guardrail_baseline.json`) | 391 in 89 files |
| Forbidden imports (`ALLOWED` in `tests/test_architecture.py`) | 13 |

Largest files: `templates/ops/manage_dashboard.html` 4,369; `static/css/site.css` 3,332; `static/css/panel.css`
2,737; `apps/billing/manage_views.py` 2,213; `apps/discovery/management/commands/run_instagram_scraper.py` 2,034;
`templates/accounts/outreach.html` 1,714; `apps/discovery/ig_accounts.py` 1,441;
`apps/discovery/outreach_service.py` 1,370.

## Phase 0: Guardrails for AI-assisted development

The mess came from the process, not from one bad decision. Fix the process first, or the cleanup will be undone.

- [x] `AGENTS.md`: what runs, real commands, rules against duplication, dead code, giant files and silent errors;
      definition of done; git workflow. `CLAUDE.md` imports it.
- [x] Skills in `.claude/skills/`: `systematic-debugging`, `test-driven-development`,
      `verification-before-completion` (from obra/superpowers) and `removing-dead-code` (project-specific).
- [x] Git conventions: `<type>/<description>` branches, Conventional Commits, no AI attribution
      (`.claude/settings.json` turns off Claude Code's own).
- [x] `scripts/compare_and_merge_main.sh` no longer defaults to a personal branch.
- [x] CI checks (agents ignore text rules more often than failing checks):
  - [x] `scripts/check_baseline.py` + `scripts/guardrail_baseline.json`: no new file over 800 lines, no existing big
        file grows, no file gains broad excepts (ruff `BLE001`, `S110`); `--update` only lowers entries
  - [x] `tests/test_architecture.py`: AST import-boundary test (rule 7 in `AGENTS.md`), 13 existing violations in
        `ALLOWED`, which may only shrink
  - [x] `scripts/check_commits.py` (PRs only): Conventional Commits subjects, no AI attribution
  - [x] ruff pinned to one version (`RUFF_VERSION` in CI, `rev` in pre-commit)
- [x] `.pre-commit-config.yaml`: ruff + guardrail baseline before every commit.
- Working habits: one task per agent session and per branch; ask for a plan first for anything over ~50 lines; read
  the diff before committing; when an agent repeats a mistake, add one line to `AGENTS.md` or a CI check.

## Phase 1: Repository hygiene

- [x] Deleted the outdated `docs/architecture/` and 5 point-in-time analysis files (`IG_/X_COLLECTION_ANALYSIS`,
      `X_GROWTH/INTENT_PREFLIGHT`, `X_NEED_ENGINE_ANALYSIS`). `scripts/import_graph.py` and
      `scripts/config_inventory.py` now print to stdout. Kept: `INSTAGRAM_SESSIONS.md`, `SECURITY_CLEANUP.md` (until the
      leaked proxy links are confirmed rotated) and the business docs.
- [x] One test command: `python -m pytest` runs everything (pytest-django runs the Django suites; the custom Django
      runner moved into `conftest.py`). This also picked up `shared/tele_logging/tests.py` (18 tests), which neither
      runner collected before.

## Phase 2: Live map and dead code removal

Decisions (2026-11-09): the Telegram ops bot and `workers/channel_config_collector.py` run under systemd and stay.
The live Instagram stack (`run_instagram_scraper` + `instagram_engine/analyzer.py`, LLM judging, leads in the DB) is
kept; the experimental JSONL pipeline (`workers/ig_collector/`, `instagram_discovery/`) is removed. Starting point:
267 modules, 42 unreachable (8,006 lines).

- [x] `scripts/find_unreachable.py`: modules no entry point reaches (compose, entrypoint, Django/Celery autoload,
      `MANUAL_ROOTS` for systemd/manual tools), used as step 1 of the `removing-dead-code` skill.
- [x] Dead code: `scripts/legacy/`, `xray/proxy_switcher.py`, the old standalone SQLAlchemy engine in
      `instagram_engine/` (`main`, `scraper_worker`, `outreach_worker`, `session_manager`, `circuit_breaker`, `models`,
      `requirements.txt`; it could not even import in the app image), `apps/accounts/emails.py`,
      `need_engine/x_query_scoring.py`. `instagram_engine/` now holds only what the live scraper uses: `analyzer`,
      `post_filters`, `config`.
- [x] Unused tools: one-off management commands (`seed_demo_businesses`, `check_outreach_health`,
      `instagram_backfill`, `purge_x_contacts`, `purge_x_queries`, `regenerate_platform_queries`),
      `workers/state_migrate.py`, `scripts/profile_engine.py`, `scripts/profile_panel.py`. Kept as manual tools:
      `charge_usage`, `rotate_provider_keys`, `seed_global_sources`, `scripts/check_proxy_leaks.py`.
- [x] Experimental Instagram pipeline: `workers/ig_collector/`, `instagram_discovery/`,
      `shared/instagram_manual_post.py`, the `ig_ingest` and `ig_sync_seeds` commands, the post-review page and the
      comment/post report APIs, the unused report loading in the pages view, its JSONL retention step, its always-down
      "Instagram collector" status row, `IG_*` collector settings and data files. `find_unreachable.py` now reports 0
      unreachable modules.
- [x] Unused `.env.example` variables (16) and unused images (7). `scripts/config_inventory.py` still reports
      `INSTAGRAM_SESSION_*`/`INSTAGRAM_NETWORK_*` (read through an `os.environ.get` alias), `HTTPS_PROXY`/`NO_PROXY`
      (read by libraries) and `XRAY_RELOAD_MIN_SECONDS`/`XRAY_WATCH_POLL_SECONDS` (read by `xray/watch.sh`) as
      unused; they are false alarms and stay.
- Later (Phase 5 candidate): lead-quality measurement (human labels, precision) rebuilt on the live DB data; only the
  removed experimental pipeline had it.

## Phase 3: One Instagram stack

- [x] Owner: `instagram_engine/` plus the Instagram code in `apps/discovery/` (the experimental pipeline was removed in
      Phase 2).
- [x] Removed the scraper's manual one-off mode (`--query`, `--hashtags`, `--tenant`, about 750 lines): production only
      runs the family path. `--all-businesses` and `--max-posts` had no effect and were dropped from the command and
      `docker-compose.yml`. Helpers only it used went too (`ig_query_scoring`, LLM CTA extraction, ...).
- [x] `run_instagram_scraper.py` (2,034 lines) is a 93-line command over `apps/discovery/ig_scraper/` (`clients`,
      `session`, `accounts`, `sources`, `comments`, `cycle`, sharing one `Run` object). The opportunities view no
      longer instantiates the management command.
- [x] `apps/discovery/ig_accounts.py` (1,441 lines) keeps only add/edit/pause/delete/check (234 lines). Login with
      codes: `ig_login`; clients: `ig_client`; status and timeline: `ig_state`; outreach tenants: `tenants`.
- [x] One proxy owner, `apps/discovery/proxies/`: `urls` (which proxy, display, validation), `health` (reachability,
      `measure_config`, egress IP), `rotation` (mobile IP rotation, was `proxy_service.py`), `assignment` (sticky
      configs, healing, pruning; moved out of `ig_accounts` and `xray/`), `export` (link/QR, IP check). The manage
      panel's own TCP check and config ping were duplicates and now use `health`. `xray/` keeps only xray work.
- [x] Every instagrapi client is built by `ig_client.new_client` (6 hand-built copies replaced; their delays and
      timeouts unchanged). `tests/test_architecture.py` fails when code outside `ig_client` imports `instagrapi.Client`.
- [x] `check_baseline.py --update --moved-from <file>`: moved code takes its broad-except entries along, never more.

## Phase 4: Split the largest files

Split by responsibility, tests first, no behavior change. Each PR was checked with a render diff (every URL as anon,
staff and seller on fixed data, compared with main) and, for JS and CSS, in headless Chromium.

- [x] PR A: `apps/billing/manage_views.py` (2,153) → `apps/billing/manage/` (one module per area; the 640-line
      dashboard view is built from one function per section, pinned by `test_manage_dashboard_context.py`).
      Outreach views → `accounts/outreach_views.py`, opportunity export → `discovery/views/export.py`,
      `billing/services.py` → `services` (wallets) + `reports`, `ops_status`, `x_stats`, `engine_tables`.
- [x] PR B: `manage_dashboard.html` (4,369) → 370 lines + one partial per tab (`templates/ops/manage/`). Inline JS/CSS
      of six templates → `static/js`, `static/css`; template values reach the scripts through `data-*` attributes on
      the `<script>` tag (`document.currentScript.dataset`). `outreach.html`, `opportunities_list.html` → partials.
- [x] PR C: `site.css` / `panel.css` → `site-*.css` / `panel-*.css` along their sections, linked in the same order
      (byte-identical when concatenated; flat in `static/css/` for the relative `url()`).
- [x] PR D: the unused direct DM path in `outreach_service` removed; the rest → `apps/discovery/outreach/` (limits,
      messages, recipients, journey, replies, outbox). The ops bot's code → `apps/core/ops_bot/`;
      `workers/telegram_config_bot.py` is only the systemd entry point.
- Left: `apps/discovery/models/core.py` (848; the `Opportunity` model alone is 412 lines).

## Phase 5: Error handling and bugs

- [x] PR 5.1: the known bugs, each with a test that failed on main (page retirement, challenge handling, DM guard,
      business priority).
- [x] PR 5.2: every silent `except Exception: pass` (40) catches what it expects or logs the traceback.
- [x] PR 5.3: worker loops, commands, tasks, the crawler monitor, the X collector, the ops bot and the views (55)
      log the traceback at the level they already used; expected conditions are caught exactly and not logged.
- [x] PR 5.4: the biggest helper files narrowed (parsing → TypeError/ValueError, files → OSError, optional Django
      imports → ImportError/ImproperlyConfigured/AppRegistryNotReady/RuntimeError, ...). Instagram code only gained
      tracebacks; handlers whose message is redacted (login, session) keep it and get no traceback.
- Rule used throughout: keep each log call's level and add `exc_info=True`. Telegram alerts take ERROR and above, so
  their number did not change. `log.exception` (ERROR) would have flooded the ops chat.
- Left: 70 broad catches in small files (`scripts/guardrail_baseline.json`), to narrow when those files are touched.

## Phase 6: Audit, then plan

Phases 0-5 removed what was dead, duplicated or too big by file. What is left is harder to see: functions nobody
can follow, a data model that will be expensive to change later, and paths with no tests. This phase first finds
and ranks those problems (the audit), then turns the ranked list into phases 7+ (the plan). The audit changes no code.

- [ ] Step 1: audit, one track group per session, results written under "Audit report" below.
  - [x] Session 1: B data model, C migrations (production numbers still to come, see "Waiting for")
  - [x] Session 2: D tenant isolation
  - [x] Session 3: A complex functions
  - [ ] Session 4: E duplication, F coupling
  - [ ] Session 5: G tests, H panel queries
  - [ ] Session 6: I config and deploy
- [ ] Step 2: the user reviews the report and answers its open decisions.
- [ ] Step 3: phases 7+ are written from the accepted findings; guardrails for them are added first.

### Rules for the audit

- Read-only: no fixes, no renames, no "while I am here". A bug found on the way is a finding.
- Every finding has evidence someone else can reproduce: `file:line`, the command that found it, the number.
  "This looks complex" is not a finding; "`import_opportunity` has 82 branches in 151 lines" is.
- Verify before reporting: read the code, grep the callers, check that the path runs in production (the table in
  `AGENTS.md`). Mark each finding CONFIRMED (read and traced) or SUSPECTED (seen, not traced).
- Every finding says what goes wrong later if it stays (wrong data, a blocked feature, a change nobody dares make).
  No future cost means no finding. Style and naming are out of scope.
- Say what was not checked. A track that was only sampled says which part.
- Database findings need production numbers (row counts, violations of the rule to be added). The agent writes the
  read-only queries; the user runs them on the server and pastes the result.

### What to look for

Critical paths, where a mistake costs money, an account or a customer's data. Findings on them rank higher:
opportunity import (`engine_bridge`), wallet charging (`billing/services`), DM dispatch and limits
(`outreach_engine`, `outreach/`), the Instagram scraper cycle, tenant scoping in views and exports, retention.

| Track | Look for | How to find it |
|---|---|---|
| A. Complex functions | More than 15 branches, 50 lines or 4 levels of nesting; more than 7 parameters; a function that does several stages in one body; `if platform == ...` chains repeated across files | `ruff check --select C901,PLR0912,PLR0913,PLR0915 --statistics .`, then read the top 40 |
| B. Data model | Links kept as strings or inside JSON instead of a foreign key (`tenant_id`, `trace_metadata` ids, account usernames); JSON keys that code filters or branches on; one table holding several things; missing unique or check constraints behind "find, else create" code; the same fact in two tables; tables that only grow; redundant indexes | Read every model and both raw schemas (`telegram_crawler/schema.sql`, `need_engine/store.py`); grep `get_or_create`, `.filter(...).first()` followed by a create, `trace_metadata`, `__contains` |
| C. Migrations | Schema changes outside Django migrations; migrations that lock or rewrite a large table; data migrations without a reverse; model changes with no migration | `python manage.py makemigrations --check --dry-run`; read the migrations of the last 3 months |
| D. Tenant isolation | A view, export or API that loads an object by id without filtering by the user's business; operator-only pages reachable by sellers | List every URL with its view and decorators; for each view taking an id, trace the query |
| E. Duplication | Two implementations of one concept (LLM client, cost ledger, crawl de-duplication, account models, proxy lists); copied blocks | Grep by concept, not by name; compare modules with similar names across `apps/`, `need_engine/`, `workers/`, `shared/` |
| F. Coupling | The `ALLOWED` list in `tests/test_architecture.py`; imports inside functions that exist to avoid a cycle; modules imported by most of the codebase; views with business logic; models with presentation code | `python scripts/import_graph.py`; grep indented `from apps` imports |
| G. Tests | Critical paths with low or mock-only coverage; tests that mock the function they test; no test for cross-tenant access | `python -m pytest --cov` (ask before adding `pytest-cov`); read the tests of each critical path |
| H. Panel queries | Queries inside loops or model properties used in templates; counts over whole tables on every page load; lists without pagination | Read each list view and its template; confirm with `assertNumQueries` or the query log on fixed data |
| I. Config and deploy | Environment reads outside `config.py`/settings; state kept in process memory or local files that breaks with two processes; anything deployed that is not in git | `python scripts/config_inventory.py`; grep module-level dicts, `threading.Thread`, `cache.` |

### Ranking

Each finding gets three scores from 1 to 3, with one line of reasoning each:

- Impact: what breaks and for whom (3 = money, bans, customer data or a critical path).
- Growth: does it get more expensive to fix with time (3 = every new row or feature adds cost, as with data model
  problems; 1 = it stays the same size).
- Effort: 1 = one small PR, 2 = several PRs, 3 = needs a design decision or a data backfill.

Priority: P0 = impact 3 and growth 3; P1 = impact 3 or growth 3; P2 = the rest worth doing; drop anything else.
Also record churn for code findings (`git log --since="3 months ago" --format= --name-only | sort | uniq -c`): complex
code that changes often comes before complex code nobody touches.

### Audit report (filled in by step 1)

Keep it in this file, short enough to read in one sitting: one table row per finding, and a detail block only for
P0 and P1. More than about 40 rows means the bar is too low.

1. Summary: five lines at most. The three problems that matter most, and what was not checked.
2. Findings table, sorted by priority (columns: ID, finding, where, evidence, impact / growth / effort, priority,
   status).
3. Detail for each P0 and P1 finding:
   - What it is today, with the code reference.
   - What goes wrong later if it stays.
   - Proposed fix, as a list of small PRs in order, each with the test that pins behavior first.
   - Guardrail that keeps it fixed (a ratchet in `guardrail_baseline.json`, a constraint, an architecture test).
   - Risk and rollback, and for data changes the backfill and the query that proves it is complete.
4. Decisions for the user: questions only they can answer, each with the options and a recommendation.
5. Proposed phases 7+: findings grouped into phases, guardrails before fixes, data model before code that depends on
   it, each phase a list of PRs of one logical change.

#### Starting numbers (main @ 5944ce4, 2026-11-10)

| Metric | Value |
|---|---|
| Functions over 10 / 15 / 25 / 40 branches (ruff 0.17.4 `C901`, no tests or migrations) | 99 / 51 / 14 / 4 |
| Functions with too many branches / statements / arguments (`PLR0912` / `PLR0915` / `PLR0913`) | 69 / 56 / 44 |
| Tests | 735 passed, 18 skipped |
| Statement coverage without test files | 69% (24,305 statements, 7,545 missed) |
| Forbidden imports (`ALLOWED`) / broad excepts in the baseline | 13 / 70 |
| Exact duplicate indexes (same table, same columns) | 15 |
| Model changes without a migration (`makemigrations --check`) | 0 |

#### Findings so far (sessions 1-3 of 6; unranked across tracks until step 2)

Scores are impact / growth / effort. Paths are under `apps/discovery/` unless they start with another package.

| ID | Finding | Where | Evidence | I / G / E | Pri | Status |
|---|---|---|---|---|---|---|
| B-1 | Instagram tables point at a seller with a string `tenant_id`, not a `business` foreign key. The default `tenant_default` matches no business, and `sarv` is a hardcoded alias | `models/instagram.py:162,382,409,547`, `tenants.py:11` | 4 tables; 167 references in 23 files; the crawl-execution table has both a nullable `business` and a `tenant_id` | 3 / 3 / 3 | P0 | CONFIRMED |
| B-2 | A lead and its opportunity are linked by an id inside JSON, and 12 lead fields are copied into that JSON | `models/core.py:512`, `services.py:249-272` | `trace_metadata["instagram"]["lead_id"]`; one query per row wherever a list shows `instagram_*` properties | 3 / 3 / 2 | P0 | CONFIRMED |
| B-3 | The retention job never touches Instagram leads (comments, bios, DM texts), account events, crawl logs or `LLMUsageLog`; it is also not scheduled anywhere in git (compose profile `maintenance`) | `telegram_crawler/retention.py`, `models/instagram.py:397` | No reference to `leads` / `InstagramEngineLead` in the four retention modules | 3 / 3 / 2 | P0 | CONFIRMED |
| B-4 | Account passwords and session cookies are stored unencrypted; only `Provider.api_key` uses `EncryptedTextField`. `AGENTS.md` says secrets in the DB are encrypted | `models/instagram.py:164,176`, `models/x_twitter.py:145-147`, `apps/billing/models.py:19` | `ig_accounts.password`, `session_json`; `x_accounts.password`, `auth_token`, `ct0`; `proxy_configs.raw_url` | 3 / 2 / 2 | P1 | CONFIRMED |
| B-5 | Two ways to charge a wallet. The engine path uses the cost ledger and a cursor. The Instagram path debits inline, keeps its "already charged" list inside the opportunity's JSON (last 20 only) and logs a failed debit without retrying. A transaction does not record what it paid for | `apps/billing/services.py:102`, `services.py:339-361` | `charged_refs` in `trace_metadata`; `WalletTransaction` has no reference column; model name hardcoded at `services.py:307` | 3 / 2 / 2 | P1 | CONFIRMED |
| B-6 | Nothing in the database stops two opportunities for the same seller and engine id: the import looks one up, then creates it | `engine_bridge.py:129-134`, `models/core.py:389` | Only an index on `engine_opportunity_id`; safe today because one `sync` process runs | 3 / 1 / 1 | P1 | CONFIRMED |
| B-7 | `InstagramEngineLead` is five things in one row: person, comment, source post, scores, conversation. Messages are fixed columns (first, second, last reply) plus a JSON history that is also the send queue. One person can be one lead per seller only | `models/instagram.py:397-470`, `outreach_engine.py:368-381` | `UNIQUE (tenant_id, ig_user_pk)`; queue read by `_first_queued_item` from `conversation_history` | 2 / 3 / 3 | P1 | CONFIRMED |
| B-8 | A lead reserved for sending gets status `PROCESSING`, and no code ever releases it. A restart in the middle of a send (every deploy restarts `ig-outreach`) leaves the lead there for good | `outreach_engine.py:391`, `outreach/recipients.py:57` | `PROCESSING` is written in two places and read in none | 2 / 1 / 1 | P2 | CONFIRMED (bug) |
| B-9 | Workers read and write panel tables with hardcoded SQL, so a renamed Django column fails at runtime, not in review. The X worker writes the Instagram stats table | `workers/x_collector/account_pool.py:143-230`, `worker.py:548`, `need_engine/sources.py` | 12 worker and engine files name panel tables; 5 write statements | 2 / 2 / 2 | P2 | CONFIRMED; test cover not checked |
| B-10 | Code filters and branches on keys inside `trace_metadata` (`retention_scrubbed`, `engine.decision`), so the JSON is a second schema | `engine_bridge.py:86`, `services.py:690`, `retention.py:10` | 56 references in code, 6 in query filters | 2 / 2 / 2 | P2 | CONFIRMED |
| B-11 | Accounts are referenced by username text next to, or instead of, a foreign key; a proxy's owner is stored both as `proxy_configs.assigned_to` and as `ig_accounts.proxy_config` | `models/instagram.py:449,456,505,564,656`, `models/proxy.py:26` | 6 text columns: `sender_account`, `second_sender_username`, three `account_username`, `assigned_to` | 2 / 2 / 2 | P2 | CONFIRMED |
| B-12 | Seller feedback is saved three times on every click | `views/opportunities.py:621-667`, `models/core.py:340-342` | `feedback`, `trace_metadata["seller_feedback"]`, `lead_feedback` | 1 / 2 / 1 | P2 | CONFIRMED |
| B-13 | Status columns accept any string: `choices` is not a database rule, and workers write statuses with raw SQL | `workers/x_collector/account_pool.py:192-230` | No `CheckConstraint` on lead or account status | 2 / 1 / 1 | P2 | CONFIRMED |
| B-14 | 15 indexes are exact copies of another index (`db_index=True` plus a named `Index` on the same column) | `models/instagram.py:472-519,611-613`, `models/x_twitter.py:108-111` | `pg_index` grouped by table and columns on a freshly migrated database | 1 / 1 / 1 | P2 | CONFIRMED |
| C-1 | Six places create tables at start-up with `CREATE TABLE IF NOT EXISTS` and `ALTER ... ADD COLUMN IF NOT EXISTS`. There is no version table, so a rename, a drop or a type change cannot be shipped | `need_engine/store.py:27`, `telegram_crawler/schema.sql`, `shared/collector_kit/seen.py`, `state_store.py`, `workers/x_collector/proqe.py`, `x_ingest/__main__.py` | 53 `CREATE TABLE` statements outside Django migrations | 2 / 2 / 2 | P2 | CONFIRMED |
| C-2 | CI does not run `makemigrations --check`, so a model change without its migration can merge (none today) | `.github/workflows/ci.yml` | Check passes on main; 87 migrations applied | 2 / 1 / 1 | P2 | CONFIRMED |
| D-1 | A public URL makes paid LLM calls: no login and no rate limit. When the keyword match fails it asks the model | `apps/products/views.py:444`, `apps/products/services.py:327` | Test client, anonymous `GET /products/api/suggest-category/?name=...` returns 200 and calls `chat_json` (mocked) | 3 / 1 / 1 | P1 | CONFIRMED |
| D-2 | Any seller can make the web process open the Instagram inbox of all their sender accounts, at any hour and as often as they click, with a plain GET. The dispatcher's shift hours and random 5-10 minute spacing are not applied on this path | `apps/accounts/outreach_views.py:306`, `outreach/replies.py:89` | `reply_sync_allowed` is called only in `tasks.py:103` and `run_instagram_outreach.py:141,157`; the view has `login_required` only | 3 / 1 / 1 | P1 | CONFIRMED (read, not run) |
| D-3 | Seller scoping is written by hand in each of the 53 seller views, with three different "is operator" checks (`is_operator`, `_staff`, `is_superuser`). Every id lookup traced is scoped correctly today, but no test walks all routes as another seller, so one missed filter in a new view ships | `apps/accounts/outreach_views.py:390,480`, `views/opportunities.py:373-603` | 136 routes mapped from the resolver; about 30 id lookups traced; cross-seller tests exist in 5 test files, per view | 3 / 2 / 1 | P1 | CONFIRMED (missing guardrail) |
| D-4 | A public URL returns the attribute list of any category by id, including a seller's private categories | `apps/products/views.py:458` | Test client, anonymous `GET /products/api/category-attributes/1/` returns 200 | 1 / 1 / 1 | P2 | CONFIRMED |
| A-1 | Nothing limits function size. The file-size ratchet let 280-line functions through, and each agent edit adds another branch | `scripts/check_baseline.py`, `pyproject.toml` | 51 functions over 15 branches, 14 over 25; 56 over 50 statements; 44 with more than 5 parameters (`fetch_source_medias` has 10) | 2 / 3 / 1 | P1 | CONFIRMED (missing guardrail) |
| A-2 | The retention purge is one function: 58 branches, 288 lines, 34 SQL statements inside a single `with` block, deleting customer data in three schemas. Its file is 22% covered | `telegram_crawler/retention.py:371` | ruff `C901` = 58 (highest in the repo); coverage 349 of 445 statements missed | 3 / 1 / 2 | P1 | CONFIRMED |
| A-3 | Two long functions create opportunities, one per source. Each does customer upsert, product matching, opportunity upsert and analysis in one body; the Instagram one also bills. The import reads the engine's JSON by hand with `x.get(...) or default` although the engine has a pydantic schema for it | `engine_bridge.py:82` (22 branches, 151 lines, 11 writes), `services.py:167` (20 branches, 202 lines, 8 writes, 9 imports inside) | `services.py` is the most changed file on this list (33 commits); `engine_bridge.py` is 64% covered | 3 / 2 / 2 | P1 | CONFIRMED |
| A-4 | The scraper cycle is three large functions with low cover around them | `ig_scraper/comments.py:62` (37 branches, 281 lines, one 251-line loop), `ig_scraper/sources.py:191` (33) and `:84` (25) | Coverage: `comments.py` 69%, `sources.py` 58%, `cycle.py` 32%, `session.py` 22%, `accounts.py` 22% | 3 / 2 / 2 | P1 | CONFIRMED |
| A-5 | The inbox reply sync (writes lead status and captured phone numbers) is one 150-line function, 25% covered | `outreach/replies.py:89` | 20 branches, one 121-line loop; 78 of 104 statements missed | 3 / 1 / 2 | P1 | CONFIRMED |
| A-6 | X collector: the cycle and the search client are the most branched worker code, and the client is barely tested | `workers/x_collector/worker.py:270` (37 branches, 208 lines, 32 commits), `client.py:91` (31, 242 lines), `client.py:334` (27) | `client.py` 22% covered | 2 / 2 / 2 | P2 | CONFIRMED |
| A-7 | Panel views carry the logic: filtering, row decoration and CSV building live in view bodies | `views/export.py:23` (45 branches, one 162-line loop), `views/opportunities.py:41,201,595` (22, 22, 20), `views/instagram.py:81` (24), `apps/products/views.py:223` (21) | `views/opportunities.py` 64% covered, 14 commits | 2 / 2 / 2 | P2 | CONFIRMED |
| A-8 | Category suggestion and Instagram query building mix scoring rules, database writes and an LLM fallback in one function each | `apps/products/services.py:127` (49 branches, 228 lines), `instagram_families.py:191` (43, with a 33-branch nested function) | `instagram_families.py` 38% covered; it also holds the broken buyer-exemplar query from Known issues | 2 / 2 / 2 | P2 | CONFIRMED |
| A-9 | The engine's LLM call handles rate limit, cache, retries, truncation and JSON repair in one loop with 10 parameters; the engine's main `process` step changes often | `need_engine/llm.py:170` (27 branches, 146 lines), `need_engine/engine.py:383` (23, 21 commits) | `llm.py` 60% covered | 2 / 2 / 2 | P2 | CONFIRMED |

Not a finding: 7 of the 8 data migrations have no reverse. They are old and applied; nothing to do. All 45 `/manage/` and 22 `/ops/` routes
require an operator (`operator_required` under two aliases); the 16 routes without a decorator are public on purpose
(landing, login, signup, shop, product card, click counter) except D-1 and D-4.

#### Waiting for

- Production numbers for B-1 to B-14: the user runs `phase6_production_numbers.sql` (read-only, counts only; handed
  over in the session, not committed) on the server. The effort scores of B-1, B-2, B-6 and B-7 depend on how many
  rows break the rule to be added.
- Whether the retention job runs on a schedule on the server, and whether 180 days is a promise to customers (B-3).

#### Not checked yet

- Session 1 read field lists and relations of every model, but not every model method, and not the tables of
  `shared/collector_kit` and `x_ingest` beyond counting them.
- Track A: branch counts are ruff's (`if`, loops, `except`, comprehensions). They do not count `and` / `or` and
  one-line conditionals, which is where `import_opportunity` hides most of its paths. Churn is weak evidence: the
  repository is five weeks old and phases 3-4 moved most of this code into new files.
- Tracks E to I.

## Running the checks locally (Docker)

```bash
cp .env.example .env    # then set POSTGRES_PASSWORD, DJANGO_DEBUG=true and a generated FIELD_ENCRYPTION_KEY
docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple --build-arg PIP_TRUSTED_HOST=pypi.org web
docker compose up -d db
# mount the checkout AND data/ (the web service mounts a named volume over /app/data that hides test fixtures)
docker compose run --rm -T --user root -v "$PWD":/app -v "$PWD/data":/app/data \
  -e DJANGO_DEBUG=true -e NE_PANEL_MOCK_LLM=true -e REQUIRE_DB=true \
  -e TEST_DATABASE_URL=postgresql://postgres:<POSTGRES_PASSWORD>@db:5432/postgres \
  web python -m pytest -q -p no:cacheprovider
docker compose run --rm -T --user root -v "$PWD":/app web sh -c "pip install -q ruff && ruff check ."
```

## Known issues

- Tests must not depend on a developer's `.env`: need_engine and telegram_crawler load it at import time. The
  X-collector tests clear `X_QUERY_*` (`workers/x_collector/tests/conftest.py`); do the same for new env-driven tests.
- Tests that mock the function under review can pass while production breaks (PR #21: the probe was mocked, so CI
  never saw that `api.telegram.org` answers 302). Prefer real local servers/databases in tests.
- A deploy that changes the `Dockerfile` needs `docker compose build` on the server (the app image ships the xray
  binary since PR #21).
- Fixed in Phase 5 (PR 5.1, each with a test that failed before): retired auto-discovered pages are deactivated;
  every worker client answers Instagram challenges without a keyboard (the account becomes CHALLENGED instead of
  being reused after an EOFError); cold DMs and first queued DMs are checked by `validate_and_sanitize_dm`; the
  scraper takes `INSTAGRAM_PRIORITY_BUSINESS_IDS` first and then every business (before, while a dental business
  existed no other business was scraped).
- Found in Phase 5, not fixed: `instagram_families` builds "buyer exemplars" for query expansion with
  `InstagramEngineLead.objects.filter(business=..., buyer_score__gte=...)` and `lead_text`, fields the model does not
  have, so the query always fails and the expansion never sees real buyer comments (now logged at debug).
- Found in Phase 3, not fixed:
  - Telegram's own egress proxy candidates exist in three slightly different lists (`apps/core/ops_bot/telegram_api.py`,
    `shared/telegram_alerts.py`, `channel_config_automation.get_http_request_with_proxy`).
  - `apps/discovery/ig_login.py` is 429 lines, over the 400-line guideline for new files.
- Found in Phase 4, not fixed:
  - `apps/billing/manage/cockpit.py` is 420 lines (over the 400-line guideline).
  - The /manage/ dashboard still has its own Instagram account endpoints (`manage/ig_legacy.py`) next to the
    account pages (`ig_accounts_views.py`) because the dashboard JS calls them; both use the same services.
  - Splitting files in parallel PRs makes `scripts/guardrail_baseline.json` conflict; merge one at a time and re-run
    `check_baseline.py --update`.

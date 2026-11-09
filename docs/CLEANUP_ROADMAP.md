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
- **Next step:** Phase 5, error handling and the bugs listed under "Known issues".
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

- [ ] Work down the broad `except Exception` blocks: catch the specific exception, or `log.exception` at process
      boundaries.
- [ ] Every bug fix starts with a failing test (`systematic-debugging` skill).

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
- Found in Phase 3, not fixed (behavior changes, Phase 5 candidates):
  - Retiring an unqualified page source never deactivates the page: discovered candidates are created with
    `kind="watched"`, and `retire_and_delete_source` keeps every watched page active.
  - The outreach, scraper and cockpit clients keep instagrapi's default challenge handlers (they read a code from
    stdin, which fails in a container), so a challenge there surfaces as a generic error rather than `CHALLENGE`.
    `new_client(..., no_prompts=False)` marks these callers.
  - `instagram_engine.analyzer.validate_and_sanitize_dm` (no links, @mentions or phone numbers in a cold DM) is not
    called anywhere; outreach DMs rely on the prompt alone.
  - The scraper hardcodes priority for business id 4 / "دندان" (`ig_scraper/cycle.build_targets`).
  - Telegram's own egress proxy candidates exist in three slightly different lists (`workers/telegram_config_bot.py`,
    `shared/telegram_alerts.py`, `channel_config_automation.get_http_request_with_proxy`).
  - `apps/discovery/ig_login.py` is 429 lines, over the 400-line guideline for new files.
- Found in Phase 4, not fixed:
  - `apps/billing/manage/cockpit.py` is 420 lines (over the 400-line guideline).
  - The /manage/ dashboard still has its own Instagram account endpoints (`manage/ig_legacy.py`) next to the
    account pages (`ig_accounts_views.py`) because the dashboard JS calls them; both use the same services.
  - Splitting files in parallel PRs makes `scripts/guardrail_baseline.json` conflict; merge one at a time and re-run
    `check_baseline.py --update`.

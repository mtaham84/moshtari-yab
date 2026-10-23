# Cleanup candidates

Items are candidates only. This inventory does not change application code. Static import scans can miss Django autodiscovery, CLI entry points, dynamic imports, shell use, and external consumers.

## Directories

- apps/discovery/taxonomy/ is empty in the current checkout. Evidence: directory walk returned it with no files or child directories. Confidence: high for this checkout; ignored or access-restricted content may exist.
- Root directories buyer_engine/, analysis/, sources/, and tests/ appear in Get-ChildItem but had no source files returned by rg --files. Evidence: git ls-files listed 285 paths; rg --files analysis buyer_engine sources tests returned no source output. Confidence: medium; ignored content or access-restricted files may exist.

## Modules with no static inbound import

These are not declared dead: each has a plausible framework, command-line, or dynamic caller. The AST scan excluded tests and migrations, resolved relative imports, and searched tracked plus visible project Python files. Confidence: medium that they have no ordinary static import; low that they are unused.

- need_engine/x_query_scoring.py: only static use found was its test import; runtime reference search found need_engine/tests/test_x_quality_pure.py:10 and no production caller. It may be an abandoned scorer. Confidence: medium.
- Django views, models, signals, context processors, admin modules, template tags, and app configs may be loaded through framework conventions or URL declarations. Evidence: config/urls.py, apps/*/urls.py, apps/*/apps.py, and templates. They were not classified as unused from import search alone. Confidence: low.
- Collector and discovery CLI modules, including workers/ig_collector/cli.py, its backends, instagram_discovery/cli.py, telegram_crawler/main.py, workers/x_collector/cli_mapping.py, xray/proxy_switcher.py, scripts/retention_report.py, and workers/state_migrate.py, may be invoked as commands or imported dynamically. Evidence: docker/entrypoint.sh:46-68, package __main__.py files, and local relative imports. Confidence: low for removal.
- need_engine/x_query_scoring.py is the only concrete candidate found by the runtime symbol search: its only static import is need_engine/tests/test_x_quality_pure.py:10; no production caller was found by rg for x_query_scoring, score_query, or query_weight. Confidence: medium; external or dynamic imports were not checked.

## Duplicated or overlapping logic

- Status serialization is shared, though the workers keep separate target/path handling: workers/x_collector/worker.py:354-366 and workers/ig_collector/worker.py:554-568 both call shared/collector_kit/status.py:11-31. This is reuse of a shared helper, not duplicated atomic-write code. Confidence: high.
- State behavior is split between shared/collector_kit/state_store.py (shared SQLite/PostgreSQL collector state) and need_engine/store.py (engine-specific PostgreSQL queue, vectors, costs, and opportunities). They share a database technology, not an interchangeable responsibility. Evidence: shared/collector_kit/state_store.py:54-82, need_engine/store.py:29-65. Confidence: high both stores exist; low that they should merge.
- X promotional filtering is shared at shared/x_filters.py:1-18; need_engine/x_filters.py:9-42 imports it and adds platform filtering. This is already partly shared rather than a full duplicate. Confidence: medium.

## Files over 500 lines

Counts came from a Python line counter over tracked plus visible project .py files; command used git ls-files plus Path.rglob under application packages. Proposed seams follow named responsibilities and are not an instruction to refactor.

| File | Lines | Possible responsibility seams | Confidence |
|---|---:|---|---|
| apps/billing/services.py | 706 | wallet/billing; operational status and retention report; X analytics. Evidence: function groups ensure_wallet at line 30, service_status at 247, x_collector_status at 501, x_overview at 573. | medium |
| apps/core/management/commands/seed_demo_businesses.py | 568 | seed data by business/catalog versus command orchestration. Evidence: two business blocks, second begins around line 349. | medium |
| apps/discovery/models.py | 804 | opportunity/customer/community models versus X search configuration/telemetry. Evidence: Customer/Opportunity at apps/discovery/models.py:169-252; X models at :728-804. | high |
| apps/discovery/views.py | 670 | opportunity pages, community pages, and X management/search pages. Evidence: apps/discovery/views.py:147, :477, :559. | high |
| apps/products/views.py | 613 | product CRUD, images, category APIs, public card, X toggle. Evidence: apps/products/views.py:17, :399, :508, :599. | high |
| need_engine/engine.py | 697 | ingest/product sync; generic matching/emission; X-specific matching/thread handling; run lifecycle. Evidence: need_engine/engine.py:119-195, :252-365, :458-591, :669-697. | high |
| telegram_crawler/retention.py | 662 | Telegram table retention; panel X-post cleanup; SQLite collector-state cleanup. Evidence: telegram_crawler/retention.py:1-662; command calls at apps/core/management/commands/purge_retained_data.py:40-49. | medium |
| workers/ig_collector/worker.py | 583 | record normalization/seed validation; run orchestration; post/comment collection; persistence/status. Evidence: workers/ig_collector/worker.py:35-181, :183-490, :491-568. | high |
| xray/proxy_switcher.py | 851 | URL parsers; candidate tests/config swap; daemon/report; config import and systemd install. Evidence: xray/proxy_switcher.py:45-290, :291-505, :506-689, :690-807. | high |
| need_engine/tests/test_need_engine.py | 616 | test file, not production code; split only if navigation becomes difficult. Evidence: line-count command. | high |

## Worktrees

- .worktrees/x-need-engine is registered as worktree feature/x-need-engine, HEAD 399527e9c4403a46f6490df0697e151a7d256e96. Current worktree is branch new-insta, HEAD 10b1d7ed807b1e30c69e26a6d0dfb68bcbad0b08. Evidence: git worktree list --porcelain. Its name does not prove it is stale. Freshness, uncommitted changes, and whether it is still needed are unverified and require owner review. Confidence: high for registered metadata; low for staleness.

The filesystem scan printed access-denied errors for temporary test directories such as .pytest-ig-tmp/, data/ig_temp_test/, and tmp*. Their contents are unverified and no deletion candidate is inferred from them.

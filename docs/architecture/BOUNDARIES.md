# Proposed package boundaries

This proposal treats imports in application code as dependencies. Test modules and migrations are excluded from the boundary test because they may import implementation details to exercise them. `scripts/import_graph.py` currently scans some test-named files, so its package edges include test-only imports.

## Allowed directions

| Package | May import | Why |
|---|---|---|
| `config` | `apps` | Django URL configuration loads app routes (`config/urls.py:8`). |
| `apps` | `need_engine`, `shared` | The panel calls engine utilities and shared operational helpers (`apps/discovery/views.py:22`, `apps/billing/services.py:260`). |
| `need_engine` | `shared` | The engine uses shared X filtering (`need_engine/x_filters.py:9`). |
| `telegram_crawler` | `shared` | The crawler records shared collector heartbeats (`telegram_crawler/main.py:20`). |
| `workers.x_collector` | `need_engine`, `shared`, `x_ingest` | X uses engine query/filter utilities, shared collector tools, and optional ingestion (`workers/x_collector/worker.py:18-25, 400, 418-423`). |
| `workers.ig_collector` | `shared`, `instagram_discovery` | Instagram uses shared persistence/status tools and its comment policy (`workers/ig_collector/worker.py:14-20`, `workers/ig_collector/policies.py:6`). |
| `x_ingest` | `shared` | Ingest validates shared source records (`x_ingest/__main__.py:14`). |
| `instagram_discovery` | `apps`, `workers.ig_collector` | Optional Django category loading and the existing label tools (`instagram_discovery/cli.py:113`, `instagram_discovery/labeling.py:9-10`). |
| `shared` | none of these packages | Shared code must remain usable by collectors, engine, and panel without importing them. |
| `xray` | none of these packages | Xray is a separate utility. |

The `instagram_discovery` ↔ `workers.ig_collector` dependency is currently bidirectional: the collector imports discovery policy code, while discovery labeling imports collector policies. This is an existing package cycle. The proposal allows it temporarily; a later boundary change should pick one owner for the policy functions and remove one direction.

## Forbidden directions and current exceptions

These are forbidden for application code. The AST test should report `file:line`; each listed existing exception is temporary and may only be removed, never expanded without updating this proposal.

| Forbidden import | Existing exception | Evidence and reason |
|---|---|---|
| `need_engine` → `apps` | `need_engine/eval/golden.py:137` | Evaluation exports seller feedback from Django opportunities. Keep this exception until export is moved behind a panel-owned command or API. |
| `need_engine` → `telegram_crawler` | `need_engine/retention.py:2` | Retention reuses crawler cleanup code. This crosses process/package ownership and remains an exception pending a shared retention seam. |
| `telegram_crawler` → `apps` | `telegram_crawler/retention.py:84,86` | Telegram retention scrubs panel opportunities. `telegram_crawler/retention.py` has uncommitted work and must not be changed as part of boundary enforcement. |

The observed production import graph has no `workers.x_collector` → `apps`, `workers.ig_collector` → `apps`, `x_ingest` → `apps`, or `shared` → another listed package edge. The generated graph also reports `apps` → `telegram_crawler` from `apps/discovery/test_sources.py:8`, and `apps` → `x_ingest` from `apps/discovery/tests.py`; these are tests, not runtime dependencies.

## Test scope

`tests/test_architecture.py` should parse imports with `ast`, resolve relative imports, and inspect production `.py` files under the listed packages. Exclude tests, migrations, and `__pycache__`. Exceptions above must be an explicit commented allow-list with exact importing module and target package. Report every new violation with its path and line number.

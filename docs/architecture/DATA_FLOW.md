# Data flow

## Telegram

1. Docker starts `telegram_crawler.main.async_main` (`docker/entrypoint.sh:46-53`, `telegram_crawler/main.py:65-102`). It creates `CrawlerMonitor` and runs backfill plus live monitoring.
2. `CrawlerMonitor.run_backfill` / `handle_live_message` call `_store` (`telegram_crawler/monitor.py:367-396`). `_store` writes user, chat, parent/context, and message rows through `Archive` (`telegram_crawler/monitor.py:302-330`, `telegram_crawler/db.py:37-51`, `:57-118`). **Between steps:** PostgreSQL `crawler.tg_users`, `crawler.tg_chats`, `crawler.tg_messages` (`telegram_crawler/schema.sql:20-90`).
3. `NeedEngine.run_once` calls `ingest` and `_ingest_source` (`need_engine/engine.py:669-679`, `:119-160`). `SQLMessageSource.fetch_after` reads new `tg_messages` rows and joins chats/users and recent reply parents (`need_engine/sources.py:64-105`). **Between steps:** engine PostgreSQL schema (`need_engine.store`, queue tables `pending` and `recent`; `need_engine/store.py:29-65`).
4. `NeedEngine.process` matches messages and `_emit` publishes opportunity payloads (`need_engine/engine.py:252-365`, `:378-457`). **Between steps:** `need_engine.opportunities` sequence/payload rows (`need_engine/store.py:61-65`, `:490-499`).
5. Django `sync_opportunities.Command.handle` reads published rows and calls `engine_bridge.import_opportunity` (`apps/discovery/management/commands/sync_opportunities.py:30-58`; `apps/discovery/engine_bridge.py:70-190`, `:253-261`). **Between steps:** Django public tables `discovery_opportunity`, `discovery_customer`, evidence and product match tables (`apps/discovery/models.py:169-252`, `:489-582`).

## X

1. Compose profile `x` starts `workers.x_collector` (`docker-compose.yml:90-96`, `docker/entrypoint.sh:60-64`; `workers/x_collector/__main__.py:16-106`). `XCollector.run`/`_run_locked` call the configured X CLI client and normalize results (`workers/x_collector/worker.py:174-270`, `:324-337`).
2. The collector appends records to dated JSONL and `latest.jsonl` (`workers/x_collector/worker.py:247-270`, `shared/collector_kit/jsonl.py:11-24`). **Between steps:** files under `X_COLLECT_OUTPUT_DIR`, typically `data/x_collected/` (`workers/x_collector/worker.py:147`).
3. `ingest_new` selects the newly appended file range and calls `x_ingest.load` when `X_AUTO_INGEST` is on (`workers/x_collector/worker.py:368-409`). `x_ingest._load` validates each line and inserts/upserts records (`x_ingest/__main__.py:31-75`, `:75-135`). **Between steps:** `crawler.x_posts` and `crawler.x_post_products`; search run metadata is written to `crawler.x_search_runs` (`x_ingest/__main__.py:86-135`, `:141-164`).
4. `NeedEngine.ingest` chooses `XMessageSource` for public mode or `XProductHitSource` for product mode (`need_engine/engine.py:119-135`). They read `crawler.x_posts` or joined `crawler.x_post_products` (`need_engine/sources.py:108-162`). **Between steps:** `need_engine.pending` and `recent` (`need_engine/engine.py:143-160`, `need_engine/store.py:31-32`).
5. X posts pass the platform's prefilter/matching path and may be published via the same engine opportunity table (`need_engine/platforms/x.py:4-10`, `need_engine/engine.py:378-457`, `need_engine/store.py:490-499`). Django sync imports them using the same path as Telegram (`apps/discovery/management/commands/sync_opportunities.py:30-58`, `apps/discovery/engine_bridge.py:70-190`).

## Instagram

1. `workers.ig_collector.cli._main` loads seeds and selects mock, Instaloader, or HikerAPI backend (`workers/ig_collector/cli.py:33-73`). There is no Instagram Compose service (`docker-compose.yml:28-128`).
2. `InstagramCollector.run` / `_run_locked` read posts, apply the configured intent and comment policies, and normalize records (`workers/ig_collector/worker.py:216-270`, `:390-465`).
3. `_persist_records` and `_persist_seller_leads` append records (`workers/ig_collector/worker.py:491-541`). **Between steps:** dated/latest JSONL and optionally `seller_leads.jsonl` under `IG_COLLECT_OUTPUT_DIR` (`workers/ig_collector/worker.py:491-528`); state and status are local SQLite/JSON files (`workers/ig_collector/worker.py:193-210`, `:554-568`). **This is where the Instagram flow stops today:** no table, `need_engine` source, or panel importer is called (`workers/ig_collector/worker.py:491-568`).
4. Separately, `instagram_discovery.cli.main` calls `OpenCLICollector.fetch`, analyzes post captions, and writes archive/review JSONL (`instagram_discovery/cli.py:87-100`, `:133-174`; `instagram_discovery/collector.py:40-84`). That CLI says it does not read comments or write the main Django database (`instagram_discovery/cli.py:173`).

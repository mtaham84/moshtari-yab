# Proxy refactor (second refactor)

Goal, set by the user on 2026-11-10: **all proxy logic lives in one place**, so it can be read, changed and debugged
without hunting through the repo. This document lists what was found on 2026-11-10 (first deploy of the cleaned-up
`main` to production, then reading the code) and the order to fix it in.

Status: plan only. Nothing below is done unless it says "done".

Rules from `AGENTS.md` apply: one small PR per step, a test that fails before each fix, replace means delete, no
change to anti-ban behaviour (which account gets which IP, rest after an IP change) unless the step says so.

## What happened in production on 2026-11-10

Why the rest of this document matters:

1. The app had written four vless configs without TLS into `xray/config.json`. The xray version on the server
   refuses those, so xray did not start and **every proxy was dead** (Instagram, X, the config channels, the ops bot).
2. The ops bot's 5-minute purge (`prune_dead_configs`) tests each config through the xray service. With xray down,
   every test failed, so it **deleted all 55 configs** and moved every Instagram and X account to the shared pool.
   Fixed in PR #48 (the purge does nothing while xray is down).
3. `channel-config-sync` could not read t.me even "directly", because its direct attempt still used
   `HTTP(S)_PROXY` (xray). Fixed in PR #50.
4. The configs came back only when the channel sync imported a new one and xray was rebuilt.

One broken config took down everything, and the path from "config posted in a channel" to "account uses this IP" runs
through four folders, two files on disk, three schedulers and the ops bot. That path is what this refactor fixes.

## Where the proxy code is today

| File | Lines | What it does |
|---|---|---|
| `xray/multi_proxy_manager.py` | 776 | Top-level folder outside `apps/`, but sets up Django itself. Parses links (vless, vmess, trojan, ss), validates outbounds, tests a link with a temporary xray, saves configs, picks local ports, writes `xray/config.json`, reloads xray, a fallback tunnel |
| `apps/discovery/channel_config_automation.py` | 522 | The list of config channels, reading `t.me/s/<channel>`, finding links in posts, the per-channel "last post" state, the sync |
| `apps/discovery/proxies/assignment.py` | 391 | Which account gets which config, healing, `release_config`, `prune_dead_configs` |
| `apps/discovery/proxies/health.py` | 179 | `port_open`, `measure_config` (tests through xray), egress IP / ISP lookup |
| `apps/discovery/proxies/urls.py` | 124 | Proxy URL helpers (pool port 1080, `effective_proxy_url`) |
| `apps/discovery/proxies/rotation.py` | 142 | Mobile IP rotation before a DM |
| `apps/discovery/proxies/export.py` | 102 | Config export for accounts (txt / QR) |
| `apps/discovery/models/proxy.py` | 69 | `ProxyConfig`, `ConfigChannelSource` |
| `apps/discovery/apps.py` | | `pre_delete` signal: deleting a config moves its Instagram accounts |
| `apps/discovery/management/commands/run_channel_config_automation.py` | | The `channel-config-sync` service loop |
| `apps/discovery/tasks.py` | | Celery: channel sync, healthcheck and heal |
| `apps/core/ops_bot/worker.py`, `status.py` | | The 5-minute purge runs inside the ops bot; config status and retest buttons |
| `apps/core/ops_bot/telegram_api.py` | | Its own list of proxies to reach the Telegram Bot API |
| `shared/telegram_alerts.py` | | Another list of proxies to reach Telegram (alerts) |
| `apps/billing/manage/proxies.py` | 221 | Panel: proxy test, config channels (inside the billing app, see F-3 in the roadmap) |
| `workers/channel_config_collector.py` | | A third runner of the channel sync (systemd); not installed on the current server |
| `xray/watch.sh` | | Inside the xray container: restarts xray when `config.json` changes |

## Problems found

Each problem has an id used by the steps below.

### P1. Three schedulers run the same jobs

- The channel sync runs from the `channel-config-sync` service (every 180 s), from Celery beat
  `sync-telegram-channel-configs` (every 180 s) and from `workers/channel_config_collector.py`. Celery beat only
  started running on 2026-11-10, when the celery crash loop was fixed (PR #51), so on production every channel is
  now read twice every 3 minutes.
- Config health runs from the ops bot thread (`prune_dead_configs` every 5 min, deletes) and from Celery
  (`task_healthcheck_and_heal_proxies` every 5 min, moves accounts). A Telegram bot should not own infrastructure
  maintenance: when the bot is down or restarted (every deploy restarts it), the purge stops; when it is upgraded
  by a `git pull`, new purge code runs before the rest is deployed.

### P2. Config channels are stored in two places, with silent fallbacks

- The table `config_channel_sources` (`ConfigChannelSource`, migration 0035) exists, but on production it is empty;
  the only channel lives in `data/proxy_channels.json`.
- Every function (`get_monitored_channels`, `add_monitored_channel`, `delete_monitored_channel`,
  `toggle_monitored_channel`) tries the database, swallows any error at debug level, falls back to the file, and also
  copies everything into the file. Two sources of truth that can disagree, and failures nobody sees.
- When the file is missing or unreadable, `_load_channels_json` returns a hard-coded channel (`AR14N24B`, "کانال اصلی
  کانفیگ"), so the system pretends a channel is configured. It should return an empty list, and the panel should say
  that no channel has been added.
- The "last post read" per channel is a third store: `data/channel_configs_state.json`.

### P3. A broken config can still stop xray

- `is_valid_xray_outbound` was added (2026-11-06) to keep configs xray refuses out of `config.json`, but:
  - `rebuild_xray_config_file` keeps the existing file unchanged when the database has no ACTIVE config, even if that
    file contains the configs that stop xray (seen on production: 0 configs in the DB, a stale file with four bad
    ones).
  - Its "private address" check uses prefixes `"172.2"` and `"172.3"`, which also match public addresses such as
    `172.245.x.x`. Only `172.16.0.0/12` is private; use `ipaddress.ip_address(...).is_private`.
- xray has no way to report "I refused this config": `watch.sh` restarts it on every file change and the error goes
  only to the container log. Nothing alerts the ops chat that xray itself is down.

### P4. Testing a config means two different things

- New links are tested with a temporary xray (`test_outbound_ping`); saved configs are tested through the running
  xray service (`health.measure_config` → `probe_proxy_port`). The second one fails for every config when xray is
  down. PR #48 guards the purge, but anything else that calls `measure_config` while xray is down still reads
  "dead".
- The ping limit (2000 ms) is written in more than one place: `health.MAX_PING_MS`, and again as a literal in
  `xray/multi_proxy_manager.py` (`probe_socks5_http`, `add_proxy_config`).

### P5. Link parsing is fragile

- `parse_ss_url` crashes with `TypeError` when an `ss://` link has no port (seen on production, 2026-11-10); the
  caller logs a traceback and skips the link. Each parser should reject a bad link with a clear reason.
- `fetch_channel_messages` finds posts in the t.me HTML with one large regex. If Telegram changes its HTML, it finds
  zero posts and nothing reports it. Python's own `html.parser` is sturdier and needs no new dependency; a sync
  that finds zero posts on a channel that had posts before should log a warning.

### P6. Several "how to reach Telegram" lists

`apps/core/ops_bot/telegram_api.get_proxy_candidates`, `shared/telegram_alerts.proxy_candidates` and
`channel_config_automation.get_http_request_with_proxy` each build their own list of proxies to try (already listed in
the roadmap's known issues). The user confirmed on 2026-11-10 that **the bots and the Telegram account do not need a
proxy on the current server**, so direct must always be tried first and must really be direct (PR #50 fixed the
channel fetch only).

### P7. Logging

- `telegram_api` logs the full request URL when every candidate fails, and the URL contains the bot token
  (`/bot<token>/getUpdates`). Anyone who can read the server logs can take over the bot. The token must be redacted
  (and the current token revoked in BotFather).
- The purge and the heal log one line per config or account, but no summary ("tested 40, removed 3, xray up").

### P8. Code in the wrong place

- `xray/multi_proxy_manager.py` is a Django module living outside `apps/`, with its own `django.setup()` and a
  `POSTGRES_HOST` rewrite for running on the host.
- The panel pages for proxies are in `apps/billing/manage/proxies.py` (finding F-3: the operator panel lives in the
  billing app).
- Proxy code for X accounts (`_detach_proxy_from_account`, the X branch of `assign_proxies_to_accounts`) sits next
  to the Instagram code with different rules; `workers/x_collector/account_pool.py` has its own default
  (`TWITTER_PROXY`, `socks5h://xray:1080`).

## Target layout

Everything about proxies in one package, one file per job, each under 400 lines:

```
apps/discovery/proxies/
    links.py        parse and validate a config link (vless, vmess, trojan, ss) -> xray outbound, or a clear error
    xray.py         write config.json from the DB (only valid outbounds, never keep a bad file), reload, is xray up
    probe.py        test a config: one function, says "xray down" instead of "dead" when xray is down
    channels.py     config channels (DB only), read t.me with html.parser, find links in posts
    sync.py         one sync round: new links from channels -> test -> save
    maintenance.py  one health round: retest, mark dead, delete after N failed rounds, heal accounts, summary log
    assignment.py   which account gets which config (unchanged rules)
    urls.py         proxy URL helpers (unchanged)
    rotation.py     mobile IP rotation (unchanged)
    export.py       export for accounts (unchanged)
    telegram.py     the one "how to reach Telegram" list: direct first, then the configured proxy
```

And around it:

- **One scheduler.** Celery beat runs `sync` and `maintenance`; the `channel-config-sync` service, the ops bot
  purge thread and `workers/channel_config_collector.py` are deleted (replace means delete). Celery is chosen because
  it already has both schedules and runs in the image; the service can be removed from `docker-compose.yml` in the
  same PR. Before deleting the systemd script, ask the user whether it runs on any server.
- **The ops bot only reports and triggers**: its buttons call `sync` / `maintenance` and show the result.
- **One store per thing**: configs in `proxy_configs`, channels in `config_channel_sources` with a `last_post_id`
  column; `xray/config.json` stays a file because xray reads files, but it is always generated, never edited.
- **Panel pages** move with the rest of the operator panel when F-3 is done; until then they only call functions of
  `apps/discovery/proxies/`.

## Steps (one PR each, in this order)

Safety first, then the moves, then the cleanup. Each step lists its pinning test.

1. **Redact secrets in proxy and Telegram logs** (P7). Test: a failed Bot API call logs no token. Revoke the current
   bot token after the deploy.
2. **Never keep a config xray refuses** (P3): `rebuild_xray_config_file` always filters the existing file through
   `is_valid_xray_outbound` instead of keeping it as is; fix the private-address check with `ipaddress`. Tests: a stale
   file with a vless-without-TLS outbound and an empty DB is rebuilt without it; `172.245.1.1` is public.
3. **Alert when xray is down**: the maintenance round sends one ops-chat alert when the pool port stays closed for
   two rounds, and one when it is back. Test with the port closed / open.
4. **One probe** (P4): `probe.py` returns live / dead / xray-down; `measure_config` and the purge use it; the 2000 ms
   limit lives in one constant. Test: with xray down, nothing is reported dead.
5. **Delete only after repeated failures**: a config is deleted after it failed N rounds in a row (for example 3),
   not on the first failed test. This changes when accounts move, so ask the user before merging. Test: one failed
   round marks DEAD, three delete.
6. **Parsers reject bad links clearly** (P5): `links.py` with one function per protocol, moved out of
   `xray/multi_proxy_manager.py`. Tests: an `ss://` link without a port, a vmess link with bad base64, a vless link
   without id.
7. **Channels in the database only** (P2): a data migration copies `data/proxy_channels.json` and
   `channel_configs_state.json` into `config_channel_sources` (new `last_post_id` column); the file code, the
   fallbacks and the hard-coded `AR14N24B` are deleted. Test: no channel → empty list and a clear panel message;
   the migration imports the file.
8. **Read t.me with `html.parser`** (P5), warn when a channel suddenly has zero posts. Test with a saved t.me page.
9. **One scheduler** (P1): Celery beat runs `sync` and `maintenance`; delete the `channel-config-sync` service, the ops
   bot purge thread and (after asking the user) `workers/channel_config_collector.py`. Test: the beat schedule
   has each job once; the bot has no purge loop.
10. **Move the rest of `xray/multi_proxy_manager.py`** into `apps/discovery/proxies/xray.py` and `sync.py`, delete
    `xray/multi_proxy_manager.py` (`xray/` keeps only `config.json`, `config.example.json` and `watch.sh`). Pure
    move: `scripts/check_baseline.py --update --moved-from xray/multi_proxy_manager.py`.
11. **One Telegram egress list** (P6): `proxies/telegram.py`, used by the ops bot, the alerts and the channel reader;
    direct first. Test: with every proxy down, direct is still tried.
12. **X accounts** (P8): the X proxy rules get their own small function in `assignment.py` (or a clearly named
    section), and `account_pool.py` reads the default from the same place as everything else.

After step 12, the answer to "where is the proxy logic?" is one folder: `apps/discovery/proxies/`.

## Done on 2026-11-10 (not part of the steps)

- PR #48: the purge does nothing while xray is down.
- PR #50: the channel fetch's direct attempt is really direct.
- PR #51: redis client version, so Celery runs (which is why P1 became visible).

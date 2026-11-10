# Telegram pipeline: what to fix

Plan for making the Telegram pipeline deliver leads sooner and more reliably. Written on 2026-11-10 from a read of
the code (`telegram_crawler/`, `need_engine/`); nothing was run against Telegram and there are no production numbers
yet, so every item says how it was verified. Update this file in the PR that fixes an item; delete it when the list
is empty.

How the pipeline works: `docs/TELEGRAM_PIPELINE.md`.

Two kinds of change are mixed here. Items marked **engine** only change when and how stored messages are analysed.
Items marked **account** change what the crawler account asks Telegram for, and need the owner's explicit yes
(`AGENTS.md`, Domain notes), even when they reduce the number of requests.

## Fixes, in order

### 1. Leads from quiet groups arrive late or never (engine)

- Today: a chat is analysed in whole batches of `NE_TRIGGER_COUNT` messages (50, `need_engine/windowing.py:49`). The
  time limit that would analyse leftovers, `NE_MAX_WAIT_MINUTES`, is 0 = off (`need_engine/config.py:165`). In a
  quiet group a buyer's message waits until 49 more arrive; in every group the last messages are always waiting.
- First check what the server's `.env` sets. If it is already on, close this item.
- Fix: turn the time limit on (start with 30 to 60 minutes) in `.env.example` and on the server. No code change.
- Cost: more, smaller LLM calls. Each call also carries the 10 context messages, so very short waits are wasteful.
  Measure calls per day and cost per lead for a week before and after.
- Verified: read in code; the local `.env` and `.env.example` set only `NE_TRIGGER_COUNT=50`.

### 2. Bios are fetched but never read (account)

- Today: a worker asks Telegram for the full profile of every new sender, one every 3 seconds
  (`telegram_crawler/monitor.py:399-404`). The engine's Telegram query (`need_engine/sources.py:75`) does not select
  the bio, the phone or the raw object, and nothing else reads them for Telegram leads. The only reader of
  `tg_users` outside the crawler looks up usernames for X contacts.
- This is the crawler's highest-volume request besides reading messages, so the most likely cause of flood waits,
  and it stores personal data nobody uses.
- Decide one of:
  - stop: `TG_FETCH_PROFILES=false`, and `TG_STORE_RAW=false` for the same reason; or
  - use it: pass the bio to the extraction prompt so the model can tell sellers and advertisers from buyers (the X
    path already does this with `NE_X_BIO_CHARS`). Then measure whether fewer seller posts become leads.
- Verified: read in code and by grep; flood-wait frequency not measured.

### 3. Show what each group costs and gives (engine and panel)

- Today: cost follows how chatty a group is. Every 50 messages are one LLM call whatever they contain; only bots
  and empty messages are removed first (`need_engine/windowing.py:62`). Nothing shows, per group, messages read,
  cost, needs found, opportunities, and how many the seller marked relevant.
- Fix: one report per group from data that already exists (the cost ledger's `ref` holds the chat id, needs and
  opportunities hold the chat). Operators then pause groups that only cost money and look for more like the good
  ones. This is the way to more leads without more load on the account.
- Not proposed: a keyword filter before the LLM. The prompt looks for implied needs, which a keyword filter would
  drop. Revisit only with the report's numbers.

### 4. Messages are lost after a stop (account)

- Today: on start and on resume the crawler reads history newest first, at most `BACKFILL_LIMIT` (200) messages and
  `BACKFILL_HOURS` (24) back (`telegram_crawler/monitor.py:334-343`), then saves the newest id as the resume point.
  If a group produced more than 200 messages during the stop, the older ones are skipped for good.
- Suspected, not proven: while running, nothing re-checks for messages that Telegram did not deliver as live updates
  (large groups can drop updates). There is no periodic catch-up.
- Fix: after a stop, read forward from the resume point until caught up (paced, with the existing flood handling),
  and run the same catch-up for each group every N minutes.
- Test first: a group with 500 messages since the resume point ends with all 500 archived.

### 5. The health signal says "fine" when nothing arrives (engine and ops)

- Today: the heartbeat is sent every 60 seconds while the process runs (`telegram_crawler/main.py:61`), with the
  number of messages archived, which may be zero. A session that is connected but receives nothing, or an account
  removed from its groups, looks healthy.
- Fix: the ops status and the Telegram alert treat "no message archived for N minutes during the day, with active
  groups" as a problem. Item 4's catch-up gives the truth to compare against.

### 6. One account does everything (account)

- Today: one Telethon session joins and reads every group, at most 100 (`telegram_crawler/config.py:82`). A ban, a
  logout or the limit stops Telegram for every seller, and recovery is a manual login on the server.
- Fix, in two steps: first write down and test the recovery (new session, re-join from the sources table, which
  the crawler already does). Later, a second account as a spare or to split groups; that is a design change
  (which account owns which group) and a ban-risk decision.

### 7. Edited and deleted messages are not followed (account)

- Today: only new messages are handled (`telegram_crawler/monitor.py:440`). The schema has `edit_date`, but an edit
  that adds details ("budget is 20 million") never reaches the engine, and a message its author deleted stays
  stored until retention removes it.
- Fix: handle edits of messages not yet analysed by updating the stored text; decide separately whether deletes
  should remove the stored message (a privacy question for the owner).

## Not looked at

- The quality of the extraction and verification prompts, and the matching thresholds. They need labelled examples
  (`need_engine/eval/`), not a code read.
- How sellers contact a Telegram lead in practice, and what happens for users without a username.
- The retention job for the `crawler` schema (it is covered by the audit in `docs/CLEANUP_ROADMAP.md`, finding A-2).

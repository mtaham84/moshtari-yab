# How the Telegram pipeline works

From a group link in the panel to a lead for a seller. Written on 2026-11-10 from the code; defaults are the values
in the code, the server's `.env` may differ. Nothing was run against Telegram. Known problems are in `docs/TELEGRAM_FIXES.md`.

## The short version

```
operator or seller adds a group in the panel («جوامع آنلاین»)
        │
        ▼
1. JOIN        the crawler account joins the group                    telegram_crawler  (service `crawler`)
2. ARCHIVE     history on join, then every new message, stored raw    → schema `crawler`
        │
        ▼
3. COLLECT     new rows are queued per chat                           need_engine  (service `engine`)
4. WAIT        until a chat has 50 new messages
5. READ        one LLM call per 50 messages: who needs something?     → need cards
6. MATCH       candidates by embeddings + keywords, LLM checklist, score in code
7. PUBLISH     opportunity with a drafted reply                       → need_engine.opportunities
        │
        ▼
8. IMPORT      one opportunity per seller whose product matched       sync_opportunities  (service `sync`)
9. SELLER      sees the lead and contacts the person on Telegram themselves
```

The crawler does no analysis and the engine never talks to Telegram. They meet in the database.

## The difference from X

On X a product's own search finds the post, so the product is known. On Telegram nobody searches: the crawler stores
everything said in a group, the engine first works out **who needs what**, and only then looks for products that fit.
A lead is tied to a product by matching (step 6), not by a query.

## Which groups, and who sees their leads (`apps/discovery/sources.py`, `need_engine/access.py`)

A row of `MonitoredCommunity` is a source. It has a link, an owner and a scope:

- **GLOBAL** (no owner, added by operators): needs found there are matched against every seller's products, and the
  platform pays for reading the group.
- **PRIVATE** (added by a seller): needs are matched only against that seller's products, and the seller pays. Several
  sellers can add the same group; it is joined and read once, and each owner is charged the full reading cost
  (`NE_CHARGE_EACH_OWNER`).
- No active source for a chat: the engine does not read it at all.

The rule is applied before any LLM call, and again when the panel imports an opportunity. A seller whose balance is
used up is skipped; private groups that only blocked sellers watch are still archived, and analysed after the top-up.

## 1. Joining (`monitor.py`, `panel.py`, `telegram_client.py`)

- One Telegram user account (a Telethon session in the `appdata` volume, created once with
  `docker compose run --rm crawler login`), optionally behind `TG_PROXY`.
- Every 30 seconds the crawler reads the sources table. For a new link it joins the group: one join per 60 seconds,
  at most 100 groups for the account. Groups the account is already in are recognised without joining again.
- It writes the result back to the row: status (`ACTIVE`, `PENDING`, `PAUSED`, `ERROR`), an error code the panel
  translates (`INVALID_LINK`, `NO_ACCESS`, `NOT_A_GROUP`, `BANNED`, `LIMIT_REACHED`, `FLOOD_WAIT:<seconds>`), member
  count and message counters. A bad link is retried only after someone re-activates the row.
- Switching a source off pauses the group: live messages are ignored, and what was missed is fetched when it is
  switched on again.

## 2. Archiving (`monitor.py`, `extractor.py`, `db.py`, `schema.sql`)

- **On join and on every start:** the last 24 hours of the group, at most 200 messages, and only messages newer than
  the last one already scanned.
- **Live:** every new message of a joined group, as Telegram delivers it.
- **Reply context:** when a message answers an older one that is not stored, the parent is fetched (up to 3 levels)
  and stored as context only; the engine reads it but does not analyse it as a new message.
- **Stored:** the group (`tg_chats`), the sender (`tg_users`: names, usernames, phone when visible to the account,
  bio), and the message (`tg_messages`: text, reply links, forward and media metadata, and the raw Telegram object).
  Media files are not downloaded.
- **Profiles:** a background worker asks Telegram for each new sender's bio, one user every 3 seconds.
- **Flood limits:** when Telegram says "wait N seconds" the crawler waits and retries (up to 3 times, up to
  15 minutes); longer waits skip the step.
- Messages older than `DATA_RETENTION_DAYS` (180) are not stored, and the retention job deletes older rows.

## 3 and 4. Collecting and waiting (`engine.py`, `windowing.py`)

- Every 20 seconds the engine reads new archive rows (by row id, so nothing is read twice) and queues them per chat.
  Service messages, context-only parents and chats without an active source are left out.
- A chat is analysed when it has **50 new messages** (`NE_TRIGGER_COUNT`). Whole batches of 50 are taken; the rest
  waits for the next batch. There is an optional time limit (`NE_MAX_WAIT_MINUTES`) that is off by default.

## 5. Reading (`extract.py`, prompt `NEED_SYSTEM`)

- One LLM call per 50 messages. The model sees the group name, the 10 previous messages as context, the parents of
  replies, and "N hours later" markers for gaps. Bots and empty messages are removed first.
- It is asked for every **person** with a need that buying something could satisfy, stated or implied, even weak
  ones ("missing a real customer is worse than a weak lead"), each tied to message ids. It also labels the cases to
  discard: already solved, joke, seller, curiosity, advice giver.
- For each need it writes search phrases for possible solutions, phrases for the problem, and checkable requirements
  (must or nice to have), plus budget and city when stated.
- The result is a **need card**. A new card for the same person in the same chat is merged with the earlier one when
  the evidence overlaps or the meaning is close, so a conversation over several batches stays one need.

## 6. Matching a need to products (`catalog.py`, `retrieve.py`, `verify.py`, `scoring.py`)

- **Product cards.** Every active product has a card written once by the LLM (what it is, other names, problems it
  solves) and embedding vectors; they are rebuilt when the product changes.
- **Candidates, without the LLM.** The need's phrases are embedded and compared with all product vectors, and a
  keyword search (BM25) adds exact-word hits. The cut-off adapts to how similar the best products are, instead of
  a fixed top-K. Only products of sellers allowed for this chat take part.
- **Checklist, with the LLM.** For each candidate the model answers: does it solve the need (yes, partly, no), and is
  each requirement met, unmet or unknown. It gives no numbers.
- **Score, in code.** From the checklist, the budget (price against the stated budget) and the city. Matches under the
  minimum score are dropped; at most 10 products per opportunity.
- **New products** are matched backwards against open needs, so a product added today can still get last week's lead.

## 7 to 9. From the engine to the seller

- For the best products a reply is drafted in the seller's saved style, with a tracked link to the product.
- The opportunity is written to `need_engine.opportunities`; when the need later changes (resolved, expired after
  7 days) it is published again with the same id.
- `sync_opportunities` imports it every 10 seconds, split per seller: `Customer` (the Telegram user), `Opportunity`,
  `AIAnalysis`, `OpportunityProductMatch`, `Evidence` (the messages). It also charges wallets from the engine's cost
  ledger.
- The seller sees the lead with the person's profile link and messages the person from their own Telegram. The system
  sends nothing on Telegram.

## Where things are kept

| What | Where |
|---|---|
| Sources (groups), their status and counters | panel table `discovery_monitoredcommunity` |
| Groups, users, messages, crawler heartbeat | schema `crawler`: `tg_chats`, `tg_users`, `tg_messages`, `collector_heartbeats` |
| Queue per chat, need cards and vectors, product cards and vectors, LLM cache, costs, opportunities | schema `need_engine` |
| The Telegram session | `data/telegram_crawler_session` (volume `appdata`) |

## Settings that change behavior most

| Setting | Default | Effect |
|---|---|---|
| `NE_TRIGGER_COUNT` / `NE_MAX_WAIT_MINUTES` | 50 / 0 (off) | when a chat is analysed |
| `NE_WINDOW_SIZE` / `NE_CONTEXT_MESSAGES` | 50 / 10 | messages per LLM call, earlier messages shown |
| `BACKFILL_HOURS` / `BACKFILL_LIMIT` | 24 / 200 | history fetched on join and on start |
| `TG_MAX_GROUPS` / `TG_JOIN_INTERVAL_SECONDS` | 100 / 60 | groups per account, time between joins |
| `TG_FETCH_PROFILES` / `TG_PROFILE_DELAY_SECONDS` | true / 3 | bio lookups |
| `TG_STORE_RAW` | true | keep the raw Telegram object of every message and user |
| `NE_NEED_TTL_DAYS` | 7 | how long a need stays open |

Join and request settings change how the account behaves towards Telegram; treat them like the anti-ban settings
in `AGENTS.md`.

## Known problems

Listed with their fixes in `docs/TELEGRAM_FIXES.md`.

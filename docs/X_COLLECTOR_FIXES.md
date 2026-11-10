# X collector: what to fix

Plan for making the X (Twitter) collector reliable and better at finding leads. Written on 2026-11-10 from a read of
the code (`workers/x_collector/`); nothing was run against X and there are no production numbers yet, so every item
says how it was verified. Update this file in the PR that fixes an item; delete it when the list is empty.

Related: phase 13 of `docs/CLEANUP_ROADMAP.md` (splitting the collector's large functions). This file is about
behavior; that phase is about structure. Fix behavior first, with tests, then refactor.

## How it works today

1. `query_source.collector_queries` builds search queries per product family (short phrases from the product, the
   LLM-written terms and queries learned from confirmed buyers), merged across sellers and capped per seller.
2. `SeenStore.schedule` picks 12 queries per cycle: never-run queries first, then by staleness, priority, kind and
   a learned success rate (Thompson sampling; queries that sellers keep marking irrelevant are dropped).
3. `XCliClient.search_top_past_week` searches the Top tab, Persian only, last 7 days, 20 posts per page, up to 10
   pages, until 40 valid posts.
4. The worker drops posts it has already seen, writes the rest to JSONL, and `x_ingest` loads them into the
   database, where need_engine filters them and asks the LLM which are real buying needs.

Decided (2026-11-10): the search stays on the **Top** tab. In the owner's experience Top gives better leads than
Latest, because X itself removes much of the spam and advertising. Top also means the 7-day window stays: a post
needs time to collect engagement before it shows up there, so a "since the last run" window would miss it.

## Fixes, in order

### 1. Errors must not look like "no results" (bug)

- Today: when the in-process search fails for any reason it falls back to the CLI (`client.py:295`), and when that
  fails too the error is caught and an empty list is returned (`client.py:329`). The worker then records a success
  for the account (`worker.py:344`). A rate limit, an expired session or missing cookies therefore looks like a
  query with no posts: the account is not rotated, no cooldown starts, and the ops status stays green.
- Fix: `search_top_past_week` raises `XCliError` with the right kind (`rate_limit`, `fatal` for authentication,
  `transient`) and returns only on a real answer from X. The worker's existing handlers then do the rotation and
  cooldown they already contain.
- Tests first: a client whose search raises a rate limit makes the worker call `record_rate_limit` and move to the
  next account; an authentication error calls `record_auth_error`; neither calls `record_success`.
- Verified: read in code, not run. This is the first fix because every number below depends on it.

### 2. X search works for one seller only (bug, unless intended)

- Today: `query_source.py:202` keeps only products of business 4 or with "دند" in the title or category path. Any
  other seller who turns on X search gets no queries and no message. Dental discovery phrases are also hardcoded
  (`families.py:36`, `query_source.py:132-146`).
- Open question for the owner: was this limit on purpose (cost control while testing)?
- Fix: remove the filter; sellers are already limited by the per-seller query cap and by their wallet balance. The
  dental phrases move out of the code into data the seller (or an operator) owns.
- Test first: two sellers with X search on, one of them not dental, both get queries.

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

### 5. Account cookies in process-wide environment variables

- Today: `client.py:163` writes the account's `auth_token` and `ct0` into `os.environ` for every search. They stay
  there for the next account and for any subprocess.
- Fix: pass them to the client directly, as the code already does on the next line.

### 6. Measure the funnel per query

- Needed before any change to volume: per query and per seller, posts fetched, new posts, passed the pre-filter,
  became an opportunity, marked relevant by the seller. The data exists (`XQueryDaily`, `x_filter_decisions`,
  `x_hit_results`, opportunity feedback); it is not shown together.

## Later, only after 1-3 and with numbers from 6

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

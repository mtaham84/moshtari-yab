# How the X pipeline works

From a seller's product to a lead in the panel. Written on 2026-11-10 from the code; defaults are the values in the
code, the server's `.env` may differ. Known problems are in `docs/X_COLLECTOR_FIXES.md` and are only pointed to here.

## The short version

```
seller turns X search on for a product
        │
        ▼
1. QUERIES      products → families → phrases → search queries        workers/x_collector  (service `x`)
2. SCHEDULE     pick 12 queries for this cycle
3. SEARCH       X "Top" tab, Persian, last 7 days, with a pooled account
4. STORE        new posts → JSONL → crawler.x_posts + x_post_products  x_ingest
        │
        ▼
5. FILTER       cheap rules, no LLM                                    need_engine  (service `engine`)
6. JUDGE        LLM: is this person a buyer, and does this product fit?
7. PUBLISH      opportunity → need_engine.opportunities
        │
        ▼
8. IMPORT       opportunity, customer, product match in the panel      sync_opportunities  (service `sync`)
9. SELLER       sees the lead, a drafted reply, gives feedback
        │
        └──► 10. LEARN: outcomes go back to step 1 and 2 (better queries, better order)
```

Compose enables accepted-lead reply discovery by default (`NE_X_THREAD_REPLIES`, `X_THREAD_COLLECT`;
either can be explicitly disabled in `.env`). Accepted, recent root posts queue a `conversation_id:<id>`
search. These searches use the same Top/week transport, weighted scheduler, wallet gates, account
rotation, pacing and daily cap. Only replies whose raw parent/conversation IDs link them to the requested
root are stored as replies; missing identifiers never imply a reply. The product-mode engine judges
these replies against the accepted products through its existing Luna analysis. No outbound X message
is sent. New searches wait when the daily cap is reached.
Reply analysis cache keys include the parent text, so identical replies under different posts cannot
reuse a judgment from the wrong context. Ordinary post cache keys are unchanged.

The three services only talk through the database and files: the collector never imports the engine's decisions, it
reads them from tables.

## What connects a product to a lead

The link is made at search time and carried all the way through:

1. Every query belongs to a family, and a family is a list of product ids of one seller.
2. Every post a query finds is written once per product of that family: table `crawler.x_post_products`
   (`tweet_id`, `product_id`, `query`). The post itself is stored once in `crawler.x_posts`.
3. The engine reads those rows as if each product had its own chat, `x:p:<product_id>`, and asks the LLM about the
   post **against that one product only**: is the author a buyer, and does this product fit (`yes`, `partly`, `no`)?
4. A fit becomes an opportunity for that product's seller with a fixed match score (0.9 for `yes`, 0.7 for `partly`,
   at most 0.5 when the need is weak). There is no vector search and no second "verify" call in this mode, because
   the product is already known.

So a lead is tied to a product because that product's own search found the post. This is `NE_X_MODE=product`, the
default. (`public` mode treats all posts as one stream and matches them to any product by embeddings, like Telegram;
`both` does both.)

## 1. Building queries (`query_source.py`, `families.py`, `intent_queries.py`, `llm_terms.py`)

Runs at the start of every cycle, so a product switched on in the panel is picked up without a restart.

- **Which products.** Products with `x_search_enabled`, whose seller has a positive wallet balance and is not blocked
  by the enforced minimum balance. `X_REQUIRE_WALLET=true` also blocks missing wallet rows. An empty
  `X_SELLER_ALLOWLIST` permits every funded seller; a populated list limits rollout to those business IDs.
  Wallet read failures stop query generation. There is no dental-only eligibility filter.
- **Taxonomy.** Luna assigns products only to existing IDs in the common business-type tree. Per-product
  responses cannot create or rename categories. Initial shared-tree generation remains bounded to three levels
  with at most ten children per generated parent. Manual categories remain supported.
- **Families.** Products of one seller in the same category node (first three levels of the category path) form a
  family. Queries are written per family, not per product, and the seller's free-text `search_notes` are passed
  along. A product without a category is a family of one.
- **Phrases.** Short Persian phrases (2 to 3 words) a buyer would type, of five kinds:

  | Kind | Example |
  |---|---|
  | A. buying intent, and product names | «خرید پیراهن مردانه» |
  | B. asking for advice | «لپتاپ چی بگیرم» |
  | C. a problem or pain, without naming the product | «دندون درد شدید» |
  | D. price and budget | «قیمت ایمپلنت دندان» |
  | E. experience and comparison | «تجربه لپتاپ استوک» |

  They come from four places: the product itself (type, category, title words, aliases from its engine card), an
  LLM call per family (`x_query_terms` prompt, cached until the family's content changes), built-in lists for
  dental and clothing, and phrases learned from confirmed buyers (step 10).
- **Checks.** A phrase is dropped when it is too broad (a lone «لباس»), too long, a weak word, or belongs to a
  sibling family of the same seller (a shirt query must not drift to trousers).
- **Balance.** At most six phrases per shared category child, chosen to differ from each other (MMR).
  Candidate lists from sellers sharing the child are interleaved and deduplicated, then searched once
  with each participating product's ownership. Each seller's query cap is applied separately.
- **Merge.** A query is `<phrase> lang:fa`. The same query from two families or two sellers becomes one search
  tagged with all their products. At most 150 queries per seller.

`X_COLLECT_QUERIES_FILE` (plain text, JSONL objects, or a JSON list) or `X_QUERY_SOURCE=registry` (the `XSearchQuery` table edited in the panel)
replace phrase generation. Registry queries require an enabled X-search product and the same wallet checks.
Static entries with `business_id` or `business_ids` use the same gate. Unowned entries, including plain-text
overrides, are rejected before any live search; use JSON or JSONL with ownership instead. Explicit `--mock`
runs can still use unowned fixture queries without contacting X. JSON example:

```json
[{"query": "لپتاپ لنوو", "business_id": "9", "product_ids": ["91"]}]
```

The worker reads fresh wallet balances before every query attempt (TTL zero, including retries). A shared query runs
while at least one owner remains funded, and only funded owners appear in the search billing metadata. Generated
queries carry `product_business_ids`, so blocked owners' product IDs are removed too. There is no persistent pause
flag: a top-up restores eligibility next cycle. Scheduling first reads history without inserting rows; only queries
that pass the gate are registered. Balance skips create no query runs, strikes or bandit penalties. Run/status stats
include `paused_businesses` and `skipped_queries_by_balance`.

## 2. Choosing what to search (`shared/collector_kit/seen.py`)

At most 12 queries per cycle (`X_COLLECT_QUERIES_PER_CYCLE`), one cycle every 5 minutes. X uses
`verified unique leads / completed searches` as its weight. A successful empty response counts as a search;
authentication, rate-limit and transport errors do not. One tweet found for several products or sellers counts
once per query. With fixed yields of 20/100 and 5/100, 100 allocations produce 80 and 20 searches. Priority,
query kind and elapsed time do not multiply X yield; other platforms keep their existing scheduler.

Smooth weighted round robin persists allocation credits across cycles/restarts. A query runs at most once per
cycle; when the next winner already ran, it waits for the next cycle. An untried query starts at the eligible sampled queries' weighted mean yield:
`sum(verified leads) / sum(completed searches)`. Queries with only failed requests remain untried. With no
sampled history, the starting score is 0.5 for every fresh query. This is an allocation score, not a promise of
50% of all searches. No artificial lead or search is inserted; after the first completed search, the query uses
its own observed yield. For example, 20/100 and 5/100 give a new query a 12.5% starting score. Dry-run exposes
both observed `lead_rate` and effective `score`.

Sampled zero-yield queries share 5% of allocations while positive scores share the rest proportionally.
If all scores are zero, allocation is equal.
Existing non-buyer penalties, dropping and ten-day evaluation strikes remain in force. Only an actual eligible
attempt consumes an allocation credit. Dry-run reads the same history and weights without changing the state.
The continuous loop retries temporary query-loading/database failures after the existing 120-second delay;
a successful empty search proceeds to the next normal cycle. Account rotation, caps and pacing are unchanged.

Collector SQLite/PostgreSQL state adds `error_runs` and a per-query/tweet lead ledger automatically. Existing
historical lead counters are retained; old duplicate rewards and past errors cannot be reconstructed reliably.
The new ledger prevents duplicate credit on newly synced decisions. Review historical counters before relying
on their initial weights; no destructive reset is performed automatically.

## 3. Searching (`client.py`, `account_pool.py`)

- **Startup.** Compose waits for healthy web, whose entrypoint completes migrations before serving requests.
- **Accounts.** A pool in `x_accounts` (cookies `auth_token` and `ct0`, a proxy each). The least-used available
  account is taken; each has a daily action limit (250) and a 15-minute rest after a rate limit. The daily
  counter resets in PostgreSQL at UTC midnight, both on refresh and on the next successful action.
- **The search.** X's own search endpoint through the `twitter-cli` library: tab **Top**, `lang:fa`, the last
  7 days, 20 posts a page, up to 10 pages, 1.2 to 2.5 seconds between pages, until 40 valid posts. A post is valid
  when it is new in this run, inside the 7 days and really Persian. Two pages in a row with nothing valid stop the
  search. The CLI transport is used when the native library or account cookies are unavailable. Native
  search errors propagate with `auth`, `rate_limit`, `transient`, or `fatal` kinds; authentication errors challenge
  the account and rate limits put it in cooldown. Neither is recorded as an empty success. The existing CLI 404
  `query` kind continues to skip only that query. Cookies stay in client arguments or the subprocess environment;
  proxy credentials are masked before logging or storing stats. `search_errors` counts failures by kind.
- **Limits.** 2,500 new posts a day, 2 to 5 seconds between queries, stop after 4 failures in a row.

## 4. Storing (`worker.py`, `x_ingest/`)

- Retweets, explicit advertising posts, political posts and posts older than 10 days are skipped. Buyer phrases
  such as «برای خرید» or «تخفیف داره؟», including links, are not hard promotional rules. Promotion drops are logged
  and counted in `promotional_posts_dropped`; this phase does not store dropped posts.
- A post already seen (state store, today's file, or the `XCrawledPost` table) is skipped. The seen-key is the post
  plus the product, including database hashes, so the same post can be kept for two products. Legacy unscoped
  hashes remain valid for unscoped searches and do not suppress a new product-specific hit.
- New posts are appended to `output/x_collected/<date>.jsonl` and `latest.jsonl`; `x_ingest` then upserts them into
  `crawler.x_posts` and `crawler.x_post_products`. A record savepoint makes database errors skip only that record
  (counted and logged), leaving earlier and later valid records intact. Manual replay:
  `python -m x_ingest --path data/x_collected/latest.jsonl`.
- Each search is recorded (`crawler.x_search_runs`) for the seller's product page, and can be charged
  (`X_SEARCH_FEE_TOMAN`, default 0).

## 5. Filtering without the LLM (`need_engine/engine.py`, `x_filters.py`)

Needs `NE_X_ENABLED=true`. Per product chat, each run:

- posts older than 7 days are dropped; at most 200 posts per run, newest first; at most 3 posts per author;
- the cheap filter checks language, length, advertising, political content, shop-like bios, and whether the text
  shares words with the catalog. It also gives a 0-100 score from intent words, topic and author.
- `NE_X_PREFILTER` decides what the filter does: `shadow` (default) only records its decision and lets every post
  through to the LLM; `on` really drops; `off` skips it. Decisions are kept in `need_engine.x_filter_decisions`.

## 6. Judging with the LLM (`extract.py`, prompt `NEED_SYSTEM_X_PRODUCT`)

- Posts go in batches of 8. The prompt gets the product's brief (title, type, what it is, aliases, problems it
  solves) and each post with a short author bio, and is told the posts are unrelated to each other.
- For every post it returns whether there is a need, how strong, and the fit to this product with a reason.
- The answer is cached by post text and product, so the same text is not paid for twice for one product.
- The cost is written to the engine's ledger with the seller's id; `sync` charges the wallet from there.
- Every post gets an outcome in `need_engine.x_hit_results`: `customer`, `not_fit` (a buyer of something else),
  `not_customer` (no extracted need), or `skipped`. A default/cached `fit="no"` with no need is
  `not_customer`; a need below the opportunity threshold is `not_fit`.

## 7 to 9. From the engine to the seller

- The engine writes the opportunity (need, author, post link, matched product, drafted reply) to
  `need_engine.opportunities`. A reply draft is cleaned of links and phone numbers and cut to X's length; the panel
  offers it through an X "intent" link that opens the reply box. The system never posts or sends a DM on X itself.
- `sync_opportunities` imports it into the panel: `Customer` (the X author), `Opportunity`, `AIAnalysis`,
  `OpportunityProductMatch`. X delivery rechecks the seller's wallet at this boundary. Eligible wallet rows are
  locked until the panel write commits, so a concurrent debit cannot invalidate the eligibility check. Zero,
  negative, missing-wallet and enforced-minimum balances receive no new panel lead or customer record, even if
  the shared search or engine analysis ran while the seller was funded. Other funded owners still receive their
  matching leads. This reads/locks balances without changing charging. Existing delivered leads remain available
  and can still be closed; topping up allows future deliveries. `X_REQUIRE_WALLET=false` permits the legacy
  collection fallback only, not unpaid delivery.
- The seller sees it in the opportunities list and on the product's X page (`/discovery/x/<product>/`), which
  shows the phrases searched and a log of every post found with its outcome. Sellers switch X search on per product
  there; operators see accounts and statistics under `/manage/` and `/ops/x/`.

## 10. Learning (`proqe.py`)

After every cycle the collector reads the new rows of `x_hit_results`:

- `customer`: the query that found the post is rewarded, and the post text is saved as an example of a real buyer;
- `not_customer`: the query is penalized (half weight) and the text saved as a counter-example;
- `not_fit`: nothing, because the person is a buyer, only not for this product.

When a family has at least 2 new buyer examples (and not more often than every 6 hours), an LLM call writes new
short queries from those examples, the queries that worked and the ones that were dropped. They join step 1 on
the next cycle. The scores feed the ordering in step 2.

## Where things are kept

| What | Where |
|---|---|
| Accounts, the editable query list, crawled-post hashes, daily query numbers | panel tables: `x_accounts`, `XSearchQuery`, `XCrawledPost`, `XQueryDaily` |
| Posts, post × product hits, search runs | schema `crawler`: `x_posts`, `x_post_products`, `x_search_runs` |
| Filter decisions, outcomes, LLM cache, costs, opportunities | schema `need_engine` |
| Seen posts, query history and scores, buyer examples, learned queries | `output/x_collector_state.sqlite3` |
| Raw collected posts | `output/x_collected/*.jsonl` |

## Settings that change behavior most

| Setting | Default | Effect |
|---|---|---|
| `NE_X_ENABLED` | false | the engine processes X posts at all |
| `NE_X_MODE` | product | per-product judging (this document) or `public` / `both` |
| `NE_X_PREFILTER` | shadow | cheap filter only records, or really drops (`on`) |
| `X_COLLECT_QUERIES_PER_CYCLE` / `X_COLLECT_INTERVAL_SECONDS` | 12 / 300 | how many searches, how often |
| `X_COLLECT_DAILY_CAP` | 2500 | new posts per day |
| `X_SELLER_ALLOWLIST` | empty | optional comma-separated business IDs for staged rollout |
| `X_REQUIRE_WALLET` | true | block sellers without wallet rows |
| `X_ACCOUNT_MAX_DAILY_ACTIONS` | 250 | searches per account per day |
| `X_QUERY_MAX_PER_TENANT` / `X_QUERY_MAX_PER_FAMILY` | 150 / 6 | family candidates are capped at six; sellers sharing a child share six queries total |
| `NE_X_BATCH_SIZE` / `NE_X_MAX_PER_RUN` | 8 / 200 | posts per LLM call, posts per engine run |

Changing the collector's limits changes how often accounts talk to X; see `AGENTS.md`, Domain notes.

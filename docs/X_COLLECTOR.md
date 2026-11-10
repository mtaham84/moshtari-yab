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

- **Which products.** Products with `x_search_enabled`, whose seller's wallet is not blocked. A hardcoded filter then
  keeps only the dental seller (fix 2).
- **Families.** Products of one seller in the same category node (first three levels of the category path) form a
  family. Queries are written per family, not per product, and the seller's free-text `search_notes` are passed
  along. A product without a category is a family of one.
- **Phrases.** Short Persian phrases (1 to 6 words) a buyer would type, of five kinds:

  | Kind | Example |
  |---|---|
  | A. buying intent, and product names | «خرید پیراهن مردانه» |
  | B. asking for advice | «لپ تاپ چی بگیرم» |
  | C. a problem or pain, without naming the product | «دندون درد شدید شبانه» |
  | D. price and budget | «قیمت ایمپلنت دندان» |
  | E. experience and comparison | «تجربه خرید لپ تاپ استوک» |

  They come from four places: the product itself (type, category, title words, aliases from its engine card), an
  LLM call per family (`x_query_terms` prompt, cached until the family's content changes), built-in lists for
  dental and clothing, and phrases learned from confirmed buyers (step 10).
- **Checks.** A phrase is dropped when it is too broad (a lone «لباس»), too long, a weak word, or belongs to a
  sibling family of the same seller (a shirt query must not drift to trousers).
- **Balance.** Up to 3 phrases per kind and 15 per family, chosen to differ from each other (MMR).
- **Merge.** A query is `<phrase> lang:fa`. The same query from two families or two sellers becomes one search
  tagged with all their products. At most 150 queries per seller.

`X_COLLECT_QUERIES_FILE` (a text file) or `X_QUERY_SOURCE=registry` (the `XSearchQuery` table edited in the panel)
replace all of this with a fixed list.

## 2. Choosing what to search (`shared/collector_kit/seen.py`)

12 queries per cycle (`X_COLLECT_QUERIES_PER_CYCLE`), one cycle every 5 minutes. The order:

- a query that never ran goes first;
- otherwise: time since its last run × priority × kind weight × a learned success rate. Kind weights favor buying
  intent (1.0) over problems (0.8), alternatives (0.7), complaints (0.6) and plain product names (0.4);
- the success rate is drawn at random from the query's history (Thompson sampling), so good queries run more often
  and new ones still get tried;
- a query that keeps producing non-buyers is dropped, and one that found no lead for 10 days gets a strike.

## 3. Searching (`client.py`, `account_pool.py`)

- **Accounts.** A pool in `x_accounts` (cookies `auth_token` and `ct0`, a proxy each). The least-used available
  account is taken; each has a daily action limit (250) and a 15-minute rest after a rate limit.
- **The search.** X's own search endpoint through the `twitter-cli` library: tab **Top**, `lang:fa`, the last
  7 days, 20 posts a page, up to 10 pages, 1.2 to 2.5 seconds between pages, until 40 valid posts. A post is valid
  when it is new in this run, inside the 7 days and really Persian. Two pages in a row with nothing valid stop the
  search. If the library fails, a command-line fallback is tried.
- **Limits.** 2,500 new posts a day, 2 to 5 seconds between queries, stop after 4 failures in a row.

## 4. Storing (`worker.py`, `x_ingest/`)

- Retweets, advertising posts, political posts and posts older than 10 days are skipped.
- A post already seen (state store, today's file, or the `XCrawledPost` table) is skipped. The seen-key is the post
  plus the product, so the same post can be kept for two products.
- New posts are appended to `output/x_collected/<date>.jsonl` and `latest.jsonl`; `x_ingest` then upserts them into
  `crawler.x_posts` and `crawler.x_post_products`.
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
  `not_customer`, or `skipped`.

## 7 to 9. From the engine to the seller

- The engine writes the opportunity (need, author, post link, matched product, drafted reply) to
  `need_engine.opportunities`. A reply draft is cleaned of links and phone numbers and cut to X's length; the panel
  offers it through an X "intent" link that opens the reply box. The system never posts or sends a DM on X itself.
- `sync_opportunities` imports it into the panel: `Customer` (the X author), `Opportunity`, `AIAnalysis`,
  `OpportunityProductMatch`.
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
| `X_ACCOUNT_MAX_DAILY_ACTIONS` | 250 | searches per account per day |
| `X_QUERY_MAX_PER_TENANT` / `X_QUERY_MAX_PER_FAMILY` | 150 / 15 | how many queries a seller and a family get |
| `NE_X_BATCH_SIZE` / `NE_X_MAX_PER_RUN` | 8 / 200 | posts per LLM call, posts per engine run |

Changing the collector's limits changes how often accounts talk to X; see `AGENTS.md`, Domain notes.

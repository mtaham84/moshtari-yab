# How the Instagram pipeline works

From a seller's products to a direct message and a reply. Written on 2026-11-10 from the code; defaults are the
values in the code, the server's `.env` and `docker-compose.yml` may differ. Nothing was run against Instagram.
Known problems are in `docs/INSTAGRAM_FIXES.md`.

## The short version

```
seller's active products (and pages or terms the seller added)
        │
        ▼
1. PLAN        products → families → hashtags, keywords, page searches     service `ig`
2. PICK        4 sources per seller per cycle (pages first, then learned)   (run_instagram_scraper)
3. FETCH       recent posts of each source, with a SCRAPER account
4. COMMENTS    posts ≤ 30 days old with enough comments → read comments
5. JUDGE       rules first, LLM only for unclear comments: is this a buyer?
6. LEAD        lead row + opportunity in the panel, scored 0-45
        │
        ▼
7. SEND        best new lead → one cold DM, with a SENDER account           service `ig-outreach`
8. REPLIES     inbox checked, replies and phone numbers saved               (run_instagram_outreach)
9. SELLER      sees leads, the chat and follow-ups on the outreach page
        │
        └──► sources that gave leads are picked more often (step 2)
```

Two kinds of accounts do the work and never swap roles: **SCRAPER** accounts read posts and comments, **SENDER**
accounts send messages. Every account talks through its own proxy and its own device profile.

## The difference from X and Telegram

- Instagram does not go through `need_engine`. The scraper judges comments itself and writes panel records directly.
- The lead is a **commenter under someone else's post**, usually a competitor's or a topic page's. The comment is
  often one word ("قیمت"), so the post's caption carries most of the meaning.
- It is the only platform where the system contacts the person itself (a DM). That is why most of the code here is
  about not getting accounts banned.

## 1. Planning what to look at (`instagram_families.py`)

- **Families.** Same grouping as X: a seller's active products with Instagram discovery on, grouped by category
  node. A hardcoded rule limits this to the dental seller (fix 1).
- **Plan.** Per family, one saved `InstagramFamilyPlan`: hashtags, keywords, terms for finding pages, words that
  signal a buyer, words that signal "not a buyer". It is rebuilt when the family's products or the seller's notes
  change, at most 8 new plans per seller per day. In production the plan is built **without the LLM**, from the
  seller's category names plus a built-in dental list; the LLM planner exists but only tests call it.
- **Sources.** A source is something to open on Instagram: a hashtag, a keyword search, a page, or a page search.
  They come from the plan, from terms and hashtags the seller typed on a product, and from pages the seller added
  (`InstagramPage`, also filled by page searches).

## 2. Picking sources for a cycle (`sample_sources`, `cycle.py`)

- A cycle runs every 15 minutes (compose) between 09:00 and 23:00 Tehran time. Sellers take turns; sellers in
  `INSTAGRAM_PRIORITY_BUSINESS_IDS` go first; a seller with an empty wallet is skipped.
- Up to 4 sources per seller per cycle: pages the seller added that were not checked in the last 20 hours come
  first (at most half the slots), the rest are chosen by a learned success rate per source (Thompson sampling, pages
  and hashtags favored over keywords, 20% reserved for trying less-proven sources).
- After each run the source's record is updated: leads raise its rate, an empty run lowers it. A hashtag or keyword
  that shows fewer than 3 usable posts in 15 is deleted for good and replaced by a variation of the best sources.

## 3 and 4. Posts and comments (`ig_scraper/sources.py`, `ig_scraper/comments.py`)

- Per source the scraper fetches 15 to 30 recent posts, newest first, and looks for 3 usable ones: at most 30 days
  old and with at least `--min-comments` comments (10 in compose).
- For a usable post it reads up to 50 comments, with each commenter's public fields (name, private or public,
  business account, follower count, bio).
- Posts and their comments are cached in `InstagramCrawledPost` and shared by all sellers: a post already read is
  not fetched again for 5 days. Cache rows are deleted after 30 days.
- Only comments from the last 5 days are considered.

## 5. Is this commenter a buyer? (`instagram_engine/analyzer.py`, `post_filters.py`)

In order, for each comment:

1. Dropped without cost: empty, emoji only, insults and spam, plain compliments, and comments containing one of the
   plan's negative words.
2. **Short comments are leads by rule.** A number, a single word, two short words, or the post's call-to-action word
   ("comment قیمت") counts as a lead with intent 0.90. The caption is sent to the LLM once per post to work out what
   the post offers and to draft a reply.
3. A longer comment containing a buyer word (the plan's or a built-in list) is a lead with intent 0.86.
4. Everything else is unclear and goes to the LLM in batches of 25, with the family's category, product titles and
   seller notes.

## 6. Lead and opportunity (`comments.py`, `services.py`, `instagram_scoring.py`)

- **Outreach score, 0 to 45**, decides the order of sending: public profile (15), kind of comment (up to 15),
  freshness within 5 days (up to 5), a bonus for known dental pages (4) and for the dental service mentioned (up to
  6). A comment older than the cut-off is not eligible.
- **Lead.** One `InstagramEngineLead` per person per seller. A newer comment by the same person updates the row.
- **Product.** The lead is tied to a product by word overlap between the comment plus caption and each product's
  name, Instagram terms and hashtags; no overlap means no product.
- **Opportunity.** `sync_instagram_lead_to_opportunity` writes the panel records (`Customer`, `Opportunity`,
  `AIAnalysis`, `OpportunityProductMatch`) and charges the seller's wallet for the LLM cost of that comment.
- Every source run is logged in `InstagramCrawlExecution`, and live activity in `InstagramLiveActivity`.

## 7. Sending a direct message (`outreach_engine.py`, `outreach/`)

One dispatcher (`ig-outreach`, or Celery when `INSTAGRAM_OUTREACH_DISPATCHER=celery`, never both). It wakes about
once a minute and does **at most one thing**:

1. **Queued messages first**: follow-ups and messages a seller or operator wrote on the outreach page.
2. **Otherwise one cold DM**: the best new lead of the last 5 days (highest outreach score), through the sender
   that has rested longest.

Before a cold DM is sent:

- the day's working shift must be active (a daily plan with breaks, `human_routine.py`);
- the text is built from the seller's pitch template with small variations, and must contain no link, mention or
  phone number;
- the recipient is checked: private accounts and empty "ghost" accounts are skipped;
- the sender behaves like a person first: opens the feed, searches, views the profile and a story;
- then the message is sent.

Pacing is stored in the database, not in sleeps:

| Limit | Value |
|---|---|
| Per sender, by account age | new: about 9 a day, 25-50 minutes apart; medium: about 18, 12-25 minutes; aged: about 35, 8-15 minutes |
| Across senders | one cold DM per 20 minutes plus jitter (`INSTAGRAM_OUTREACH_MIN_GAP_MINUTES`) |
| Compose flags | `--max-daily-dms 18`, `--delay-min 1500`, `--delay-max 3000` |

Errors are classified per sender (challenge, expired session, proxy, rate limit, recipient does not accept DMs), so
one broken account rests or is marked while the others continue.

## 8 and 9. Replies and the seller's page (`outreach/replies.py`, `apps/accounts/outreach_views.py`)

- During shift hours, every 5 to 10 minutes, the dispatcher opens each sender's inbox, saves replies to the lead's
  conversation, marks the lead `REPLIED` and extracts an Iranian phone number if the person sent one.
- The seller's outreach page shows leads, the conversation, senders and their state, and lets the seller edit the
  pitch, write a follow-up (queued, sent by the dispatcher) and export leads. Operators manage accounts, logins,
  proxies and a live log under `/manage/`.

## Accounts, proxies and anti-ban (`ig_accounts.py`, `ig_login.py`, `ig_client.py`, `proxies/`, `anti_ban.py`)

- Every instagrapi client is built in one place (`ig_client.new_client`), bound to the account's proxy and saved
  device profile. An account without its own proxy uses the shared pool, never the server's IP.
- Scraper limits per account per day: 350 requests, 60 searches, 150 posts; waits of 35-65 seconds between
  searches, 8-16 between posts, 3-6 after reading comments, and a 2.5-6.5 minute break every few actions.
- Account states: `ACTIVE`, `COOLDOWN`, `CHALLENGED`, `BANNED`, `DISABLED`, with a reason shown in the panel. Logins
  that need a code wait for it in the panel.
- Proxy configs are collected from Telegram channels, tested, assigned one per account and replaced when they die
  (`channel-config-sync`, `xray/`).

## Where things are kept

| What | Where |
|---|---|
| Plans, source statistics, pages, per-category targets | `InstagramFamilyPlan`, `InstagramSourceStat`, `InstagramPage`, `InstagramCategoryTarget` |
| Cached posts with comments, run log, live log | `InstagramCrawledPost`, `InstagramCrawlExecution`, `InstagramLiveActivity` |
| Leads with their conversation and send queue | `leads` (`InstagramEngineLead`) |
| Accounts, their events, proxies | `ig_accounts`, `ig_account_events`, `proxy_configs` |
| Pitch and consultant name per seller | `outreach_tenant_profiles` (older fallback: `data/sarv_outreach_config.json`) |
| What the seller sees | `Opportunity`, `Customer`, `AIAnalysis`, `OpportunityProductMatch` |

## Settings that change behavior most

| Setting | Default | Effect |
|---|---|---|
| `--interval`, `--min-comments` (compose) | 15 min, 10 | cycle frequency, comments a post needs |
| `INSTAGRAM_MAX_SOURCES_PER_TENANT_CYCLE` | 4 | sources per seller per cycle |
| `INSTAGRAM_MAX_DAILY_REQUESTS` / `_QUERIES` / `_POSTS` | 350 / 60 / 150 | scraper account limits per day |
| `INSTAGRAM_MAX_COMMENTS_PER_POST` | 50 | comments read per post |
| `INSTAGRAM_WINDOW_START_HOUR` / `_END_HOUR` | 9 / 23 | scraper working hours |
| `INSTAGRAM_OUTREACH_MIN_GAP_MINUTES` | 20 | gap between cold DMs across senders |
| `INSTAGRAM_OUTREACH_DISPATCHER` | command | which process sends DMs |
| `INSTAGRAM_PRIORITY_BUSINESS_IDS` | 4 | sellers scraped first |

Almost all of these are anti-ban settings: do not change them without the owner's explicit yes (`AGENTS.md`).

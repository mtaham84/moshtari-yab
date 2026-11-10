# Instagram pipeline: what to fix

Plan for making the Instagram pipeline work for every seller, find leads sooner and send fewer wrong messages.
Written on 2026-11-10 from a read of the code; nothing was run against Instagram and there are no production
numbers yet, so every item says how it was verified. Update this file in the PR that fixes an item; delete it when
the list is empty.

How the pipeline works: `docs/INSTAGRAM_PIPELINE.md`. Structural problems of the same code (string `tenant_id`,
leads as one wide row, unencrypted account secrets, large functions) are in the audit in `docs/CLEANUP_ROADMAP.md`
and are only referenced here.

Items marked **account** change what an account does on Instagram and need the owner's explicit yes (`AGENTS.md`,
Domain notes). A wrong cold DM is not only a wasted message: reports from people who did not ask are what gets
sender accounts restricted.

## Fixes, in order

### 1. Instagram works for the dental seller only (bug, unless intended)

- Today: `families_for_business` returns no families for a seller that is not dental whenever a dental seller
  exists (`apps/discovery/instagram_families.py:424-430`; "dental" = business 4, or «دند» in the name or domain).
  Every other seller gets no plan, no scraping and no leads, without a message.
- The same assumption is built into the rest: 10 of the 45 outreach points are dental bonuses
  (`instagram_scoring.py:73,89,166,185`), the plan adds a dental keyword list (`instagram_families.py:331`), and the
  default pitches are dental (`outreach_engine.py:49`).
- Open question for the owner: was the limit on purpose (cost or account capacity while testing)?
- Fix: remove the gate; move the dental lists and bonuses into data that belongs to the seller (plan terms,
  priority services, target pages), so another seller can have their own. Roadmap finding E-2.
- Test first: two sellers, one not dental, both get families and targets.
- Verified: read in code.

### 2. Short comments become leads without a check (account)

- Today: any comment that is a number, one word, or two short words is a lead with intent 0.90
  (`instagram_engine/analyzer.py:308-327`), after only the emoji, insult and compliment filters. «سلام», «کجا؟»,
  a tagged friend's name or «ممنون» under a competitor's post all pass unless a filter happens to list them. Each
  becomes an opportunity and a candidate for a cold DM.
- Fix: a short comment is a lead only when it answers the post, i.e. it is the caption's call-to-action word, a
  number when the caption asks for one, or a known buying word (price, how much, where, order). Other short comments
  go to the batched LLM review like long ones.
- Measure first: of the leads created by this rule, how many did sellers mark irrelevant, and how many DMs to them
  got a reply. The data is in `leads.matched_cta` (`short_trigger:...`) and the opportunity feedback.
- Verified: read in code; false-lead rate not measured.

### 3. New comments are seen up to five days late

- Today: a post whose comments are cached is not read again for 5 days (`ig_scraper/comments.py:127`), and only
  comments of the last 5 days count (`:190`). A comment written the day after the post was read is picked up four
  days later, at the edge of the window, with a freshness score near zero.
- Fix: re-read a post's comments sooner while the post is young (for example after 1 day in its first week), using
  the comment count already returned with the post list to skip posts with no new comments. That check costs no
  extra request.
- This changes how many comment requests a scraper makes, within the existing daily limits. **account**
- Verified: read in code.

### 4. A search term is deleted for good after one bad run

- Today: a hashtag or keyword that shows fewer than 3 usable posts among 15 is removed permanently
  (`ig_scraper/comments.py:340`, `cycle.py:202`). One quiet week, a failed fetch that returned few posts, or a
  raised `--min-comments` deletes terms that worked before.
- Fix: lower the source's learned rate instead (the sampler already does this for empty runs) and delete only after
  several bad runs spread over days.
- Verified: read in code.

### 5. The product is guessed by word overlap, and confidence numbers are constants

- Today: the lead's product is the one whose name words, terms or hashtags appear most in the comment and caption
  (`apps/discovery/services.py:137,229`). Under a competitor's post the caption describes the competitor's product,
  so the match is often by accident or empty. `category_confidence` and `confidence` are always 0.95
  (`services.py:244,316`), so the panel shows certainty that was never computed.
- Fix: the family is already known when the post is scraped; pass it to the sync and choose among that family's
  products. Store real values or leave the fields empty.
- Verified: read in code.

### 6. The scraper's judgment is separate from the engine's

- Today: X and Telegram leads go through `need_engine` (need card, checklist, score in code, cost ledger). Instagram
  has its own rules, its own LLM calls through the panel's client, and its own inline wallet debit. The caption
  analysis for short comments is not charged to the seller at all (`analyzer.py:327` returns cost 0).
- Fix: belongs to roadmap phase 10 (one LLM client, one way to charge). Listed here so Instagram-specific changes do
  not grow the second path further.

### 7. The plan never uses the LLM in production

- Today: `cycle.py:35` calls `ensure_family_plan` without an LLM function, so plans come from category names (plus
  the dental list). The LLM planner and its prompt are exercised only by tests.
- Decide: wire it in (better terms for non-dental sellers, a small cost per family, at most 8 a day) or delete it.
  Needed for fix 1 to give non-dental sellers useful sources.

### 8. Small

- `INSTAGRAM_DAILY_REQUEST_BUDGET` is set to 500 in `docker-compose.yml` and read in `instagram_families.py:23`, but
  nothing uses it; the real daily limit is `INSTAGRAM_MAX_DAILY_REQUESTS` (350). Remove the dead setting.
- A lead reserved for sending can stay in `PROCESSING` for ever after a restart (roadmap B-8, phase 7.3).
- The seller's "sync replies" button skips shift hours and spacing (roadmap D-2, phase 7.2).

## Later, with numbers

- A funnel per source and per seller: posts read, comments read, leads, DMs sent, replies, phone numbers. Most of it
  exists in `InstagramCrawlExecution`, `InstagramSourceStat` and `leads`; it is not shown as one funnel.
- Replies to comments as a second, lighter contact than a DM. **account**
- More sources per cycle or more scraper accounts. **account**

## Not looked at

- The anti-ban values themselves (delays, limits, tiers, warm-up journey): whether they are right can only be
  judged from ban and restriction history, not from code.
- Login, session and challenge handling (`ig_login.py`, `ig_session.py`) and the proxy assignment code.
- The prompts for unclear comments and for reply drafts.
- The pitch texts and message variations.

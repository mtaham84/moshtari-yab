"""ProQE: Progressive Query Expansion & Feedback Loop for Twitter/X Buyer Discovery.

- ExemplarStore: Persistent RAG exemplar memory for real confirmed customer tweets.
  Stores exact customer phrasing, colloquial vocabulary, and the search query that surfaced them.
  Persists across model/API switches.
- sync_bandit_feedback: Reads evaluations from need_engine.x_hit_results (joined with crawler.x_post_products
  and crawler.x_posts) and updates Thompson Sampling (Beta distribution alpha/beta) on SeenStore.
  Queries generating confirmed customers (outcome='customer') receive rewards (alpha += 1.0) and save
  exemplars. Non-customer outcomes receive penalties (beta += 0.5).
- expand_queries_with_proqe: Progressively expands queries using LLM with in-context RAG exemplars.
- load_proqe_queries: Returns cached expanded queries for a product.
- Negative exemplars (tweets judged not_customer) steer the LLM away from noisy phrasing; exemplars are ranked by
  lexical similarity to the product (not just recency); weak expanded queries are pruned by their bandit stats;
  re-expansion is triggered by new evidence; per-source precision is tracked in proqe_query_outcomes.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any

from need_engine.text import norm, tokens

log = logging.getLogger("x_collector.proqe")

PROQE_EXPANSION_SYSTEM = """You are the query-evolution engine (ProQE: Progressive Query Expansion) of a Persian buyer-discovery
system on X/Twitter. You see real tweets that were CONFIRMED as buyers (positive exemplars), tweets that were REJECTED
(negative exemplars) and the queries that already work. Your job: propose NEW search phrases that will surface more people
like the confirmed buyers and fewer like the rejected ones.

HARD RULES
1. Every phrase is 1, 2 or 3 Persian words. Never 4+. X search is exact-match, so each extra word makes a query rarer.
2. Prefer colloquial buyer searches, needs, recommendations and product phrases: «خرید پیراهن مردانه», «پیشنهاد لباس», «دندون درد», «لباس خنک», «لباس راحت», «پیراهن کژوال», «هودی گرم». NEVER write awkward, weird or purely negative terms like «نامناسب» or «بدفرم».
   The SELLER NOTES in the family description (ideal customer, wholesale, city) are binding.
3. Learn the VOCABULARY of the confirmed buyers (their verbs and nouns) and compress it into 1-3 word phrases. Do not copy
   whole sentences and do not return anything already in "Existing queries".
4. Avoid the vocabulary pattern of rejected tweets (news, jokes, politics, seller ads, generic chatter).
5. Prefer variations in the spirit of the best-performing queries (same topic noun, different companion word).
6. No operators, quotes, hashtags, mentions, emojis or English words.

Return 4 to 6 phrases covering pain, fit/style/use and useful topic names. Keep each phrase short and natural.

Return strictly JSON:
{
  "expanded_queries": [
    {"query": "short phrase", "kind": "problem"},
    {"query": "short phrase", "kind": "alternative"},
    {"query": "short phrase", "kind": "solution"}
  ]
}
"""


def _format_expansion_prompt(product_title: str, category: str, existing_queries: list[str],
                             exemplars: list[dict[str, Any]], negatives: list[dict[str, Any]] | None = None,
                             winners: list[str] | None = None, dropped_queries: list[str] | None = None) -> str:
    lines = [
        f"Product/Service: {product_title}",
        f"Category: {category}",
        "",
        "Existing queries already running:",
    ]
    for q in existing_queries[:5]:
        lines.append(f"- {q}")

    if exemplars:
        lines.append("\nReal Confirmed Customer Exemplars (Tweets that actually converted):")
        for i, ex in enumerate(exemplars, 1):
            q_info = f" (Discovered by query: {ex.get('query', '')})" if ex.get("query") else ""
            lines.append(f"Exemplar {i}: \"{ex['tweet_text']}\"{q_info}")
            if ex.get("reason"):
                lines.append(f"  Reason: {ex['reason']}")
    else:
        lines.append("\nNo confirmed customer exemplars yet. Generate initial conversational expansion based on likely user pain and informal requests.")

    if negatives:
        lines.append("\nRejected Tweets (surfaced by our queries but NOT real buyers):")
        for i, ex in enumerate(negatives, 1):
            lines.append(f"Rejected {i}: \"{ex['tweet_text']}\"")
    if winners:
        lines.append("\nBest performing queries so far:")
        for q in winners:
            lines.append(f"- {q}")

    if dropped_queries:
        lines.append("\nDO NOT REPEAT these phrases: they returned zero results in six searches:")
        lines.extend(f"- {q}" for q in dropped_queries[:30])

    lines.append("\nGenerate 4-6 NEW topical Persian search phrases of 1-3 words each (no duplicates of existing queries).")
    return "\n".join(lines)


class ExemplarStore:
    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS proqe_exemplars (
                    tweet_id TEXT NOT NULL,
                    product_id TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    query TEXT NOT NULL DEFAULT '',
                    tweet_text TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY(tweet_id, product_id)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS proqe_expanded_queries (
                    product_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'intent',
                    source TEXT NOT NULL DEFAULT 'proqe',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY(product_id, query)
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS proqe_expansion_state (
                    product_id TEXT PRIMARY KEY,
                    last_at TEXT NOT NULL,
                    exemplar_count INTEGER NOT NULL DEFAULT 0
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS proqe_query_outcomes (
                    query TEXT PRIMARY KEY,
                    is_proqe INTEGER NOT NULL DEFAULT 0,
                    customers INTEGER NOT NULL DEFAULT 0,
                    total INTEGER NOT NULL DEFAULT 0
                )
            """)
            cols = {r[1] for r in db.execute("PRAGMA table_info(proqe_exemplars)")}
            if "label" not in cols:
                db.execute("ALTER TABLE proqe_exemplars ADD COLUMN label TEXT NOT NULL DEFAULT 'customer'")
            db.execute("""
                CREATE TABLE IF NOT EXISTS proqe_synced_decisions (
                    chat_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    PRIMARY KEY(chat_id, message_id)
                )
            """)
            db.commit()

    NEGATIVE_CAP = 200  # per product; oldest rejected tweets are dropped first

    def add_exemplar(self, tweet_id: str | int, product_id: str, tweet_text: str,
                     query: str = "", category: str = "", reason: str = "", label: str = "customer") -> bool:
        if not tweet_text or not str(tweet_id).strip():
            return False
        label = "negative" if label == "negative" else "customer"
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""
                INSERT INTO proqe_exemplars (tweet_id, product_id, category, query, tweet_text, reason, label)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tweet_id, product_id) DO UPDATE SET
                    category=excluded.category,
                    query=excluded.query,
                    tweet_text=excluded.tweet_text,
                    reason=excluded.reason,
                    label=excluded.label
            """, (str(tweet_id).strip(), str(product_id).strip(), str(category or "").strip(),
                  str(query or "").strip(), str(tweet_text).strip(), str(reason or "").strip(), label))
            if label == "negative":
                db.execute("""
                    DELETE FROM proqe_exemplars WHERE product_id = ? AND label = 'negative' AND rowid NOT IN (
                        SELECT rowid FROM proqe_exemplars WHERE product_id = ? AND label = 'negative'
                        ORDER BY created_at DESC, rowid DESC LIMIT ?)
                """, (str(product_id), str(product_id), self.NEGATIVE_CAP))
            db.commit()
            return True

    def add_negative(self, tweet_id: str | int, product_id: str, tweet_text: str,
                     query: str = "", category: str = "", reason: str = "") -> bool:
        return self.add_exemplar(tweet_id, product_id, tweet_text, query, category, reason, label="negative")

    @staticmethod
    def _sim(a: set[str], b: set[str]) -> float:
        return len(a & b) / len(a | b) if a and b else 0.0

    def _ranked(self, label: str, product_id: str | None, category: str, limit: int,
                context: str = "") -> list[dict[str, Any]]:
        """Ranks stored tweets by: same product > similarity to the product context > same category > recency."""
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT tweet_id, product_id, category, query, tweet_text, reason FROM proqe_exemplars "
                "WHERE label = ? ORDER BY created_at DESC, rowid DESC LIMIT 300", (label,)
            ).fetchall()
        cat_tokens = [t.strip() for t in (category or "").split("/") if len(t.strip()) >= 2]
        ctx = set(tokens(context or category))
        scored = []
        for idx, r in enumerate(rows):
            score = 0.5 / (1 + idx)
            if product_id and r[1] == str(product_id):
                score += 3.0
            if cat_tokens and any(t in (r[2] or "") for t in cat_tokens):
                score += 1.0
            score += 2.0 * self._sim(ctx, set(tokens(f"{r[4]} {r[3]} {r[2]}")))
            scored.append((score, r))
        scored.sort(key=lambda x: -x[0])
        return [{"tweet_id": r[0], "product_id": r[1], "category": r[2], "query": r[3],
                 "tweet_text": r[4], "reason": r[5]} for _, r in scored[:limit]]

    def get_exemplars(self, product_id: str | None = None, category: str = "", limit: int = 3,
                      context: str = "") -> list[dict[str, Any]]:
        return self._ranked("customer", product_id, category, limit, context)

    def get_negatives(self, product_id: str | None = None, category: str = "", limit: int = 3,
                      context: str = "") -> list[dict[str, Any]]:
        return self._ranked("negative", product_id, category, limit, context)

    def count_exemplars(self, product_id: str) -> int:
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT count(*) FROM proqe_exemplars WHERE product_id = ? AND label = 'customer'",
                              (str(product_id),)).fetchone()[0]

    def winning_queries(self, product_id: str, limit: int = 5) -> list[str]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT query, count(*) c FROM proqe_exemplars WHERE product_id = ? AND label = 'customer' "
                "AND query != '' GROUP BY query ORDER BY c DESC, max(created_at) DESC LIMIT ?",
                (str(product_id), limit)).fetchall()
        return [r[0] for r in rows]

    # --- expansion scheduling -------------------------------------------------
    def expansion_due(self, product_id: str, min_new: int = 2, min_interval_hours: float = 6.0) -> bool:
        """True when there is evidence and either no expansion ever ran, or enough new exemplars arrived since."""
        count = self.count_exemplars(product_id)
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute("SELECT last_at, exemplar_count FROM proqe_expansion_state WHERE product_id = ?",
                             (str(product_id),)).fetchone()
        if not row:
            return True      # cold start: the very first expansion works from the product alone, exemplars refine it later
        if count == 0:
            return False
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(row[0])).total_seconds() / 3600
        except ValueError:
            return True
        return age_h >= min_interval_hours and count - int(row[1]) >= min_new

    def mark_expanded(self, product_id: str) -> None:
        count = self.count_exemplars(product_id)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO proqe_expansion_state (product_id, last_at, exemplar_count) VALUES (?, ?, ?) "
                       "ON CONFLICT(product_id) DO UPDATE SET last_at=excluded.last_at, exemplar_count=excluded.exemplar_count",
                       (str(product_id), datetime.now(timezone.utc).isoformat(), count))
            db.commit()

    # --- pruning & metrics ----------------------------------------------------
    def prune_expanded_queries(self, seen_store: Any, product_id: str, max_keep: int = 30,
                               min_trials: float = 8.0, min_rate: float = 0.10) -> int:
        """Drops expanded queries that were tried enough and almost never produced a buyer, then caps the list."""
        platform = getattr(seen_store, "platform", "x")
        removed = 0
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute("SELECT query, created_at FROM proqe_expanded_queries WHERE product_id = ?",
                              (str(product_id),)).fetchall()
            try:
                runs = db.execute("SELECT query, alpha, beta FROM query_runs WHERE platform = ?", (platform,)).fetchall()
            except sqlite3.OperationalError:  # query_runs not created yet
                runs = []
            scored = []
            for query, created in rows:
                stats = (sum(r[1] - 1 for r in runs if r[0].startswith(query)),
                         sum(r[2] - 1 for r in runs if r[0].startswith(query)))
                pos, neg = float(stats[0]), float(stats[1]) / 0.5
                trials = pos + neg
                rate = pos / trials if trials else None
                scored.append((query, created, trials, rate))
            doomed = {q for q, _, t, r in scored if t >= min_trials and r is not None and r < min_rate}
            survivors = [x for x in scored if x[0] not in doomed]
            if len(survivors) > max_keep:
                # keep proven and untested ones; evict the weakest rate, then oldest
                survivors.sort(key=lambda x: (x[3] if x[3] is not None else 0.5, x[1]), reverse=True)
                doomed |= {x[0] for x in survivors[max_keep:]}
            for q in doomed:
                db.execute("DELETE FROM proqe_expanded_queries WHERE product_id = ? AND query = ?", (str(product_id), q))
                removed += 1
            db.commit()
        if removed:
            log.info("ProQE pruned %d weak expanded queries for product %s", removed, product_id)
        return removed

    def record_outcome(self, query: str, is_proqe: bool, customer: bool) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO proqe_query_outcomes (query, is_proqe, customers, total) VALUES (?, ?, ?, 1) "
                       "ON CONFLICT(query) DO UPDATE SET customers = customers + ?, total = total + 1, is_proqe = ?",
                       (query, int(is_proqe), int(customer), int(customer), int(is_proqe)))
            db.commit()

    def metrics(self) -> dict[str, Any]:
        """Buyer precision of ProQE-expanded queries versus the baseline queries (the number that proves it works)."""
        with closing(sqlite3.connect(self.path)) as db:
            out: dict[str, Any] = {}
            for name, flag in (("proqe", 1), ("baseline", 0)):
                c, t = db.execute("SELECT coalesce(sum(customers),0), coalesce(sum(total),0) FROM proqe_query_outcomes "
                                  "WHERE is_proqe = ?", (flag,)).fetchone()
                out[name] = {"customers": c, "judged": t, "precision": round(c / t, 4) if t else None}
            out["exemplars"] = db.execute("SELECT count(*) FROM proqe_exemplars WHERE label='customer'").fetchone()[0]
            out["negatives"] = db.execute("SELECT count(*) FROM proqe_exemplars WHERE label='negative'").fetchone()[0]
            out["expanded_queries"] = db.execute("SELECT count(*) FROM proqe_expanded_queries").fetchone()[0]
        return out

    def add_expanded_queries(self, product_id: str, queries: list[dict[str, str]]) -> int:
        count = 0
        with closing(sqlite3.connect(self.path)) as db:
            for item in queries:
                q = str(item.get("query", "")).strip()
                k = str(item.get("kind", "intent")).strip()
                if q:
                    db.execute("""
                        INSERT INTO proqe_expanded_queries (product_id, query, kind, source)
                        VALUES (?, ?, ?, 'proqe')
                        ON CONFLICT(product_id, query) DO UPDATE SET kind=excluded.kind
                    """, (str(product_id), q, k))
                    count += 1
            db.commit()
        return count

    def get_expanded_queries(self, product_id: str) -> list[dict[str, str]]:
        with closing(sqlite3.connect(self.path)) as db:
            rows = db.execute(
                "SELECT query, kind FROM proqe_expanded_queries WHERE product_id = ?",
                (str(product_id),)
            ).fetchall()
            return [{"query": r[0], "kind": r[1]} for r in rows]

    def is_decision_synced(self, chat_id: str, message_id: int) -> bool:
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT 1 FROM proqe_synced_decisions WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id)
            ).fetchone()
            return row is not None

    def mark_decision_synced(self, chat_id: str, message_id: int, outcome: str) -> None:
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "INSERT INTO proqe_synced_decisions (chat_id, message_id, outcome) VALUES (?, ?, ?) "
                "ON CONFLICT(chat_id, message_id) DO UPDATE SET outcome=excluded.outcome",
                (chat_id, message_id, outcome)
            )
            db.commit()


def _family_map(conn) -> dict[str, str]:
    """real product id -> family id (seller + first three category levels), so learning is shared inside a family."""
    from workers.x_collector.families import family_id, path_head
    try:
        rows = conn.execute("""SELECT p.id, p.business_id, COALESCE(c.full_path, '') FROM public.products_product p
                               LEFT JOIN public.products_category c ON c.id = p.category_id""").fetchall()
    except Exception:
        return {}
    return {str(r[0]): family_id(r[1], path_head(r[2]), r[0]) for r in rows}


def sync_bandit_feedback(seen_store: Any, dsn: str | None = None, crawler_schema: str = "crawler",
                         engine_schema: str = "need_engine") -> dict[str, int]:
    """Syncs LLM decisions from need_engine.x_hit_results to update Thompson Sampling bandit stats
    and extract real customer exemplars.
    Returns: {"rewarded": count, "penalized": count, "exemplars_added": count}
    """
    stats = {"rewarded": 0, "penalized": 0, "exemplars_added": 0, "negatives_added": 0}
    if seen_store is None or not getattr(seen_store, "path", None):
        return stats

    exemplar_store = ExemplarStore(seen_store.path)

    try:
        import psycopg
        from x_ingest.__main__ import _dsn
        conn_dsn = dsn or _dsn()
    except Exception as exc:
        log.debug("Database driver unavailable for bandit sync: %s", exc)
        return stats

    try:
        with psycopg.connect(conn_dsn, connect_timeout=5) as conn:
            has_hit = conn.execute("SELECT to_regclass(%s)", (f"{engine_schema}.x_hit_results",)).fetchone()[0]
            has_pp = conn.execute("SELECT to_regclass(%s)", (f"{crawler_schema}.x_post_products",)).fetchone()[0]
            has_posts = conn.execute("SELECT to_regclass(%s)", (f"{crawler_schema}.x_posts",)).fetchone()[0]
            if not (has_hit and has_pp and has_posts):
                return stats

            query_sql = f"""
                SELECT
                    r.chat_id,
                    r.message_id,
                    r.outcome,
                    r.reason,
                    h.query,
                    h.product_id,
                    p.text AS tweet_text
                FROM {engine_schema}.x_hit_results r
                JOIN {crawler_schema}.x_post_products h
                  ON ('x:p:' || h.product_id) = r.chat_id AND h.tweet_id = r.message_id
                JOIN {crawler_schema}.x_posts p
                  ON p.tweet_id = r.message_id
                ORDER BY r.decided_at DESC, r.message_id DESC
                LIMIT %s OFFSET %s
            """
            # Page through the history so a burst of >500 decisions between cycles is never skipped.
            family_of = _family_map(conn)
            page_size, max_pages = 500, 20
            proqe_cache: dict[str, list[str]] = {}
            for page in range(max_pages):
                rows = conn.execute(query_sql, (page_size, page * page_size)).fetchall()
                if not rows:
                    break
                fresh = 0
                for chat_id, message_id, outcome, reason, search_query, product_id, tweet_text in rows:
                    if exemplar_store.is_decision_synced(chat_id, int(message_id)):
                        continue
                    fresh += 1
                    pid = family_of.get(str(product_id), str(product_id))
                    if pid not in proqe_cache:
                        proqe_cache[pid] = [e["query"] for e in exemplar_store.get_expanded_queries(pid)]
                    is_proqe = any(search_query.startswith(q) for q in proqe_cache[pid])

                    if outcome == "customer":
                        seen_store.record_feedback(search_query, success=True, weight=1.0)
                        stats["rewarded"] += 1
                        exemplar_store.record_outcome(search_query, is_proqe, True)
                        if exemplar_store.add_exemplar(
                            tweet_id=message_id, product_id=pid, tweet_text=tweet_text,
                            query=search_query, reason=reason
                        ):
                            stats["exemplars_added"] += 1

                    elif outcome == "not_customer":
                        seen_store.record_feedback(search_query, success=False, weight=0.5)
                        stats["penalized"] += 1
                        exemplar_store.record_outcome(search_query, is_proqe, False)
                        if exemplar_store.add_negative(message_id, pid, tweet_text, query=search_query, reason=reason):
                            stats["negatives_added"] = stats.get("negatives_added", 0) + 1

                    # "not_fit" says the person is a buyer of something else, not that the query is bad:
                    # it is marked synced below but neither rewards nor penalizes the query.

                    exemplar_store.mark_decision_synced(chat_id, int(message_id), outcome)
                if fresh == 0 and page > 0:
                    break  # a whole page already synced: older history is done too

            for pid in proqe_cache:
                exemplar_store.prune_expanded_queries(seen_store, pid)

    except Exception as exc:
        log.warning("sync_bandit_feedback encountered an issue: %s", exc)

    if stats["rewarded"] or stats["penalized"]:
        log.info("ProQE Bandit Feedback Sync: +%d rewards, +%d penalties, +%d exemplars, +%d negatives | metrics=%s",
                 stats["rewarded"], stats["penalized"], stats["exemplars_added"], stats["negatives_added"],
                 exemplar_store.metrics())
    return stats


def expand_queries_with_proqe(
    product: Any,
    store_or_path: Any,
    llm: Any = None,
    model: str | None = None,
    existing_queries: list[str] | None = None,
    dropped_queries: list[str] | None = None,
) -> list[dict[str, str]]:
    """Generates progressive query expansions using LLM + RAG customer exemplars."""
    if isinstance(store_or_path, (str, Path)):
        exemplar_store = ExemplarStore(store_or_path)
    elif hasattr(store_or_path, "path"):
        exemplar_store = ExemplarStore(store_or_path.path)
    else:
        exemplar_store = store_or_path

    pid = str(getattr(product, "product_id", ""))
    cat = str(getattr(product, "category_path", "") or "")
    title = str(getattr(product, "title", "") or "")
    existing = existing_queries or []

    context = f"{title} {cat}"
    exemplars = exemplar_store.get_exemplars(product_id=pid, category=cat, limit=5, context=context)
    negatives = exemplar_store.get_negatives(product_id=pid, category=cat, limit=3, context=context)
    winners = exemplar_store.winning_queries(pid)

    if llm is None:
        return exemplar_store.get_expanded_queries(pid)

    user_prompt = _format_expansion_prompt(title, cat, existing, exemplars, negatives, winners, dropped_queries)
    try:
        data, _ = llm.complete_json(
            "x_query_expand",
            model or "gemini-2.5-flash",
            PROQE_EXPANSION_SYSTEM,
            user_prompt,
            max_tokens=2048,
            temperature=0.3,
            ref=f"proqe:{pid}"
        )
    except Exception as exc:
        log.warning("ProQE expansion LLM call failed for product %s: %s", pid, exc)
        return exemplar_store.get_expanded_queries(pid)

    expanded = []
    from workers.x_collector.intent_queries import clean_term, _FA
    for item in (data or {}).get("expanded_queries", []):
        if isinstance(item, dict):
            q = clean_term(item.get("query", ""))
            k = item.get("kind", "problem")
            if k not in {"problem", "complaint", "alternative", "solution", "intent"}:
                k = "problem"
            if 1 <= len(q.split()) <= 3 and len(q) >= 5 and _FA.search(q):
                expanded.append({"query": q, "kind": k})

    exemplar_store.mark_expanded(pid)
    if expanded:
        exemplar_store.add_expanded_queries(pid, expanded)
        log.info("ProQE expanded %d new queries for product %s", len(expanded), pid)

    return exemplar_store.get_expanded_queries(pid)


def load_proqe_queries(product_id: str, state_path: str | Path | None = None) -> list[dict[str, str]]:
    """Retrieves cached ProQE-expanded queries for a product."""
    if not state_path:
        state_path = os.getenv("X_COLLECT_STATE_FILE", "output/x_collector_state.sqlite3")
    try:
        store = ExemplarStore(state_path)
        return [q for q in store.get_expanded_queries(str(product_id)) if 1 <= len(q["query"].split()) <= 3]
    except Exception as exc:
        log.debug("Could not load ProQE queries for product %s: %s", product_id, exc)
        return []


def purge_long_queries(state_path: str | Path, max_words: int = 3) -> dict[str, int]:
    """Deletes every stored query longer than ``max_words`` positive words from the SQLite state (bandit + ProQE)."""
    from workers.x_collector.intent_queries import positive_word_count

    out = {"query_runs": 0, "proqe_expanded_queries": 0, "proqe_query_outcomes": 0}
    with closing(sqlite3.connect(state_path)) as db:
        for table, col in (("query_runs", "query"), ("proqe_expanded_queries", "query"),
                           ("proqe_query_outcomes", "query")):
            try:
                rows = db.execute(f"SELECT DISTINCT {col} FROM {table}").fetchall()
            except sqlite3.OperationalError:
                continue
            for (q,) in rows:
                if positive_word_count(q) > max_words:
                    db.execute(f"DELETE FROM {table} WHERE {col} = ?", (q,))
                    out[table] += 1
        db.commit()
    return out


def purge_legacy_product_state(state_path: str | Path, dry_run: bool = True) -> dict[str, int]:
    """Remove per-product ProQE state after the collector switches to stable family IDs (``f…``)."""
    out: dict[str, int] = {}
    with closing(sqlite3.connect(state_path)) as db:
        for table in ("proqe_expanded_queries", "proqe_exemplars", "proqe_expansion_state"):
            try:
                ids = [r[0] for r in db.execute(
                    f"SELECT DISTINCT product_id FROM {table} WHERE product_id NOT LIKE 'f%'"
                )]
            except sqlite3.OperationalError:
                out[table] = 0
                continue
            if ids and not dry_run:
                db.executemany(f"DELETE FROM {table} WHERE product_id = ?", [(pid,) for pid in ids])
            out[table] = len(ids)
        if not dry_run:
            db.commit()
    return out

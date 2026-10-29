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

from need_engine.text import norm

log = logging.getLogger("x_collector.proqe")

PROQE_EXPANSION_SYSTEM = """You are a search query expansion specialist using the ProQE (Progressive Query Expansion) framework for Persian Twitter/X buyer discovery.
Your objective is to expand search queries for a product/service by learning from real Persian tweets that were confirmed as real buying customers.

Guidelines:
1. Twitter users never use textbook or catalog terms. They use colloquial Persian, slang, informal complaints, personal pain descriptions, or ask for recommendations.
2. Observe the provided "Real Customer Exemplars" (real converting tweets) to learn:
   - Specific Persian verbs and colloquial expressions (e.g., «دندونم تیر میکشه», «لپ تاپم بالا نمیاد», «سنگ نما شوره زده»).
   - How real people ask questions or complain without mentioning brands.
3. Generate 3 to 6 NEW diverse, entity-blind search queries specifically targeting high-intent leads across 4 angles:
   - problem: Pain symptoms or situational frustrations (e.g., "دیوار لابی شوره زده", "هنگ کردن لپ تاپ حین رندر")
   - complaint: Dissatisfaction with current setup (e.g., "خسته شدم از مسکن", "دیگه نمیکشه لپ تاپم")
   - alternative: Informal advice seeking (e.g., "کسی راهی بلده برای ترمیم سنگ", "برای این درد دندون چی پیشنهاد میدین")
   - solution: Implicit solution expressions (e.g., "روش جلوگیری از نفوذ آب به سنگ", "راهکار بالا بردن سرعت سیستم")

Return strictly JSON:
{
  "expanded_queries": [
    {"query": "...", "kind": "problem"},
    {"query": "...", "kind": "complaint"},
    {"query": "...", "kind": "alternative"},
    {"query": "...", "kind": "solution"}
  ]
}
"""


def _format_expansion_prompt(product_title: str, category: str, existing_queries: list[str],
                             exemplars: list[dict[str, Any]]) -> str:
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

    lines.append("\nGenerate 3-6 NEW high-converting Persian Twitter search queries (no duplicates of existing).")
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
                CREATE TABLE IF NOT EXISTS proqe_synced_decisions (
                    chat_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    outcome TEXT NOT NULL,
                    PRIMARY KEY(chat_id, message_id)
                )
            """)
            db.commit()

    def add_exemplar(self, tweet_id: str | int, product_id: str, tweet_text: str,
                     query: str = "", category: str = "", reason: str = "") -> bool:
        if not tweet_text or not str(tweet_id).strip():
            return False
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""
                INSERT INTO proqe_exemplars (tweet_id, product_id, category, query, tweet_text, reason)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tweet_id, product_id) DO UPDATE SET
                    category=excluded.category,
                    query=excluded.query,
                    tweet_text=excluded.tweet_text,
                    reason=excluded.reason
            """, (str(tweet_id).strip(), str(product_id).strip(), str(category or "").strip(),
                  str(query or "").strip(), str(tweet_text).strip(), str(reason or "").strip()))
            db.commit()
            return True

    def get_exemplars(self, product_id: str | None = None, category: str = "", limit: int = 3) -> list[dict[str, Any]]:
        with closing(sqlite3.connect(self.path)) as db:
            results = []
            seen_ids = set()
            # 1. Product-specific exemplars
            if product_id:
                rows = db.execute(
                    "SELECT tweet_id, product_id, category, query, tweet_text, reason, created_at "
                    "FROM proqe_exemplars WHERE product_id = ? ORDER BY created_at DESC LIMIT ?",
                    (str(product_id), limit)
                ).fetchall()
                for r in rows:
                    seen_ids.add(r[0])
                    results.append({"tweet_id": r[0], "product_id": r[1], "category": r[2], "query": r[3], "tweet_text": r[4], "reason": r[5]})

            # 2. Category-matching exemplars
            if len(results) < limit and category:
                cat_tokens = [t.strip() for t in category.split("/") if len(t.strip()) >= 2]
                for cat_token in cat_tokens:
                    if len(results) >= limit:
                        break
                    rows = db.execute(
                        "SELECT tweet_id, product_id, category, query, tweet_text, reason, created_at "
                        "FROM proqe_exemplars WHERE category LIKE ? ORDER BY created_at DESC LIMIT ?",
                        (f"%{cat_token}%", limit - len(results))
                    ).fetchall()
                    for r in rows:
                        if r[0] not in seen_ids:
                            seen_ids.add(r[0])
                            results.append({"tweet_id": r[0], "product_id": r[1], "category": r[2], "query": r[3], "tweet_text": r[4], "reason": r[5]})

            # 3. Fallback to any recent confirmed customer exemplars
            if len(results) < limit:
                rows = db.execute(
                    "SELECT tweet_id, product_id, category, query, tweet_text, reason, created_at "
                    "FROM proqe_exemplars ORDER BY created_at DESC LIMIT ?",
                    (limit - len(results),)
                ).fetchall()
                for r in rows:
                    if r[0] not in seen_ids:
                        seen_ids.add(r[0])
                        results.append({"tweet_id": r[0], "product_id": r[1], "category": r[2], "query": r[3], "tweet_text": r[4], "reason": r[5]})

            return results

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


def sync_bandit_feedback(seen_store: Any, dsn: str | None = None, crawler_schema: str = "crawler",
                         engine_schema: str = "need_engine") -> dict[str, int]:
    """Syncs LLM decisions from need_engine.x_hit_results to update Thompson Sampling bandit stats
    and extract real customer exemplars.
    Returns: {"rewarded": count, "penalized": count, "exemplars_added": count}
    """
    stats = {"rewarded": 0, "penalized": 0, "exemplars_added": 0}
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
                ORDER BY r.decided_at DESC
                LIMIT 500
            """
            rows = conn.execute(query_sql).fetchall()

            for chat_id, message_id, outcome, reason, search_query, product_id, tweet_text in rows:
                if exemplar_store.is_decision_synced(chat_id, int(message_id)):
                    continue

                if outcome == "customer":
                    seen_store.record_feedback(search_query, success=True, weight=1.0)
                    stats["rewarded"] += 1

                    if exemplar_store.add_exemplar(
                        tweet_id=message_id,
                        product_id=product_id,
                        tweet_text=tweet_text,
                        query=search_query,
                        reason=reason
                    ):
                        stats["exemplars_added"] += 1

                elif outcome in ("not_customer", "not_fit"):
                    seen_store.record_feedback(search_query, success=False, weight=0.5)
                    stats["penalized"] += 1

                exemplar_store.mark_decision_synced(chat_id, int(message_id), outcome)

    except Exception as exc:
        log.warning("sync_bandit_feedback encountered an issue: %s", exc)

    if stats["rewarded"] or stats["penalized"]:
        log.info("ProQE Bandit Feedback Sync: +%d rewards, +%d penalties, +%d exemplars",
                 stats["rewarded"], stats["penalized"], stats["exemplars_added"])
    return stats


def expand_queries_with_proqe(
    product: Any,
    store_or_path: Any,
    llm: Any = None,
    model: str | None = None,
    existing_queries: list[str] | None = None
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

    exemplars = exemplar_store.get_exemplars(product_id=pid, category=cat, limit=3)

    if llm is None:
        return exemplar_store.get_expanded_queries(pid)

    user_prompt = _format_expansion_prompt(title, cat, existing, exemplars)
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
            if len(q.split()) >= 2 and _FA.search(q):
                expanded.append({"query": q, "kind": k})

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
        return store.get_expanded_queries(str(product_id))
    except Exception as exc:
        log.debug("Could not load ProQE queries for product %s: %s", product_id, exc)
        return []

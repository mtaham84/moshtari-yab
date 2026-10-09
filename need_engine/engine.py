"""Orchestration: ingest new messages → analyse ready chats → match → emit Opportunity objects.

Every emitted Opportunity is published to ``<state schema>.opportunities`` (the panel imports it with
``manage.py sync_opportunities``); an extra sink (e.g. a JSONL file for the demo) can be plugged in.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from need_engine.catalog import Catalog
from need_engine.config import EngineConfig
from need_engine.embeddings import Embedder
from need_engine.extract import extract_window, extract_x_batch, remember
from need_engine.llm import LLMClient, QuotaExhausted
from need_engine.retrieve import retrieve
from need_engine.schemas import (STRENGTH_RANK, STRENGTH_WEIGHT, Candidate, ChatMessage, Cost, Evidence, MatchedProduct,
                                 NeedCard, NeedOut, Opportunity, Source)
from need_engine.sources import MessageSource, ProductSource, XMessageSource, message_source, product_source
from need_engine.store import Store
from need_engine.verify import draft_reply, evidence_messages, verify_need, verify_new_product
from need_engine.windowing import build_windows, is_ready
from need_engine.x_filters import cheap_x_filter

log = logging.getLogger("need_engine")

Sink = Callable[[Opportunity], None]


class JsonlSink:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, opp: Opportunity) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(opp.model_dump_json() + "\n")


@dataclass
class RunReport:
    ingested: int = 0
    analysed_messages: int = 0
    windows: int = 0
    needs_new: int = 0
    needs_updated: int = 0
    opportunities: list[Opportunity] = field(default_factory=list)
    products_changed: int = 0
    cost_messages_toman: float = 0.0
    cost_matching_toman: float = 0.0
    errors: list[str] = field(default_factory=list)
    x_prefilter_dropped: dict[str, int] = field(default_factory=dict)
    x_posts_sent_to_llm: int = 0
    x_batches: int = 0
    x_single_post_retries: int = 0
    x_reply_drafts: int = 0

    @property
    def toman_per_message(self) -> float:
        return self.cost_messages_toman / max(1, self.analysed_messages)

    def summary(self) -> str:
        return (f"ingested={self.ingested} analysed={self.analysed_messages} windows={self.windows} "
                f"needs new/updated={self.needs_new}/{self.needs_updated} opportunities={len(self.opportunities)} "
                f"products_changed={self.products_changed} cost: messages={self.cost_messages_toman:,.1f} "
                f"({self.toman_per_message:,.2f}/msg) matching={self.cost_matching_toman:,.1f} toman"
                + (f" x_posts_to_llm={self.x_posts_sent_to_llm} x_batches={self.x_batches} x_prefilter_dropped={self.x_prefilter_dropped}"
                   if self.x_posts_sent_to_llm or self.x_prefilter_dropped else "")
                + (f" errors={len(self.errors)}" if self.errors else ""))


def _message_url(m: ChatMessage) -> str | None:
    if m.url:
        return m.url
    if m.chat_username:
        return f"https://t.me/{m.chat_username}/{m.message_id}"
    cid = m.chat_id[4:] if m.chat_id.startswith("-100") else m.chat_id.lstrip("-")
    return f"https://t.me/c/{cid}/{m.message_id}" if cid.isdigit() else None


class NeedEngine:
    def __init__(self, cfg: EngineConfig | None = None, messages: MessageSource | None = None,
                 products: ProductSource | None = None, sink: Sink | None = None,
                 mock_llm: Callable[[str, str, str], dict] | None = None, store: Store | None = None):
        self.cfg = cfg or EngineConfig()
        self.store = store or Store(self.cfg.database_url, self.cfg.state_schema)
        self.llm = LLMClient(self.cfg, self.store, mock=mock_llm)
        self.emb = Embedder(self.cfg, self.llm)
        self.catalog = Catalog(self.cfg, self.store, self.llm, self.emb)
        self.messages = messages or message_source(self.cfg)
        self.products = products or product_source(self.cfg)
        self.sink = sink or (JsonlSink(self.cfg.output_jsonl) if self.cfg.output_jsonl else None)

    # ── steps ───────────────────────────────────────────────────────────────
    def ingest(self) -> int:
        total = self._ingest_source(self.messages, "fetch_cursor")
        if self.cfg.x_enabled:
            x_source = XMessageSource(self.cfg)
            try:
                total += self._ingest_source(x_source, "fetch_cursor_x", self.cfg.x_ingest_max_per_run)
            finally:
                conn = getattr(getattr(x_source, "db", None), "conn", None)
                if conn is not None:
                    conn.close()
        return total

    def _ingest_source(self, source: MessageSource, cursor_key: str, limit: int | None = None) -> int:
        cursor = int(self.store.get(cursor_key, 0))
        total = 0
        remaining = limit
        while remaining is None or remaining > 0:
            batch_limit = self.cfg.fetch_batch if remaining is None else min(self.cfg.fetch_batch, remaining)
            batch = source.fetch_after(cursor, batch_limit)
            if not batch:
                break
            total += self.store.add_pending(batch)
            cursor = max(m.row_id for m in batch)
            self.store.set(cursor_key, cursor)
            if remaining is not None:
                remaining -= len(batch)
            if len(batch) < batch_limit:
                break
        return total

    def ingest_x(self, source: MessageSource | None = None) -> int:
        if not self.cfg.x_enabled:
            return 0
        x_source = source or XMessageSource(self.cfg)
        try:
            return self._ingest_source(x_source, "fetch_cursor_x", self.cfg.x_ingest_max_per_run)
        finally:
            db = getattr(x_source, "db", None)
            if source is None and db is not None and hasattr(db, "close"):
                db.close()

    def sync_products(self, report: RunReport) -> None:
        changed = self.catalog.sync(self.products.all())
        report.products_changed = len(changed)
        if not changed or not self.store.get("catalog_initialised", False):
            self.store.set("catalog_initialised", True)  # first sync: nothing to back-match yet
            return
        needs = self.store.open_needs()
        for pid in changed:
            hits, toman = verify_new_product(pid, needs, self.catalog, self.llm, self.cfg)
            report.cost_matching_toman += toman
            for need_id, mp in hits.items():
                n = self.store.need(need_id)
                if n is None:
                    continue
                prev = self.store.emitted(need_id)
                matches = [MatchedProduct.model_validate(x) for x in json.loads(prev[1])["matched_products"]] if prev else []
                matches = [m for m in matches if m.product_id != pid] + [mp]
                self._finish_and_emit(n, matches, report, extra_cost=toman / max(1, len(hits)))

    def _match(self, n: NeedCard, qvecs: np.ndarray | None, report: RunReport) -> None:
        ret = retrieve(n, qvecs, self.catalog, self.cfg)
        matches, toman, calls = verify_need(n, ret, self.catalog, self.store, self.llm, self.cfg)
        if n.chat_id.startswith("x:"):
            matches = [match for match in matches if match.match_score * 100 >= self.cfg.x_min_match_score]
        report.cost_matching_toman += toman
        n.cost_toman += toman
        n.llm_calls += calls
        self._finish_and_emit(n, matches, report)

    def _finish_and_emit(self, n: NeedCard, matches: list[MatchedProduct], report: RunReport, extra_cost: float = 0.0) -> None:
        if n.chat_id.startswith("x:"):
            matches = [match for match in matches if match.match_score * 100 >= self.cfg.x_min_match_score]
        matches = sorted(matches, key=lambda m: -m.match_score)[:self.cfg.max_products_per_opportunity]
        n.cost_toman += extra_cost
        if self.cfg.write_replies:
            reply_limit = self.cfg.x_reply_top_n if n.chat_id.startswith("x:") else self.cfg.reply_top_n
            for mp in matches[:reply_limit]:
                if mp.reply_draft is None:
                    mp.reply_draft, t = draft_reply(n, mp, self.catalog, self.store, self.llm, self.cfg)
                    report.x_reply_drafts += int(n.chat_id.startswith("x:"))
                    report.cost_matching_toman += t
                    n.cost_toman += t
                    n.llm_calls += 1
        self.store.update_need(n)
        if not matches:
            return
        opportunity = self._opportunity(n, matches)
        self._emit(opportunity, report)
        if (n.chat_id.startswith("x:") and self.cfg.x_thread_replies and matches
                and max(match.match_score for match in matches) * 100 >= self.cfg.x_thread_min_match_score):
            evidence = [self.store.message(n.chat_id, mid) for mid in n.evidence_ids]
            roots = [message for message in evidence if message and message.author_id == n.author_id and message.kind == "post"]
            root = max(roots, key=lambda message: message.date, default=None)
            if root and (datetime.now(timezone.utc) - min(root.date, datetime.now(timezone.utc))).total_seconds() <= self.cfg.x_thread_max_age_hours * 3600:
                root_id = root.message_id
                self.store.request_x_thread(root_id, n.author_id, n.need_id, n.need_id)

    def _opportunity(self, n: NeedCard, matches: list[MatchedProduct]) -> Opportunity:
        ev = evidence_messages(n, self.store)
        platform = next((m.platform for m in ev if m.platform), "telegram")
        title = next((m.chat_title for m in ev if m.chat_title), None)
        username = next((m.author_username for m in ev if m.author_username), None)
        profile_url = next((m.profile_url for m in ev if m.profile_url), None)
        author_id = next((m.author_id for m in ev if m.author_id), n.author_id)
        source_posted_at = max((m.date for m in ev), default=None)
        if source_posted_at and source_posted_at > datetime.now(timezone.utc):
            source_posted_at = datetime.now(timezone.utc)
        x_source = n.chat_id.startswith("x:")
        expires_at = (source_posted_at or n.updated_at) + timedelta(hours=self.cfg.x_need_ttl_hours) if x_source else n.updated_at + timedelta(days=self.cfg.need_ttl_days)
        best = matches[0].match_score if matches else 0.0
        priority = STRENGTH_WEIGHT[n.strength] * best
        if x_source and source_posted_at:
            age_hours = max(0.0, (datetime.now(timezone.utc) - source_posted_at).total_seconds() / 3600)
            priority = max(0.05, priority * 0.5 ** (age_hours / max(0.01, self.cfg.x_freshness_halflife_hours)))
        contacts, linked_identities = [], []
        if x_source and n.author_bio:
            import os
            from need_engine.contacts import extract_public_contacts
            from need_engine.schemas import LinkedIdentity

            contact_types = tuple(x.strip() for x in os.getenv("X_CONTACT_TYPES", "telegram,instagram,website").split(",") if x.strip())
            contacts = extract_public_contacts(n.author_bio, types=contact_types) if os.getenv("X_CONTACT_EXTRACT", "true").lower() in {"1", "true", "yes", "on"} else []
            if os.getenv("X_CONTACT_LINK_TELEGRAM", "true").lower() in {"1", "true", "yes", "on"}:
                tg_handles = {c.value.casefold() for c in contacts if c.type == "telegram"}
                if tg_handles:
                    try:
                        from need_engine.sources import _DB
                        db = _DB(self.cfg.database_url)
                        try:
                            schema = self.cfg.crawler_schema
                            if not schema.replace("_", "").isalnum() or not schema[0].isalpha():
                                raise ValueError("invalid crawler schema")
                            rows = db.all(f"SELECT user_id, username FROM {schema}.tg_users WHERE lower(username)=ANY(%s)", (list(tg_handles),))
                        finally:
                            db.conn.close()
                        linked_identities = [LinkedIdentity(platform="telegram", external_user_id=f"telegram_{row['user_id']}", username=row["username"], basis="bio_declared", confidence="self_declared_unverified") for row in rows if row.get("username")]
                    except Exception as exc:
                        log.warning("X contact identity lookup unavailable (%s)", type(exc).__name__)
            log.info("X contact extraction: channels=%d linked_identities=%d", len(contacts), len(linked_identities))
        return Opportunity(
            opportunity_id=n.need_id,
            status=n.status if n.status in ("open", "resolved", "expired") else "open",
            created_at=n.created_at, expires_at=expires_at,
            candidate=Candidate(external_user_id=author_id if platform == "x" else f"telegram_{author_id}", customer_name=n.author_name, username=username,
                                profile_url=profile_url or ((f"https://x.com/{username}" if platform == "x" else f"https://t.me/{username}") if username else None),
                                contacts=contacts, linked_identities=linked_identities),
            source=Source(platform=platform, chat_id=n.chat_id, chat_title=title, profile_url=f"https://x.com/{username}" if platform == "x" and username else None,
                          search_query=next((m.search_query for m in ev if m.search_query), None),
                          evidence=[Evidence(message_id=str(m.message_id), timestamp=m.date, author=m.author_name, text=m.text,
                                             url=_message_url(m)) for m in ev]),
            need=NeedOut(label=n.label, strength=n.strength, situation=n.situation, summary=n.need, requirements=n.requirements,
                         constraints={k: v for k, v in n.constraints.model_dump().items() if v not in (None, "")},
                         priority=round(priority, 3)),
            matched_products=matches,
            cost=Cost(toman=round(n.cost_toman, 2), llm_calls=n.llm_calls),
            source_posted_at=source_posted_at, source_posted_at_estimated=any(m.date_estimated for m in ev),
            source_query=next((m.search_query for m in ev if m.search_query), None))

    def _emit(self, opp: Opportunity, report: RunReport) -> None:
        body = opp.model_dump_json(exclude={"cost"})
        fp = hashlib.sha1(body.encode()).hexdigest()
        prev = self.store.emitted(opp.opportunity_id)
        if prev and prev[0] == fp:
            return
        self.store.mark_emitted(opp.opportunity_id, fp, opp.model_dump_json())
        self.store.publish(opp)
        if self.sink:
            self.sink(opp)
        report.opportunities.append(opp)

    def _close(self, n: NeedCard, report: RunReport) -> None:
        """A previously emitted opportunity became resolved/expired → emit the status change."""
        prev = self.store.emitted(n.need_id)
        if not prev:
            return
        opp = Opportunity.model_validate_json(prev[1])
        if opp.status != n.status and n.status in ("resolved", "expired"):
            opp.status = n.status
            self._emit(opp, report)

    def process(self, report: RunReport, now: datetime | None = None, flush: bool = False) -> None:
        now = now or datetime.now(timezone.utc)
        for chat_id in self.store.pending_chats():
            pending = self.store.pending(chat_id)
            if chat_id.startswith("x:"):
                thread_rows = [message for message in pending if message.kind in {"reply", "quote"}]
                if thread_rows:
                    log.warning("Skipping %d X thread rows: dedicated thread analyzer is not implemented; they remain unprocessed", len(thread_rows))
                    pending = [message for message in pending if message.kind not in {"reply", "quote"}]
                all_ordered = sorted(pending, key=lambda m: (m.date, m.row_id), reverse=self.cfg.x_process_order == "newest")
                if self.cfg.x_process_order not in {"newest", "oldest"}:
                    log.warning("Invalid NE_X_PROCESS_ORDER=%r; using newest", self.cfg.x_process_order)
                    all_ordered = sorted(pending, key=lambda m: (m.date, m.row_id), reverse=True)
                stale = [m for m in all_ordered if (now - min(m.date, now)).total_seconds() > self.cfg.x_max_post_age_hours * 3600]
                selected = [m for m in all_ordered if m not in stale][:max(0, self.cfg.x_max_per_run)]
                pending = selected
                decisions = {m.message_id: cheap_x_filter(m, self.cfg, self.catalog) for m in pending}
                for msg in stale:
                    decisions[msg.message_id] = type("Decision", (), {"keep": False, "reason": "stale", "signals": {"age_hours": round((now - msg.date).total_seconds() / 3600, 2)}})()
                    report.x_prefilter_dropped["stale"] = report.x_prefilter_dropped.get("stale", 0) + 1
                    log.info("Skipped stale X post %s", msg.message_id)
                if self.cfg.x_prefilter in {"shadow", "on"}:
                    self.store.drop_x_pending(stale, decisions, mode="stale")
                pending = [m for m in pending if m not in stale]
                for msg in pending:
                    decision = decisions[msg.message_id]
                    if self.cfg.x_prefilter in {"shadow", "on"}:
                        self.store.record_x_filter_decision(msg, decision.keep, decision.reason, decision.signals, self.cfg.x_prefilter)
                    if self.cfg.x_prefilter == "shadow" and not decision.keep:
                        report.x_prefilter_dropped[decision.reason] = report.x_prefilter_dropped.get(decision.reason, 0) + 1
                if self.cfg.x_prefilter == "on":
                    dropped = [m for m in pending if not decisions[m.message_id].keep]
                    self.store.drop_x_pending(dropped, decisions, mode="on")
                    pending = [m for m in pending if decisions[m.message_id].keep]
                counts: dict[str, int] = {}
                kept = []
                for msg in reversed(pending):
                    counts[msg.author_id] = counts.get(msg.author_id, 0) + 1
                    if counts[msg.author_id] <= self.cfg.x_max_posts_per_author_per_run:
                        kept.append(msg)
                    else:
                        decisions[msg.message_id] = type("Decision", (), {"reason": "author_throttle", "signals": {}})()
                        report.x_prefilter_dropped["author_throttle"] = report.x_prefilter_dropped.get("author_throttle", 0) + 1
                if self.cfg.x_prefilter == "on":
                    self.store.drop_x_pending([m for m in pending if m not in kept], decisions, mode="on")
                pending = [m for m in selected if m in kept]
            ready, why = is_ready(pending, now, self.cfg)
            if not (ready or flush):
                continue
            log.info("chat %s: analysing %d messages (%s)", chat_id, len(pending), why if ready else "flush")
            windows = build_windows(chat_id, pending, self.store, self.cfg)
            if chat_id.startswith("x:"):
                from need_engine.windowing import Window
                chunks = []
                for window in windows:
                    batch, chars = [], 0
                    for message in window.new:
                        if batch and (len(batch) >= max(1, self.cfg.x_batch_size) or chars + len(message.text) > self.cfg.x_batch_max_chars):
                            chunks.append(batch)
                            batch, chars = [], 0
                        batch.append(message)
                        chars += len(message.text)
                    if batch:
                        chunks.append(batch)
                windows = [Window("x:public", "X", batch, consumed=batch) for batch in chunks]
            for w in windows:
                try:
                    if w.new and w.new[0].platform == "x":
                        cards, toman, calls = extract_x_batch(w.new, self.llm, self.cfg, now)
                        report.x_batches += int(calls == 1)
                        report.x_single_post_retries += int(calls > 1)
                        report.x_posts_sent_to_llm += len(w.new)
                    else:
                        cards, toman = extract_window(w, self.llm, self.cfg, now)
                except QuotaExhausted:
                    raise
                except Exception as e:  # keep the messages pending; they are retried next run
                    report.errors.append(f"{chat_id}: {e}")
                    log.exception("window failed for chat %s", chat_id)
                    break
                report.windows += 1 if w.new else 0
                report.cost_messages_toman += toman
                report.analysed_messages += len(w.consumed)
                self.store.mark_analysed(chat_id, w.consumed, keep_recent=max(self.cfg.context_messages * 5, 50))
                for n, is_new, rematch in remember(cards, self.store, self.emb, self.cfg):
                    report.needs_new += int(is_new)
                    report.needs_updated += int(not is_new)
                    if rematch and n.status == "open" and STRENGTH_RANK[n.strength] >= STRENGTH_RANK[self.cfg.verify_min_strength]:
                        try:
                            self._match(n, self.store.query_vecs(n.need_id), report)
                        except QuotaExhausted:
                            raise
                        except Exception as e:
                            report.errors.append(f"{n.need_id}: {e}")
                            log.exception("matching failed for %s", n.need_id)
                    elif n.status in ("resolved", "expired"):
                        self._close(n, report)

    def expire(self, report: RunReport, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        refreshed = 0
        for n, _ in self.store.open_needs():
            if n.chat_id.startswith("x:"):
                evidence = [self.store.message(n.chat_id, mid) for mid in n.evidence_ids]
                newest = max((m.date for m in evidence if m is not None), default=n.updated_at)
                expires_at = min(newest, now) + timedelta(hours=self.cfg.x_need_ttl_hours)
            else:
                expires_at = n.updated_at + timedelta(days=self.cfg.need_ttl_days)
            if expires_at <= now:
                n.status = "expired"
                self.store.update_need(n)
                self._close(n, report)
            elif n.chat_id.startswith("x:") and refreshed < max(1, self.cfg.x_refresh_batch_size):
                refreshed += int(self._refresh_x_priority(n, now))

    def refresh_x_priorities(self, now: datetime | None = None) -> int:
        now = now or datetime.now(timezone.utc)
        refreshed = 0
        for n, _ in self.store.open_needs():
            if not n.chat_id.startswith("x:"):
                continue
            refreshed += int(self._refresh_x_priority(n, now))
            if refreshed >= max(1, self.cfg.x_refresh_batch_size):
                break
        return refreshed

    def _refresh_x_priority(self, n: NeedCard, now: datetime) -> bool:
        previous = self.store.emitted(n.need_id)
        if not previous:
            return False
        payload = Opportunity.model_validate_json(previous[1])
        if payload.status != "open":
            return False
        evidence = [self.store.message(n.chat_id, mid) for mid in n.evidence_ids]
        newest = max((m.date for m in evidence if m is not None), default=n.updated_at)
        newest = min(newest, now)
        age_hours = max(0.0, (now - newest).total_seconds() / 3600)
        base = STRENGTH_WEIGHT[n.strength] * max((m.match_score for m in payload.matched_products), default=0.0)
        payload.need.priority = round(max(0.05, base * 0.5 ** (age_hours / max(0.01, self.cfg.x_freshness_halflife_hours))), 3)
        payload.source_posted_at = newest
        payload.expires_at = newest + timedelta(hours=self.cfg.x_need_ttl_hours)
        self._emit(payload, RunReport())
        return True

    # ── entry points ────────────────────────────────────────────────────────
    def run_once(self, now: datetime | None = None, flush: bool = False) -> RunReport:
        report = RunReport()
        try:
            self.sync_products(report)
            report.ingested = self.ingest()
            self.process(report, now=now, flush=flush)
            self.expire(report, now=now)
        except QuotaExhausted as e:
            report.errors.append(f"quota: {e}")
            log.error("%s — pending messages stay queued and are processed on the next run", e)
        log.info(report.summary())
        return report

    def run_forever(self) -> None:  # pragma: no cover
        while True:
            self.run_once()
            time.sleep(self.cfg.poll_seconds)

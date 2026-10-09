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
from need_engine.extract import extract_window, remember
from need_engine.llm import LLMClient, QuotaExhausted
from need_engine.retrieve import retrieve
from need_engine.schemas import (STRENGTH_RANK, STRENGTH_WEIGHT, Candidate, ChatMessage, Cost, Evidence, MatchedProduct,
                                 NeedCard, NeedOut, Opportunity, Source)
from need_engine.sources import MessageSource, ProductSource, message_source, product_source
from need_engine.store import Store
from need_engine.verify import draft_reply, evidence_messages, verify_need, verify_new_product
from need_engine.windowing import build_windows, is_ready

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

    @property
    def toman_per_message(self) -> float:
        return self.cost_messages_toman / max(1, self.analysed_messages)

    def summary(self) -> str:
        return (f"ingested={self.ingested} analysed={self.analysed_messages} windows={self.windows} "
                f"needs new/updated={self.needs_new}/{self.needs_updated} opportunities={len(self.opportunities)} "
                f"products_changed={self.products_changed} cost: messages={self.cost_messages_toman:,.1f} "
                f"({self.toman_per_message:,.2f}/msg) matching={self.cost_matching_toman:,.1f} toman"
                + (f" errors={len(self.errors)}" if self.errors else ""))


def _message_url(m: ChatMessage) -> str | None:
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
        cursor = int(self.store.get("fetch_cursor", 0))
        total = 0
        while True:
            batch = self.messages.fetch_after(cursor, self.cfg.fetch_batch)
            if not batch:
                break
            total += self.store.add_pending(batch)
            cursor = max(m.row_id for m in batch)
            self.store.set("fetch_cursor", cursor)
            if len(batch) < self.cfg.fetch_batch:
                break
        return total

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
        report.cost_matching_toman += toman
        n.cost_toman += toman
        n.llm_calls += calls
        self._finish_and_emit(n, matches, report)

    def _finish_and_emit(self, n: NeedCard, matches: list[MatchedProduct], report: RunReport, extra_cost: float = 0.0) -> None:
        matches = sorted(matches, key=lambda m: -m.match_score)[:self.cfg.max_products_per_opportunity]
        n.cost_toman += extra_cost
        if self.cfg.write_replies:
            for mp in matches[:self.cfg.reply_top_n]:
                if mp.reply_draft is None:
                    mp.reply_draft, t = draft_reply(n, mp, self.catalog, self.store, self.llm, self.cfg)
                    report.cost_matching_toman += t
                    n.cost_toman += t
                    n.llm_calls += 1
        self.store.update_need(n)
        if not matches:
            return
        self._emit(self._opportunity(n, matches), report)

    def _opportunity(self, n: NeedCard, matches: list[MatchedProduct]) -> Opportunity:
        ev = evidence_messages(n, self.store)
        title = next((m.chat_title for m in ev if m.chat_title), None)
        best = matches[0].match_score if matches else 0.0
        return Opportunity(
            opportunity_id=n.need_id,
            status=n.status if n.status in ("open", "resolved", "expired") else "open",
            created_at=n.created_at, expires_at=n.updated_at + timedelta(days=self.cfg.need_ttl_days),
            candidate=Candidate(external_user_id=f"telegram_{n.author_id}", customer_name=n.author_name, username=n.author_username,
                                profile_url=f"https://t.me/{n.author_username}" if n.author_username else None),
            source=Source(platform="telegram", chat_id=n.chat_id, chat_title=title,
                          evidence=[Evidence(message_id=str(m.message_id), timestamp=m.date, author=m.author_name, text=m.text,
                                             url=_message_url(m)) for m in ev]),
            need=NeedOut(label=n.label, strength=n.strength, situation=n.situation, summary=n.need, requirements=n.requirements,
                         constraints={k: v for k, v in n.constraints.model_dump().items() if v not in (None, "")},
                         priority=round(STRENGTH_WEIGHT[n.strength] * best, 3)),
            matched_products=matches,
            cost=Cost(toman=round(n.cost_toman, 2), llm_calls=n.llm_calls))

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
            ready, why = is_ready(pending, now, self.cfg)
            if not (ready or flush):
                continue
            log.info("chat %s: analysing %d messages (%s)", chat_id, len(pending), why if ready else "flush")
            for w in build_windows(chat_id, pending, self.store, self.cfg):
                try:
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
        for need_id in self.store.expire_needs((now - timedelta(days=self.cfg.need_ttl_days)).timestamp()):
            n = self.store.need(need_id)
            if n:
                self._close(n, report)

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

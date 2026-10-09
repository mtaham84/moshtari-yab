"""Conversation window → need cards (one LLM call per window) and per-person memory (merge across windows/runs)."""
from __future__ import annotations

import json
import logging
from datetime import datetime

import numpy as np

from need_engine.config import EngineConfig
from need_engine.embeddings import Embedder
from need_engine.llm import LLMClient
from need_engine.prompts import NEED_SYSTEM
from need_engine.schemas import OPPORTUNITY_LABELS, STRENGTH_RANK, Constraints, NeedCard, Requirement
from need_engine.store import Store
from need_engine.text import norm
from need_engine.windowing import Window, render

log = logging.getLogger("need_engine.extract")

_LABELS = OPPORTUNITY_LABELS | {"resolved", "joke", "seller", "curiosity", "no_buy_complaint", "advice_giver", "past_need"}


def _confirmed_x_buyer(candidate: dict, evidence_ids: list[int], author_id: str, new_by_id: dict) -> bool:
    if candidate.get("buyer_intent_confirmed") is not True or candidate.get("is_opportunity") is not True:
        return False
    if candidate.get("label") not in {"explicit_need", "on_behalf_need"}:
        return False
    if candidate.get("strength") not in {"medium", "strong"}:
        return False
    if not str(candidate.get("need") or "").strip() or not candidate.get("solution_queries"):
        return False
    if candidate.get("author_type") != "individual" or candidate.get("promotional_content") is not False:
        return False
    cited = [new_by_id.get(message_id) for message_id in evidence_ids]
    suspicious_bio = ("official", "official account", "official store", "company", "brand", "support", "store", "shop",
                      "رسمی", "شرکت", "برند", "فروشگاه", "فروش", "پشتیبانی")
    return bool(cited) and all(message is not None and message.author_id == author_id and not message.author_verified
                                and not any(term in (message.author_bio or "").casefold() for term in suspicious_bio)
                                for message in cited)


def _int_or_none(v) -> int | None:
    try:
        f = float(str(v).replace(",", ""))
        return int(f) if f > 0 else None
    except (TypeError, ValueError):
        return None


def extract_window(w: Window, llm: LLMClient, cfg: EngineConfig, now: datetime) -> tuple[list[NeedCard], float]:
    """One LLM call. Returns validated (not yet merged) need cards and the call's cost in Toman."""
    if not w.new:
        return [], 0.0
    system = NEED_SYSTEM if w.new[0].platform != "x" else NEED_SYSTEM.replace(
        "You are a perceptive sales scout reading part of a Persian Telegram group chat.",
        "You are a strict sales-intent reviewer reading independent public Persian X posts. Posts are by different authors and are not a conversation. Never use one author's post as context for another author. For X, ignore weak or inferred needs: only explicitly confirmed current purchase intent qualifies.")
    data, usage = llm.complete_json("need_extraction", cfg.extract_model, system, render(w, cfg), max_tokens=6000,
                                    ref=f"{w.chat_id}:{w.new[0].message_id}-{w.new[-1].message_id}")
    authors = {m.author_id: m for m in w.new + w.context + w.parents}
    x_messages = {m.message_id: m for m in w.new} if w.new[0].platform == "x" else {}
    cards, dropped = [], 0
    for n in (data or {}).get("needs", []) or []:
        if not isinstance(n, dict):
            continue
        ev = sorted({int(e) for e in (n.get("evidence_message_ids") or []) if str(e).lstrip("-").isdigit()} & w.all_ids)
        aid = str(n.get("author_id") or "")
        if x_messages and not _confirmed_x_buyer(n, ev, aid, x_messages):
            dropped += 1
            continue
        if not ev or aid not in authors or not (set(ev) & w.new_ids):
            dropped += 1
            continue
        label = n.get("label") if n.get("label") in _LABELS else "implicit_need"
        c = n.get("constraints") if isinstance(n.get("constraints"), dict) else {}
        reqs = [Requirement(text=str(r["text"]).strip(), must=bool(r.get("must"))) for r in (n.get("requirements") or [])
                if isinstance(r, dict) and str(r.get("text") or "").strip()][:5]
        a = authors[aid]
        is_opp = bool(n.get("is_opportunity")) and label in OPPORTUNITY_LABELS
        cards.append(NeedCard(
            need_id="", chat_id=w.chat_id, author_id=aid, author_name=a.author_name, author_username=a.author_username,
            label=label, is_opportunity=is_opp, buyer_intent_confirmed=n.get("buyer_intent_confirmed") is True,
            situation=n.get("situation"), need=n.get("need"),
            solution_queries=[q.strip() for q in (n.get("solution_queries") or []) if isinstance(q, str) and q.strip()][:6],
            problem_queries=[q.strip() for q in (n.get("problem_queries") or []) if isinstance(q, str) and q.strip()][:3],
            requirements=reqs,
            constraints=Constraints(budget_toman=_int_or_none(c.get("budget_toman")), city=c.get("city") or None,
                                    use=c.get("use") or None, level=c.get("level") or None, other=c.get("other") or None),
            strength=n.get("strength") if n.get("strength") in STRENGTH_RANK else "weak", emotion=n.get("emotion"),
            evidence_ids=ev, status="open" if is_opp else "not_opportunity", created_at=now, updated_at=now))
    if dropped:
        log.info("window %s: dropped %d needs without valid evidence in new messages", w.chat_id, dropped)
    share = usage["toman"] / max(1, len(cards))
    for c in cards:
        c.cost_toman, c.llm_calls = share, 1
    return cards, usage["toman"]


def _merge(old: NeedCard, new: NeedCard) -> NeedCard:
    m = old.model_copy(deep=True)
    m.evidence_ids = sorted(set(old.evidence_ids) | set(new.evidence_ids))
    m.solution_queries = list(dict.fromkeys(old.solution_queries + new.solution_queries))[:8]
    m.problem_queries = list(dict.fromkeys(old.problem_queries + new.problem_queries))[:4]
    have = {norm(r.text) for r in old.requirements}
    m.requirements = (old.requirements + [r for r in new.requirements if norm(r.text) not in have])[:6]
    oc, nc = old.constraints.model_dump(), new.constraints.model_dump()
    m.constraints = Constraints(**{k: (nc[k] if nc[k] not in (None, "") else oc[k]) for k in oc})  # latest statement wins
    if STRENGTH_RANK[new.strength] > STRENGTH_RANK[old.strength]:
        m.strength = new.strength
    if new.situation and len(new.situation) >= len(old.situation or "") * 0.6:
        m.situation, m.need = new.situation, new.need or old.need
    m.author_name = new.author_name or old.author_name
    m.author_username = new.author_username or old.author_username
    # the latest state matters: resolved closes it; a later joke/curiosity does not cancel a real need
    if new.is_opportunity:
        m.label, m.is_opportunity, m.status = new.label, True, "open"
    elif new.label == "resolved":
        m.label, m.is_opportunity, m.status = "resolved", False, "resolved"
    m.cost_toman = old.cost_toman + new.cost_toman
    m.llm_calls = old.llm_calls + new.llm_calls
    m.emotion = new.emotion or old.emotion
    m.updated_at = new.updated_at
    return m


def query_texts(n: NeedCard) -> list[str]:
    return n.solution_queries + n.problem_queries + ([n.situation] if n.situation else [])


def _match_key(n: NeedCard) -> str:
    return json.dumps([n.status, n.solution_queries, n.problem_queries, [r.model_dump() for r in n.requirements],
                       n.constraints.model_dump(), n.need], ensure_ascii=False, sort_keys=True)


def remember(cards: list[NeedCard], store: Store, emb: Embedder, cfg: EngineConfig) -> list[tuple[NeedCard, bool, bool]]:
    """Merge each card with the same person's earlier needs (evidence overlap or semantic similarity) and persist.
    Returns [(card, is_new, needs_rematch)]; re-matching is skipped when nothing relevant to matching changed."""
    if not cards:
        return []
    svecs = emb.encode([c.summary_text() for c in cards], stage="embed_needs")
    out: dict[str, tuple[NeedCard, bool, bool]] = {}
    for c, v in zip(cards, svecs):
        target, tvec = None, None
        for old, ov in store.person_needs(c.chat_id, c.author_id):
            overlap = set(old.evidence_ids) & set(c.evidence_ids)
            if overlap or (ov is not None and ov.shape == v.shape and float(ov @ v) >= cfg.person_merge_sim):
                target, tvec = old, ov
                break
        if target is None and c.label == "resolved":
            # "got it, thanks" rarely repeats the product words → close the person's closest open need
            opens = [(old, ov) for old, ov in store.person_needs(c.chat_id, c.author_id) if old.status == "open"]
            if opens:
                target, tvec = max(opens, key=lambda x: float(x[1] @ v) if x[1] is not None and x[1].shape == v.shape else -1.0)
        if target is None:
            c.need_id = store.next_need_id()
            merged, is_new, mv, rematch = c, True, v, True
        else:
            merged = _merge(target, c)
            is_new = False
            mv = (tvec + v) / (np.linalg.norm(tvec + v) + 1e-9)
            rematch = _match_key(merged) != _match_key(target)
        old_qv = None if (is_new or rematch) else store.query_vecs(merged.need_id)
        qv = old_qv if old_qv is not None else (emb.encode(query_texts(merged), stage="embed_queries") if merged.is_opportunity else None)
        store.save_need(merged, mv, qv)
        prev = out.get(merged.need_id)
        out[merged.need_id] = (merged, is_new or bool(prev and prev[1]), rematch or bool(prev and prev[2]))
    return list(out.values())

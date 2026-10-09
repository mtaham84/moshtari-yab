"""LLM checklist verification (no scores from the LLM), reverse matching for new products, and reply drafts."""
from __future__ import annotations

import json
import re
import hashlib
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from need_engine.catalog import Catalog
from need_engine.config import EngineConfig
from need_engine.llm import LLMClient
from need_engine.prompts import NEW_PRODUCT_SYSTEM, REPLY_SYSTEM, REPLY_SYSTEM_X, VERIFY_SYSTEM
from need_engine.retrieve import Retrieval
from need_engine.schemas import ChatMessage, MatchedProduct, NeedCard
from need_engine.scoring import score_match
from need_engine.store import Store


def evidence_messages(n: NeedCard, store: Store) -> list[ChatMessage]:
    out = [store.message(n.chat_id, i) for i in n.evidence_ids]
    return [m for m in out if m is not None]


def evidence_text(n: NeedCard, store: Store) -> str:
    return "\n".join(f"#{m.message_id} {m.author_name or m.author_id}: {m.text}" for m in evidence_messages(n, store))


def req_block(n: NeedCard) -> str:
    return "\n".join(f"  {k + 1}. {r.text}" + (" (ضروری)" if r.must else "") for k, r in enumerate(n.requirements)) or "  (none)"


def verify_need(n: NeedCard, ret: Retrieval, cat: Catalog, store: Store, llm: LLMClient, cfg: EngineConfig) -> tuple[list[MatchedProduct], float, int]:
    """All selected candidates are checked, in batches of ``verify_batch``. Returns (matches ≥ min_score, toman, calls)."""
    if not ret.candidates:
        return [], 0.0, 0
    head = json.dumps({"person_messages": evidence_text(n, store), "situation": n.situation, "need": n.need}, ensure_ascii=False)
    batches = [ret.candidates[i:i + cfg.verify_batch] for i in range(0, len(ret.candidates), cfg.verify_batch)]

    def run(cands: list[int]):
        user = head + "\n\nRequirements:\n" + req_block(n) + "\n\nCandidates:\n" + "\n".join(cat.line(j) for j in cands)
        data, usage = llm.complete_json("verify", cfg.verify_model, VERIFY_SYSTEM, user, max_tokens=2048, ref=n.need_id)
        return cands, data, usage["toman"]

    with ThreadPoolExecutor(max_workers=cfg.max_workers) as ex:
        results = list(ex.map(run, batches))
    out, toman = {}, 0.0
    for cands, data, t in results:
        toman += t
        valid = {cat.products[j].product_id: j for j in cands}
        for m in (data or {}).get("matches", []) or []:
            if not isinstance(m, dict) or str(m.get("product_id")) not in valid or m.get("solves") not in cfg.solves_factor:
                continue
            j = valid[str(m["product_id"])]
            req = m.get("req") if isinstance(m.get("req"), list) else []
            score, verdict = score_match(n, cat.products[j], m["solves"], req, cfg, m.get("reason"))
            if score * 100 >= cfg.min_score:
                out[cat.products[j].product_id] = MatchedProduct(product_id=cat.products[j].product_id, match_score=score,
                                                                 similarity=round(float(ret.dense[j]), 3), verdict=verdict)
    ms = sorted(out.values(), key=lambda x: -x.match_score)
    return ms, toman, len(batches)


def verify_new_product(pid: str, needs: list[tuple[NeedCard, np.ndarray]], cat: Catalog, llm: LLMClient,
                       cfg: EngineConfig) -> tuple[dict[str, MatchedProduct], float]:
    """A newly added product against stored open needs (no message is re-read)."""
    if not needs or pid not in cat.pid_index:
        return {}, 0.0
    j = cat.pid_index[pid]
    pv = cat.product_vectors(pid)
    scored = []
    for n, qv in needs:
        if qv is None or len(qv) == 0 or qv.shape[1] != pv.shape[1]:
            continue
        scored.append((float((pv @ qv.T).max()), n))
    floor = cfg.effective_sim_floor()
    scored = [x for x in sorted(scored, key=lambda x: -x[0]) if x[0] >= floor][:cfg.new_product_top_needs]
    if not scored:
        return {}, 0.0
    by_id = {n.need_id: (s, n) for s, n in scored}
    lines = "\n".join(f"[{n.need_id}] {n.situation} | نیاز: {n.need}\n  Requirements:\n{req_block(n)}" for _, n in scored)
    data, usage = llm.complete_json("new_product_verify", cfg.verify_model, NEW_PRODUCT_SYSTEM,
                                    "Product:\n" + cat.line(j) + "\n\nPeople:\n" + lines, max_tokens=2048, ref=pid)
    out = {}
    for m in (data or {}).get("matches", []) or []:
        if not isinstance(m, dict) or m.get("need_id") not in by_id or m.get("solves") not in cfg.solves_factor:
            continue
        sim, n = by_id[m["need_id"]]
        score, verdict = score_match(n, cat.products[j], m["solves"], m.get("req") if isinstance(m.get("req"), list) else [], cfg, m.get("reason"))
        if score * 100 >= cfg.min_score:
            out[n.need_id] = MatchedProduct(product_id=pid, match_score=score, similarity=round(sim, 3), verdict=verdict)
    return out, usage["toman"]


_STRIP = re.compile(r"https?://\S+|@\w+|(?:\+?98|0)9\d{9}")


def draft_reply(n: NeedCard, mp: MatchedProduct, cat: Catalog, store: Store, llm: LLMClient, cfg: EngineConfig) -> tuple[str, float]:
    j = cat.pid_index[mp.product_id]
    user = json.dumps({"person_messages": evidence_text(n, store), "situation": n.situation, "product": cat.line(j),
                       "mismatches_to_mention_honestly": mp.verdict.conflicts}, ensure_ascii=False)
    if n.chat_id.startswith("x:"):
        angles = ("یک پرسش روشن‌کننده", "یک نکتهٔ کاربردی و سپس اشارهٔ کوتاه به گزینه", "پیشنهاد کوتاه و بدون فشار")
        angle = angles[int(hashlib.sha256(n.need_id.encode()).hexdigest(), 16) % len(angles)]
        user = json.dumps({"context": json.loads(user), "style_angle": angle}, ensure_ascii=False)
        data, usage = llm.complete_json("reply_x", cfg.reply_model, REPLY_SYSTEM_X, user, max_tokens=800, temperature=0.4,
                                        ref=f"{n.need_id}:{mp.product_id}")
        from need_engine.x_replies import fit_public_reply
        variants = data or {}
        public = fit_public_reply(str(variants.get("public") or ""), cfg.x_public_target_chars)
        short = fit_public_reply(str(variants.get("short") or public), cfg.x_reply_max_chars)
        dm = str(variants.get("dm") or "").strip().replace("{{LINK}}", cfg.product_url_template.format(product_id=mp.product_id, opportunity_id=n.need_id))
        mp.reply_variants = {"public": public, "dm": dm, "short": short}
        return public, usage["toman"]
    data, usage = llm.complete_json("reply", cfg.reply_model, REPLY_SYSTEM, user, max_tokens=800, temperature=0.4,
                                    ref=f"{n.need_id}:{mp.product_id}")
    rep = _STRIP.sub("", (data or {}).get("reply") or "").strip()
    link = cfg.product_url_template.format(product_id=mp.product_id, opportunity_id=n.need_id)
    return (rep.replace("{{LINK}}", link) if "{{LINK}}" in rep else (rep + " " + link).strip()), usage["toman"]

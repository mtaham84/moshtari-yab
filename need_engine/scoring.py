"""Deterministic match score from the LLM's yes/no checklist plus code checks of budget and city."""
from __future__ import annotations

from need_engine.config import EngineConfig
from need_engine.retrieve import budget_ratio, city_ok
from need_engine.schemas import NeedCard, Product, Verdict

_SYM = {"met": "✓", "unmet": "✗", "unknown": "?"}


def score_match(n: NeedCard, p: Product, solves: str, req_status: list[str], cfg: EngineConfig,
                reason: str | None = None) -> tuple[float, Verdict]:
    """→ (score 0..1, verdict). The LLM never produces numbers; all weights live in EngineConfig."""
    rq = n.requirements
    st = [(req_status[k] if k < len(req_status) and req_status[k] in cfg.req_value else "unknown") for k in range(len(rq))]
    items = [(r.text, s) for r, s in zip(rq, st)]
    f_budget = f_city = 1.0
    b = n.constraints.budget_toman
    if b:
        ratio = budget_ratio(n, p.price_toman)
        if ratio is None:
            items.append((f"بودجه {b:,}", "unknown"))
        else:
            f_budget = cfg.budget_factors[-1][1]
            for lim, f in cfg.budget_factors:
                if ratio <= lim:
                    f_budget = f
                    break
            items.append((f"بودجه {b:,}" + ("" if f_budget == 1 else f" (قیمت {ratio:.1f}×)"), "met" if f_budget == 1 else "unmet"))
    if n.constraints.city:
        ok = city_ok(n, p.city, p.ships_nationwide)
        f_city = 1.0 if ok else cfg.city_factor
        items.append((f"شهر {n.constraints.city}" + ("" if ok else f" (فروشنده: {p.city}، بدون ارسال)"), "met" if ok else "unmet"))
    if rq:
        w = [cfg.req_weight["must" if r.must else "nice"] for r in rq]
        req_score = sum(wi * cfg.req_value[s] for wi, s in zip(w, st)) / sum(w)
    else:
        req_score = 1.0
    must_unmet = any(r.must and s == "unmet" for r, s in zip(rq, st))
    score = (cfg.solves_factor.get(solves, 0.0) * (0.5 + 0.5 * req_score)
             * (cfg.must_unmet_factor if must_unmet else 1.0) * f_budget * f_city)
    v = Verdict(solves=solves, met=sum(s == "met" for _, s in items), unmet=sum(s == "unmet" for _, s in items),
                unknown=sum(s == "unknown" for _, s in items), total=len(items),
                conflicts=[t for t, s in items if s == "unmet"], checks=[f"{_SYM[s]} {t}" for t, s in items], reason=reason)
    return round(score, 3), v

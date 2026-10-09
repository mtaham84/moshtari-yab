"""Candidate selection without an LLM: dense (embedding) + BM25, adaptive threshold instead of a fixed K."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from need_engine.catalog import Catalog
from need_engine.config import EngineConfig
from need_engine.schemas import NeedCard
from need_engine.text import norm


def rrf(*rank_lists: list[int], k: int = 60, weights: list[float] | None = None) -> list[int]:
    sc: dict[int, float] = defaultdict(float)
    for w, rl in zip(weights or [1.0] * len(rank_lists), rank_lists):
        for r, i in enumerate(rl):
            sc[i] += w / (k + r + 1)
    return [i for i, _ in sorted(sc.items(), key=lambda x: -x[1])]


def adaptive_threshold(dense: np.ndarray, cfg: EngineConfig, gap: float | None = None, floor: float | None = None) -> tuple[float, float]:
    """threshold = mean similarity of the top-N products − gap, never below the floor."""
    gap = cfg.sim_gap if gap is None else gap
    floor = cfg.effective_sim_floor() if floor is None else floor
    top = np.sort(dense[np.isfinite(dense)])[::-1][:cfg.sim_top_n]   # -inf = product the chat may not see
    m = float(top.mean()) if len(top) else 0.0
    return max(floor, m - gap), m


def select_candidates(dense: np.ndarray, bm25_rank: list[int], cfg: EngineConfig,
                      gap: float | None = None, floor: float | None = None) -> list[int]:
    floor = cfg.effective_sim_floor() if floor is None else floor
    thr, _ = adaptive_threshold(dense, cfg, gap, floor)
    order = np.argsort(-dense)
    sel = [int(j) for j in order if dense[j] >= thr]
    if len(sel) < cfg.cand_min:                           # minimum count, but only above the floor
        sel += [int(j) for j in order[len(sel):] if dense[j] >= floor][:cfg.cand_min - len(sel)]
    have = set(sel)
    sel += [j for j in bm25_rank[:cfg.bm25_extra] if dense[j] >= floor and j not in have]  # exact-word hits
    return sel


def budget_ratio(n: NeedCard, price: float | None) -> float | None:
    b = n.constraints.budget_toman
    return price / b if (b and price) else None


def city_ok(n: NeedCard, city: str | None, ships: bool) -> bool:
    want = n.constraints.city
    if not want or ships or not city:
        return True
    return norm(want) in norm(city) or norm(city) in norm(want)


@dataclass
class Retrieval:
    candidates: list[int] = field(default_factory=list)   # catalog indices, best first
    dense: np.ndarray | None = None
    threshold: float = 0.0
    top_mean: float = 0.0
    dropped_budget: int = 0


def retrieve(n: NeedCard, qvecs: np.ndarray, cat: Catalog, cfg: EngineConfig,
             allowed: frozenset[str] | None = None) -> Retrieval:
    """``allowed`` = business ids whose products this need's chat may be matched against (None = all sellers)."""
    if cat.n == 0 or qvecs is None or len(qvecs) == 0:
        return Retrieval()
    dense = cat.dense_scores(qvecs)
    if allowed is not None:
        visible = np.array([p.business_id in allowed for p in cat.products], dtype=bool)
        if not visible.any():
            return Retrieval()
        dense = np.where(visible, dense, -np.inf).astype(np.float32)
    L = max(cfg.cand_max * 2, 100)
    d_rank = [int(j) for j in np.argsort(-dense)[:L]]
    bm = cat.bm25.scores(" ".join(n.solution_queries)) if cat.bm25 else np.zeros(cat.n)
    b_rank = [int(j) for j in np.argsort(-bm)[:L] if bm[j] > 0 and np.isfinite(dense[j])]
    fused = rrf(d_rank, b_rank, k=cfg.rrf_k, weights=[1.0, cfg.bm25_weight])
    pos = {j: r for r, j in enumerate(fused)}
    sel = sorted(select_candidates(dense, b_rank, cfg), key=lambda j: pos.get(j, 10**9))
    kept, dropped = [], 0
    for j in sel:
        r = budget_ratio(n, cat.products[j].price_toman)
        if r is not None and r > cfg.budget_hard_factor:   # absurdly above budget → drop; otherwise only lower the score
            dropped += 1
            continue
        kept.append(j)
        if len(kept) >= cfg.cand_max:
            break
    thr, m = adaptive_threshold(dense, cfg)
    return Retrieval(kept, dense, thr, m, dropped)

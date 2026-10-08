"""The analysis funnel: prefilter (free) -> triage (small model) -> deep (large model).

Each claimed message ends with at least one AgentVerdict:
  * reached_stage="prefilter"  dropped by rules (cost 0)
  * reached_stage="triage"     screened, no product matched
  * reached_stage="deep"       one verdict per matched product, with analysis + reply draft
Messages hit by transport errors or the call budget go back to the queue.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from analysis.config import AnalysisSettings
from analysis.deep import DeepAnalyzer
from analysis.guards import apply_guards
from analysis.llm import BaseLLM, LLMBudgetExceeded, LLMError
from analysis.prefilter import prefilter
from analysis.schemas import AgentVerdict, ProductProfile, SocialMessage, StageCost, TriageResult
from analysis.store import AnalysisStore
from analysis.text import letter_count
from analysis.triage import Triage, TriageOutcome

log = logging.getLogger("analysis.pipeline")


def ref_for(uid: str, product_id: int) -> str:
    """Short opaque tracking reference used in the product link."""
    return hashlib.sha1(f"{uid}|{product_id}".encode()).hexdigest()[:10]


def _split_cost(cost: StageCost | None, parts: int) -> list[StageCost]:
    """Divide a message's triage cost between its verdicts (when it matched several products)."""
    if cost is None or parts <= 0:
        return []
    out = []
    for i in range(parts):
        pt = cost.prompt_tokens // parts + (1 if i < cost.prompt_tokens % parts else 0)
        ct = cost.completion_tokens // parts + (1 if i < cost.completion_tokens % parts else 0)
        out.append(cost.model_copy(update={
            "prompt_tokens": pt, "completion_tokens": ct,
            "cost_usd": cost.cost_usd / parts, "cost_toman": cost.cost_toman / parts,
            "shared_call": cost.shared_call or parts > 1,
        }))
    return out


@dataclass
class RunStats:
    claimed: int = 0
    dropped_prefilter: int = 0
    no_match_triage: int = 0
    deep_pairs: int = 0
    opportunities: int = 0
    completed: int = 0
    released: int = 0
    failed: int = 0
    status: str = "ok"
    drop_reasons: dict[str, int] = field(default_factory=dict)
    llm: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return dict(self.__dict__)


class AnalysisPipeline:
    def __init__(self, store: AnalysisStore, llm: BaseLLM, catalog: list[ProductProfile], settings: AnalysisSettings, few_shot: int = 4) -> None:
        self.store = store
        self.llm = llm
        self.catalog = catalog
        self.products = {p.product_id: p for p in catalog}
        self.settings = settings
        self.triage = Triage(llm, settings)
        self.deep = DeepAnalyzer(llm, settings, few_shot=few_shot)

    # ------------------------------------------------------------------
    def run_once(self, limit: int = 100, source: str | None = None) -> RunStats:
        stats = RunStats()
        self.llm.reset_run()
        run_id = self.store.start_run()
        messages = self.store.claim_pending(limit, source)
        stats.claimed = len(messages)
        try:
            if messages:
                self._process(messages, stats, run_id)
        finally:
            stats.llm = self.llm.ledger.summary()
            self.store.finish_run(run_id, stats.status, stats.as_dict())
        return stats

    # ------------------------------------------------------------------
    def _finish(self, verdicts: list[AgentVerdict], uids: list[str], run_id: int, stats: RunStats) -> None:
        self.store.save_verdicts(verdicts, run_id)
        self.store.mark_done(uids)
        stats.completed += len(uids)
        stats.opportunities += sum(1 for v in verdicts if v.final_decision == "respond")

    def _release(self, msgs: list[SocialMessage], stats: RunStats, error: str | None, count_attempt: bool = True) -> None:
        uids = [m.uid for m in msgs]
        if not uids:
            return
        if count_attempt:
            self.store.release(uids, error, self.settings.max_attempts)
        else:
            self.store.release_without_attempt(uids)
        stats.released += len(uids)

    def _process(self, messages: list[SocialMessage], stats: RunStats, run_id: int) -> None:
        # ---- Stage 1: rules ---------------------------------------------------
        passed: list[SocialMessage] = []
        dropped: list[AgentVerdict] = []
        for m in messages:
            res = prefilter(m)
            if res.passed:
                passed.append(m)
            else:
                stats.dropped_prefilter += 1
                stats.drop_reasons[res.reason] = stats.drop_reasons.get(res.reason, 0) + 1
                dropped.append(AgentVerdict(message_uid=m.uid, source=m.source, reached_stage="prefilter",
                                            skip_reason=res.reason, costs=[StageCost(stage="prefilter")]))
        if dropped:
            self._finish(dropped, [v.message_uid for v in dropped], run_id, stats)

        # ---- Stage 2: triage (cached results are reused) ---------------------
        cached = self.store.get_triage([m.uid for m in passed])
        to_screen = [m for m in passed if m.uid not in cached]
        matches: dict[str, list[TriageResult]] = {u: c[0] for u, c in cached.items()}
        triage_costs: dict[str, StageCost | None] = {u: c[1] for u, c in cached.items()}
        if to_screen:
            outcome = TriageOutcome()
            interrupted: str | None = None
            try:
                self.triage.run(to_screen, self.catalog, outcome)
            except LLMBudgetExceeded:
                stats.status, interrupted = "budget_exhausted", "budget"
            except LLMError as exc:
                stats.status, interrupted = "llm_error", str(exc)
            failed = set(outcome.failed)
            unscreened = []
            for m in to_screen:
                if m.uid in failed:
                    continue
                if m.uid not in outcome.matches:
                    unscreened.append(m)
                    continue
                matches[m.uid] = outcome.matches[m.uid]
                triage_costs[m.uid] = outcome.costs.get(m.uid)
                self.store.set_triage(m.uid, matches[m.uid], triage_costs[m.uid])
            if failed:
                stats.failed += len(failed)
                self._release([m for m in to_screen if m.uid in failed], stats, "invalid_triage_output")
            if unscreened:
                if interrupted == "budget":
                    self._release(unscreened, stats, None, count_attempt=False)
                else:
                    self._release(unscreened, stats, interrupted or "not_screened")

        # ---- Stage 3: deep analysis per matched product -----------------------
        screened = [m for m in passed if m.uid in matches]
        for idx, m in enumerate(screened):
            found = [t for t in matches[m.uid] if t.product_id in self.products]
            if not found:
                stats.no_match_triage += 1
                self._finish([AgentVerdict(message_uid=m.uid, source=m.source, reached_stage="triage", skip_reason="no_product_match",
                                           costs=[StageCost(stage="prefilter"), *_split_cost(triage_costs.get(m.uid), 1)])],
                             [m.uid], run_id, stats)
                continue
            try:
                verdicts = self._deep_verdicts(m, found, triage_costs.get(m.uid))
            except LLMBudgetExceeded:
                stats.status = "budget_exhausted"
                self._release(screened[idx:], stats, None, count_attempt=False)
                return
            except LLMError as exc:
                stats.status = "llm_error"
                self._release(screened[idx:], stats, str(exc))
                return
            stats.deep_pairs += len(verdicts)
            self._finish(verdicts, [m.uid], run_id, stats)

    def _deep_verdicts(self, m: SocialMessage, found: list[TriageResult], triage_cost: StageCost | None) -> list[AgentVerdict]:
        shares = _split_cost(triage_cost, len(found))
        persian = letter_count("".join(ch for ch in m.text if "\u0600" <= ch <= "\u06FF")) >= 3
        verdicts = []
        for t, share in zip(found, shares or [None] * len(found)):
            product = self.products[t.product_id]
            out = self.deep.analyze(m, product)
            costs = [StageCost(stage="prefilter")] + ([share] if share else []) + [out.cost]
            v = AgentVerdict(message_uid=m.uid, source=m.source, product_id=product.product_id, reached_stage="deep", costs=costs)
            if out.analysis is None:
                v.skip_reason = out.error or "invalid_deep_output"
            else:
                a = out.analysis
                v.analysis, v.model_decision = a, a.decision
                if a.decision == "respond":
                    if a.fit_score < self.settings.fit_threshold:
                        v.skip_reason = f"below_threshold({a.fit_score}<{self.settings.fit_threshold})"
                    else:
                        g = apply_guards(a.reply_draft, product, ref_for(m.uid, product.product_id),
                                         max_chars=self.settings.reply_max_chars, expect_persian=persian)
                        v.guard_issues = g.issues
                        if g.ok:
                            v.final_decision, v.reply_draft = "respond", g.text
                        else:
                            v.skip_reason = "guard_failed"
                else:
                    v.skip_reason = "model_discard"
            verdicts.append(v)
        return verdicts

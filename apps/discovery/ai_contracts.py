from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


class AIValidationError(Exception):
    """Raised when structured AI output fails validation."""
    pass


@dataclass
class CandidateProductItem:
    id: int
    name: str
    price: float | int = 0
    description: str = ""
    category_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateProductItem:
        return cls(
            id=int(data.get("id", 0)),
            name=str(data.get("name", "")).strip(),
            price=float(data.get("price", 0) or 0),
            description=str(data.get("description", "")).strip(),
            category_path=str(data.get("category_path", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CandidateInputSchema:
    candidate_id: str
    source_platform: str  # 'telegram', 'x', 'instagram', 'divar', 'other'
    source_raw_message: str
    source_message_id: str = ""
    source_message_timestamp: Optional[str] = None
    sender_username: Optional[str] = None
    sender_name: Optional[str] = None
    sender_phone: Optional[str] = None
    sender_profile_url: Optional[str] = None
    external_user_id: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    candidate_products: list[CandidateProductItem] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateInputSchema:
        platform = str(data.get("source_platform", "other")).lower().strip()
        if platform not in {"telegram", "x", "instagram", "divar", "other"}:
            platform = "other"

        # Support both canonical nested structure and flat schema
        cust_dict = data.get("customer") if isinstance(data.get("customer"), dict) else {}
        msg_dict = data.get("customer_message") if isinstance(data.get("customer_message"), dict) else {}
        context_dict = data.get("context") if isinstance(data.get("context"), dict) else {}
        src_meta = data.get("source_metadata") if isinstance(data.get("source_metadata"), dict) else {}

        raw_message = str(
            msg_dict.get("text")
            or data.get("source_raw_message")
            or data.get("text")
            or data.get("message")
            or ""
        ).strip()

        msg_id = str(
            data.get("external_message_id")
            or data.get("source_message_id")
            or ""
        ).strip()

        ext_uid = str(
            data.get("external_user_id")
            or cust_dict.get("external_user_id")
            or ""
        ).strip() or None

        s_name = cust_dict.get("name") or data.get("sender_name") or data.get("name") or data.get("lead_display_name")
        s_username = cust_dict.get("username") or data.get("sender_username") or data.get("source_username") or data.get("lead_handle")
        s_phone = cust_dict.get("phone_number") or data.get("sender_phone") or data.get("phone_number")
        s_url = cust_dict.get("profile_url") or data.get("sender_profile_url") or data.get("source_profile_url")
        s_timestamp = msg_dict.get("timestamp") or data.get("source_message_timestamp")

        combined_metadata = {
            **src_meta,
            **(data.get("metadata") or {}),
            "context": context_dict,
        }

        products_data = data.get("candidate_products", [])
        products = [
            CandidateProductItem.from_dict(p) if isinstance(p, dict) else p
            for p in products_data
        ]

        return cls(
            candidate_id=str(data.get("candidate_id") or msg_id or "cand_unknown"),
            source_platform=platform,
            source_raw_message=raw_message,
            source_message_id=msg_id,
            source_message_timestamp=s_timestamp,
            sender_username=s_username,
            sender_name=s_name,
            sender_phone=s_phone,
            sender_profile_url=s_url,
            external_user_id=ext_uid,
            category_id=data.get("category_id"),
            category_name=data.get("category_name"),
            candidate_products=products,
            metadata=combined_metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["candidate_products"] = [p.to_dict() for p in self.candidate_products]
        return res


@dataclass
class ExtractedNeedSchema:
    is_potential_buyer: bool
    need: str
    product_type: str = ""
    attributes: dict[str, Any] = field(default_factory=dict)
    urgency: str = "unknown"  # 'low', 'medium', 'high', 'unknown'
    intent_score: float = 0.0
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractedNeedSchema:
        score = float(data.get("intent_score", 0.0) or 0.0)
        conf = float(data.get("confidence", 0.0) or 0.0)
        urgency = str(data.get("urgency", "unknown")).lower().strip()
        if urgency not in {"low", "medium", "high", "unknown"}:
            urgency = "unknown"

        return cls(
            is_potential_buyer=bool(data.get("is_potential_buyer", True)),
            need=str(data.get("need", "")).strip(),
            product_type=str(data.get("product_type", "")).strip(),
            attributes=data.get("attributes") if isinstance(data.get("attributes"), dict) else {},
            urgency=urgency,
            intent_score=max(0.0, min(1.0, score)),
            confidence=max(0.0, min(1.0, conf)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FinalOpportunityAnalysisSchema:
    is_real_opportunity: bool
    intent_score: float
    need_summary: str
    reason: str
    recommended_product_ids: list[int] = field(default_factory=list)
    product_reasons: dict[int, str] = field(default_factory=dict)
    suggested_reply: str = ""
    confidence: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FinalOpportunityAnalysisSchema:
        score = float(data.get("intent_score", 0.0) or 0.0)
        conf = float(data.get("confidence", 0.0) or 0.0)
        prod_ids = [int(x) for x in data.get("recommended_product_ids", []) if str(x).isdigit()]
        reasons_raw = data.get("product_reasons") or {}
        p_reasons = {int(k): str(v) for k, v in reasons_raw.items() if str(k).isdigit()}

        return cls(
            is_real_opportunity=bool(data.get("is_real_opportunity", True)),
            intent_score=max(0.0, min(1.0, score)),
            need_summary=str(data.get("need_summary", "")).strip(),
            reason=str(data.get("reason", "")).strip(),
            recommended_product_ids=prod_ids,
            product_reasons=p_reasons,
            suggested_reply=str(data.get("suggested_reply", "")).strip(),
            confidence=max(0.0, min(1.0, conf)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProductMatchOutput:
    product_id: int
    match_score: float  # 0.0 to 1.0
    recommendation_reason: str
    rank: int = 1

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProductMatchOutput:
        score = float(data.get("match_score", 0.0) or 0.0)
        return cls(
            product_id=int(data.get("product_id", 0)),
            match_score=min(1.0, max(0.0, score)),
            recommendation_reason=str(data.get("recommendation_reason", "")).strip(),
            rank=int(data.get("rank", 1) or 1),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvidenceOutput:
    evidence_type: str  # 'customer_message', 'need_signal', 'product_match', 'category_match', 'other'
    content: str
    source_reference: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceOutput:
        ev_type = str(data.get("evidence_type", "customer_message")).strip().lower()
        if ev_type not in {"customer_message", "need_signal", "product_match", "category_match", "other"}:
            ev_type = "other"
        return cls(
            evidence_type=ev_type,
            content=str(data.get("content", "")).strip(),
            source_reference=str(data.get("source_reference", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AIAnalysisOutputSchema:
    need: str
    intent_score: float  # 0.0 to 1.0
    product_fit_score: float  # 0.0 to 1.0
    confidence: float  # 0.0 to 1.0
    why_selected: str
    suggested_reply: str
    product_matches: list[ProductMatchOutput] = field(default_factory=list)
    evidence_items: list[EvidenceOutput] = field(default_factory=list)
    model_name: str = "llama-3.3-70b-versatile"
    model_version: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0
    cost_toman: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AIAnalysisOutputSchema:
        matches_data = data.get("product_matches", [])
        matches = [
            ProductMatchOutput.from_dict(m) if isinstance(m, dict) else m
            for m in matches_data
        ]

        evidence_data = data.get("evidence_items", [])
        evidence = [
            EvidenceOutput.from_dict(e) if isinstance(e, dict) else e
            for e in evidence_data
        ]

        return cls(
            need=str(data.get("need", "")).strip(),
            intent_score=float(data.get("intent_score", 0.0) or 0.0),
            product_fit_score=float(data.get("product_fit_score", 0.0) or 0.0),
            confidence=float(data.get("confidence", 0.0) or 0.0),
            why_selected=str(data.get("why_selected", "")).strip(),
            suggested_reply=str(data.get("suggested_reply", "")).strip(),
            product_matches=matches,
            evidence_items=evidence,
            model_name=str(data.get("model_name", "llama-3.3-70b-versatile")),
            model_version=str(data.get("model_version", "")),
            tokens_used=int(data.get("tokens_used", 0) or 0),
            cost_usd=float(data.get("cost_usd", 0.0) or 0.0),
            cost_toman=int(data.get("cost_toman", 0) or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        res = asdict(self)
        res["product_matches"] = [m.to_dict() for m in self.product_matches]
        res["evidence_items"] = [e.to_dict() for e in self.evidence_items]
        return res


class LLMOutputValidator:
    """
    Validates, sanitizes, and enforces anti-hallucination and privacy guardrails
    on LLM structured outputs before database persistence.
    """

    USD_TO_TOMAN_RATE = 70_000

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """Removes reasoning tokens, think tags, and internal prompt traces."""
        if not text:
            return ""
        # Strip <think>...</think> blocks if present
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        # Strip chain of thought labels
        text = re.sub(r"(?i)^(chain of thought|reasoning process|step \d+:)\s*", "", text)
        return text.strip()

    @classmethod
    def validate_score(cls, val: Any, name: str) -> float:
        if not isinstance(val, (int, float)) or not math.isfinite(val):
            raise AIValidationError(f"Invalid score value for {name}: {val}")
        # Clamp strictly to [0.0, 1.0]
        return min(1.0, max(0.0, float(val)))

    @classmethod
    def validate_and_sanitize(
        cls,
        output: AIAnalysisOutputSchema,
        candidate: CandidateInputSchema
    ) -> AIAnalysisOutputSchema:
        """
        Validates the output against the candidate context:
        1. Validates and clamps all scores to [0.0, 1.0].
        2. ANTI-HALLUCINATION: Checks every product_id in product_matches against candidate_products.
           Discards hallucinated products not in candidate_products.
        3. Normalizes product match ranks.
        4. Strips chain-of-thought traces from why_selected.
        5. Computes cost in Toman if cost_usd is set.
        6. Ensures at least one evidence item exists (falls back to raw customer message).
        """
        # 1. Validate scores
        output.intent_score = cls.validate_score(output.intent_score, "intent_score")
        output.product_fit_score = cls.validate_score(output.product_fit_score, "product_fit_score")
        output.confidence = cls.validate_score(output.confidence, "confidence")

        # 2. Sanitize reasoning and strings
        output.why_selected = cls.sanitize_text(output.why_selected)
        output.need = cls.sanitize_text(output.need)
        output.suggested_reply = cls.sanitize_text(output.suggested_reply)

        if not output.need:
            output.need = candidate.source_raw_message[:200]

        if not output.why_selected:
            output.why_selected = "شناسایی نیاز کاربر بر اساس محتوای پیام ارسال‌شده در شبکه اجتماعی."

        # 3. ANTI-HALLUCINATION: Validate product matches against candidate products
        valid_product_ids = {p.id for p in candidate.candidate_products}
        sanitized_matches: list[ProductMatchOutput] = []

        for match in output.product_matches:
            # Enforce score bound
            match.match_score = cls.validate_score(match.match_score, "product_match_score")
            # If product_id exists in the provided candidate catalog, retain it
            if match.product_id in valid_product_ids:
                match.recommendation_reason = cls.sanitize_text(match.recommendation_reason)
                sanitized_matches.append(match)

        # Sort retained matches by match_score descending and re-assign clean ranks 1..N
        sanitized_matches.sort(key=lambda m: -m.match_score)
        for idx, m in enumerate(sanitized_matches, start=1):
            m.rank = idx
        output.product_matches = sanitized_matches

        # 4. If products were in candidate catalog but none matched, or match list empty, check fit score
        if not output.product_matches and candidate.candidate_products:
            # If product_fit_score is high but LLM omitted product_matches, match top candidate product
            if output.product_fit_score >= 0.5:
                top_p = candidate.candidate_products[0]
                output.product_matches.append(
                    ProductMatchOutput(
                        product_id=top_p.id,
                        match_score=output.product_fit_score,
                        recommendation_reason="تطابق موضوعی با دسته‌بندی و کالای منتخب فروشگاه.",
                        rank=1
                    )
                )

        # 5. Validate and sanitize Evidence
        sanitized_evidence: list[EvidenceOutput] = []
        for ev in output.evidence_items:
            ev.content = cls.sanitize_text(ev.content)
            if ev.content:
                sanitized_evidence.append(ev)

        # Ensure we always have customer_message evidence
        has_message_evidence = any(e.evidence_type == "customer_message" for e in sanitized_evidence)
        if not has_message_evidence and candidate.source_raw_message:
            sanitized_evidence.insert(
                0,
                EvidenceOutput(
                    evidence_type="customer_message",
                    content=candidate.source_raw_message,
                    source_reference=f"پیام در {candidate.source_platform}"
                )
            )
        output.evidence_items = sanitized_evidence

        # 6. Costs
        if output.cost_usd > 0 and output.cost_toman == 0:
            output.cost_toman = int(round(output.cost_usd * cls.USD_TO_TOMAN_RATE))

        return output

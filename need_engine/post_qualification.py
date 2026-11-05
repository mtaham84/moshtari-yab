"""Luna Post Qualification Engine for Peyda v2.

Evaluates every eligible post through five independent dimensions:
1. Genuine Need vs. Non-Genuine Content (content_type)
2. Need Signal Classification (need_signal)
3. Purchase Readiness / Intent Stage (purchase_stage - 8 levels)
4. Product Fit (product_fit)
5. Commercial Actionability & Heuristic Scores (buyer_intent_score, product_fit_score, commercial_actionability_score)

Applies three deterministic decision levels:
- QUALIFIED: Genuine need, matching product fit, commercial actionability -> eligible for seller delivery.
- POTENTIAL: Implicit need, problem/pain, competitor dissatisfaction -> flagged as Potential Need.
- REJECT: Jokes, sarcasm, news, ads, promotional content, or unrelated products -> strictly no Opportunity created.

Uncertain classifications are preserved for review rather than blindly rejected.
Failed LLM requests are never treated as rejection or Bayesian failure.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field

logger = logging.getLogger("need_engine.post_qualification")
try:
    from apps.core.prompts import get_system_prompt
except Exception:
    def get_system_prompt(key, default):
        return default

try:
    from apps.products.models import Product
except Exception:
    Product = Any  # type: ignore

ContentType = Literal[
    "genuine_need",
    "joke",
    "sarcasm",
    "general_discussion",
    "news",
    "advertisement",
    "promotional_content",
    "quoted_content",
    "uncertain",
]

NeedSignal = Literal[
    "explicit_product_need",
    "implicit_need",
    "problem_or_pain",
    "dissatisfaction_with_existing_product",
    "dissatisfaction_with_competitor",
    "seeking_alternative",
    "no_identifiable_need",
]

PurchaseStage = Literal[
    "general_awareness",
    "initial_need",
    "solution_research",
    "advice_seeking",
    "product_comparison",
    "price_inquiry",
    "purchase_intent",
    "ready_to_buy",
]

ProductFit = Literal["yes", "partly", "no"]
DecisionLevel = Literal["QUALIFIED", "POTENTIAL", "REJECT"]


class LunaPostQualification(BaseModel):
    post_id: str
    content_type: ContentType
    need_signal: NeedSignal
    purchase_stage: PurchaseStage
    product_fit: ProductFit
    buyer_intent_score: int = Field(default=0, ge=0, le=100)
    product_fit_score: int = Field(default=0, ge=0, le=100)
    commercial_actionability_score: int = Field(default=0, ge=0, le=100)
    is_opportunity: bool = False
    decision: DecisionLevel = "REJECT"
    related_leaf_category: str | None = None
    related_product: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: str = ""
    reason: str = ""


LUNA_QUALIFICATION_SYSTEM = """You are Luna, Peyda's lead qualification engine for Persian social posts (X and Instagram).
Your mission is to evaluate whether a public post represents a genuine customer buying opportunity for a specific seller.

You MUST evaluate the post across FIVE INDEPENDENT DIMENSIONS:

1. content_type:
   - genuine_need: Author expresses an authentic personal requirement, problem, or purchase interest.
   - joke: Humorous exaggeration, memes, jokes (e.g. «با این قیمتها باید کلیه بفروشیم 😂»).
   - sarcasm: Sarcastic or ironical remarks.
   - general_discussion: Chit-chat, casual conversation without purchase intent.
   - news: Informational news or announcement (e.g. «شرکت X محصول جدید معرفی کرد»).
   - advertisement: Author is selling or offering their own products/services.
   - promotional_content: Discounts, referral codes, campaigns, marketing posts.
   - quoted_content: Forwarded text or quote without personal expression.
   - uncertain: Ambiguous or incomplete post text.

2. need_signal:
   - explicit_product_need: Directly asking to buy a specific product or service.
   - implicit_need: Inferred need from situation, life event, or daily context.
   - problem_or_pain: Physical symptom, technical bug, slowness, or pain (e.g. «چشمم از کار با کامپیوتر میسوزه»).
   - dissatisfaction_with_existing_product: Complaining about an old/current product.
   - dissatisfaction_with_competitor: Frustration with a rival brand or clinic.
   - seeking_alternative: Actively asking for replacements or better alternatives.
   - no_identifiable_need: No solvable customer need expressed.

3. purchase_stage:
   - general_awareness: Talking about a category generally, no intent to buy.
   - initial_need: Feeling a problem or need, but no decision to purchase yet.
   - solution_research: Exploring what methods or solutions exist.
   - advice_seeking: Asking others for recommendations (e.g. «چه لپتاپی خوبه؟», «دندانپزشک خوب میشناسید؟»).
   - product_comparison: Comparing 2+ models, brands, or clinics.
   - price_inquiry: Asking about price, costs, or budget (e.g. «هزینه ارتودنسی چقدره؟»).
   - purchase_intent: Actively searching where to buy, looking for stores or providers.
   - ready_to_buy: Ready for immediate transaction or booking.

4. product_fit:
   - yes: Seller's catalog directly solves the expressed need.
   - partly: Solves partially, related category, or valid alternative.
   - no: Completely unrelated product domain.

5. Commercial Actionability & Heuristic Scores (0 to 100):
   - buyer_intent_score (0-100): Strength of intent to acquire a solution.
   - product_fit_score (0-100): Match degree with seller's offerings.
   - commercial_actionability_score (0-100): Feasibility of seller helping this user.
   - confidence (0.0 to 1.0): Model certainty.

DECISION RULES:
- REJECT: joke, sarcasm, news, advertisement, promotional_content, product_fit == "no", or need_signal == "no_identifiable_need". Set is_opportunity = false.
- QUALIFIED: content_type == "genuine_need", product_fit in ("yes", "partly"), high commercial actionability (>= 60), buyer_intent_score >= 50, and purchase_stage in ("advice_seeking", "product_comparison", "price_inquiry", "purchase_intent", "ready_to_buy"). Set is_opportunity = true.
- POTENTIAL: genuine_need or uncertain with product_fit in ("yes", "partly") and need_signal in ("implicit_need", "problem_or_pain", "dissatisfaction_with_existing_product", "dissatisfaction_with_competitor", "seeking_alternative"). Set is_opportunity = true.

CRITICAL INSTRUCTIONS:
- Every classification MUST cite exact evidence text from the post.
- Never invent purchase intent, phone numbers, or biographical facts.
- Do not automatically reject uncertain posts; classify as "uncertain" / "POTENTIAL" if plausible need exists.

Strict JSON Output format:
{
  "post_id": "...",
  "content_type": "genuine_need",
  "need_signal": "problem_or_pain",
  "purchase_stage": "initial_need",
  "product_fit": "yes",
  "buyer_intent_score": 75,
  "product_fit_score": 90,
  "commercial_actionability_score": 70,
  "is_opportunity": true,
  "decision": "POTENTIAL",
  "related_leaf_category": "...",
  "related_product": "...",
  "confidence": 0.88,
  "evidence": "...",
  "reason": "..."
}
"""


def determine_decision(
    content_type: str,
    need_signal: str,
    purchase_stage: str,
    product_fit: str,
    buyer_intent_score: int,
    commercial_actionability_score: int,
) -> tuple[DecisionLevel, bool]:
    """
    Deterministic rule-based decision logic ensuring statistical and business integrity.
    """
    # 1. Reject non-genuine content, advertisements, news, or zero product fit
    if content_type in {"joke", "sarcasm", "news", "advertisement", "promotional_content"}:
        return "REJECT", False
    if need_signal == "no_identifiable_need" or product_fit == "no":
        return "REJECT", False

    # 2. Qualified leads: genuine need, matching product, purchase readiness and actionability
    qualifying_stages = {"advice_seeking", "product_comparison", "price_inquiry", "purchase_intent", "ready_to_buy"}
    if (
        content_type == "genuine_need"
        and product_fit in {"yes", "partly"}
        and (
            (purchase_stage in qualifying_stages and buyer_intent_score >= 45)
            or need_signal in {"explicit_product_need", "dissatisfaction_with_competitor", "seeking_alternative"}
        )
        and commercial_actionability_score >= 45
    ):
        return "QUALIFIED", True

    # 3. Potential leads: implicit need, pain/symptom, or dissatisfaction
    if product_fit in {"yes", "partly"} and need_signal in {
        "implicit_need",
        "problem_or_pain",
        "dissatisfaction_with_existing_product",
        "dissatisfaction_with_competitor",
        "seeking_alternative",
    }:
        return "POTENTIAL", True

    if content_type == "uncertain" and product_fit in {"yes", "partly"}:
        return "POTENTIAL", True

    return "REJECT", False


def qualify_post_deterministic(
    post_id: str,
    text: str,
    leaf_category_path: str = "",
    product_names: list[str] | None = None,
) -> LunaPostQualification:
    """
    Deterministic fallback when LLM is unavailable, ensuring robust zero-crash operation.
    Detects jokes, sarcasm, news, ads, questions, and problem keywords via pattern matching.
    """
    post_text = (text or "").strip()
    norm_text = post_text.casefold()
    product_names = product_names or []

    # Check for joke / sarcasm indicators
    joke_markers = ["😂", "🤣", "کلیه بفروشیم", "جوک", "شوخی", "کلیه م رو", "خخخ"]
    if any(m in norm_text for m in joke_markers):
        return LunaPostQualification(
            post_id=str(post_id),
            content_type="joke",
            need_signal="no_identifiable_need",
            purchase_stage="general_awareness",
            product_fit="no",
            buyer_intent_score=5,
            product_fit_score=0,
            commercial_actionability_score=0,
            is_opportunity=False,
            decision="REJECT",
            confidence=0.92,
            evidence=post_text[:120],
            reason="متن حاوی لحن شوخی یا اغراق طنزآمیز است و بیانگر نیاز تجاری نیست.",
        )

    # Check for news / advertisement indicators
    ad_markers = ["معرفی کرد", "فروش ویژه", "تخفیف", "کد تخفیف", "عرضه شد", "رونمایی شد", "خبر فوری", "گزارش"]
    if any(m in norm_text for m in ad_markers) and not any(q in norm_text for q in ["چی بخرم", "کجا بخرم", "قیمت"]):
        is_news = any(n in norm_text for n in ["معرفی کرد", "رونمایی", "خبر"])
        c_type = "news" if is_news else "advertisement"
        return LunaPostQualification(
            post_id=str(post_id),
            content_type=c_type,
            need_signal="no_identifiable_need",
            purchase_stage="general_awareness",
            product_fit="no",
            buyer_intent_score=10,
            product_fit_score=0,
            commercial_actionability_score=0,
            is_opportunity=False,
            decision="REJECT",
            confidence=0.90,
            evidence=post_text[:120],
            reason="محتوای خبری یا تبلیغاتی بدون بیان قصد خرید شخصی.",
        )

    # Check product fit
    matched_prod = None
    cat_terms = [w for w in re.sub(r"[>،,]", " ", leaf_category_path).split() if len(w) > 2]
    has_fit = False
    for prod in product_names:
        if prod.casefold() in norm_text or any(w in norm_text for w in prod.split() if len(w) > 3):
            has_fit = True
            matched_prod = prod
            break
    if not has_fit and any(t in norm_text for t in cat_terms):
        has_fit = True

    p_fit = "yes" if matched_prod else ("partly" if has_fit else "no")

    # Detect need signal and purchase stage
    pain_markers = ["خارش", "سوزش", "درد", "کند", "تیر میکشه", "عرق سوز", "خسته", "سوخته", "ناراضی", "اذیت", "خراب", "افت فریم"]
    price_markers = ["قیمت", "هزینه", "چنده", "چند تومن", "چند تومان", "ارزون", "گرون", "بودجه"]
    advice_markers = ["پیشنهاد", "چی بخرم", "کجا برم", "می‌شناسید", "میشناسید", "راهنمایی", "تجربه"]
    alt_markers = ["جایگزین", "خسته شدم از", "عوض کنم", "برند دیگه"]

    if any(m in norm_text for m in alt_markers):
        n_signal = "seeking_alternative"
        stage = "solution_research"
        intent_score = 70
    elif any(m in norm_text for m in pain_markers):
        n_signal = "problem_or_pain"
        stage = "initial_need"
        intent_score = 65
    elif any(m in norm_text for m in price_markers):
        n_signal = "explicit_product_need"
        stage = "price_inquiry"
        intent_score = 75
    elif any(m in norm_text for m in advice_markers):
        n_signal = "explicit_product_need"
        stage = "advice_seeking"
        intent_score = 80
    elif has_fit:
        n_signal = "implicit_need"
        stage = "initial_need"
        intent_score = 55
    else:
        n_signal = "no_identifiable_need"
        stage = "general_awareness"
        intent_score = 15

    c_type = "genuine_need" if n_signal != "no_identifiable_need" else "general_discussion"
    actionability = int(intent_score * (0.9 if p_fit == "yes" else (0.6 if p_fit == "partly" else 0.0)))
    fit_score = 90 if p_fit == "yes" else (60 if p_fit == "partly" else 10)

    decision, is_opp = determine_decision(
        content_type=c_type,
        need_signal=n_signal,
        purchase_stage=stage,
        product_fit=p_fit,
        buyer_intent_score=intent_score,
        commercial_actionability_score=actionability,
    )

    return LunaPostQualification(
        post_id=str(post_id),
        content_type=c_type,
        need_signal=n_signal,
        purchase_stage=stage,
        product_fit=p_fit,
        buyer_intent_score=intent_score,
        product_fit_score=fit_score,
        commercial_actionability_score=actionability,
        is_opportunity=is_opp,
        decision=decision,
        related_leaf_category=leaf_category_path or None,
        related_product=matched_prod or (product_names[0] if product_names else None),
        confidence=0.82 if decision != "REJECT" else 0.88,
        evidence=post_text[:120],
        reason="ارزیابی قطعی سیگنال نیاز و تناسب با محصولات فروشنده.",
    )


def qualify_post_with_luna(
    post_id: str,
    text: str,
    leaf_category_path: str = "",
    products: list[Product] | None = None,
    business: Any = None,
    llm_func: Callable | None = None,
) -> LunaPostQualification:
    """
    Main Luna post qualification entrypoint.
    Executes the 5-dimensional evaluation via LLM, falling back gracefully to deterministic logic.
    """
    products = products or []
    prod_names = [p.name for p in products]

    if llm_func is None:
        try:
            from apps.core.llm import chat_json as llm_func
        except Exception:
            llm_func = None

    if not llm_func:
        return qualify_post_deterministic(post_id, text, leaf_category_path, prod_names)

    business_id = getattr(business, "id", None)
    user_payload = {
        "post_id": str(post_id),
        "post_text": text,
        "leaf_category": leaf_category_path,
        "seller_products": [{"name": p.name, "description": (p.description or "")[:120]} for p in products[:10]],
    }

    try:
        raw_res = llm_func(
            get_system_prompt("luna_qualification", LUNA_QUALIFICATION_SYSTEM),
            json.dumps(user_payload, ensure_ascii=False),
            purpose="luna_post_qualification",
            business_id=business_id,
        )
        if not isinstance(raw_res, dict):
            raise ValueError("پاسخ مدل هوش مصنوعی معتبر نبود.")

        content_type = str(raw_res.get("content_type", "uncertain")).strip().lower()
        if content_type not in ContentType.__args__:
            content_type = "uncertain"

        need_signal = str(raw_res.get("need_signal", "no_identifiable_need")).strip().lower()
        if need_signal not in NeedSignal.__args__:
            need_signal = "no_identifiable_need"

        purchase_stage = str(raw_res.get("purchase_stage", "general_awareness")).strip().lower()
        if purchase_stage not in PurchaseStage.__args__:
            purchase_stage = "general_awareness"

        product_fit = str(raw_res.get("product_fit", "no")).strip().lower()
        if product_fit not in ProductFit.__args__:
            product_fit = "no"

        buyer_intent_score = max(0, min(100, int(raw_res.get("buyer_intent_score", 0))))
        product_fit_score = max(0, min(100, int(raw_res.get("product_fit_score", 0))))
        commercial_actionability_score = max(0, min(100, int(raw_res.get("commercial_actionability_score", 0))))

        decision, is_opp = determine_decision(
            content_type=content_type,
            need_signal=need_signal,
            purchase_stage=purchase_stage,
            product_fit=product_fit,
            buyer_intent_score=buyer_intent_score,
            commercial_actionability_score=commercial_actionability_score,
        )

        return LunaPostQualification(
            post_id=str(post_id),
            content_type=content_type,
            need_signal=need_signal,
            purchase_stage=purchase_stage,
            product_fit=product_fit,
            buyer_intent_score=buyer_intent_score,
            product_fit_score=product_fit_score,
            commercial_actionability_score=commercial_actionability_score,
            is_opportunity=is_opp,
            decision=decision,
            related_leaf_category=str(raw_res.get("related_leaf_category") or leaf_category_path or ""),
            related_product=str(raw_res.get("related_product") or (prod_names[0] if prod_names else "")),
            confidence=max(0.0, min(1.0, float(raw_res.get("confidence", 0.85)))),
            evidence=str(raw_res.get("evidence") or text[:120]),
            reason=str(raw_res.get("reason") or "ارزیابی چندبُعدی لونا"),
        )
    except Exception as exc:
        logger.warning("Luna LLM qualification failed for post %s, using deterministic fallback: %s", post_id, exc)
        return qualify_post_deterministic(post_id, text, leaf_category_path, prod_names)

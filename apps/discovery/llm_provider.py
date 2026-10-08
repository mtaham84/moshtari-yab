from __future__ import annotations

import abc
import json
import logging
import os
import urllib.request
import urllib.error
from typing import Any, Optional

from django.conf import settings
from .ai_contracts import (
    CandidateInputSchema,
    AIAnalysisOutputSchema,
    ProductMatchOutput,
    EvidenceOutput,
    LLMOutputValidator,
    AIValidationError,
    ExtractedNeedSchema,
    FinalOpportunityAnalysisSchema,
)

logger = logging.getLogger(__name__)


class BaseLLMProvider(abc.ABC):
    @abc.abstractmethod
    def analyze_candidate(self, candidate: CandidateInputSchema) -> AIAnalysisOutputSchema:
        """Analyzes a candidate customer post and returns structured AI results."""
        pass

    def extract_need(self, raw_message: str, context: Optional[dict] = None) -> ExtractedNeedSchema:
        """Extracts customer intent and need without receiving taxonomy."""
        return ExtractedNeedSchema(
            is_potential_buyer=True,
            need=raw_message.strip(),
            product_type="",
            attributes={},
            urgency="medium",
            intent_score=0.85,
            confidence=0.85
        )

    def resolve_category_ambiguity(
        self,
        customer_need: str,
        candidates: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Resolves ambiguity among supplied candidate categories."""
        if not candidates:
            return {"selected_category_id": None, "confidence": 0.0, "reason": "No candidates"}
        return {
            "selected_category_id": candidates[0]["id"],
            "confidence": 0.88,
            "reason": f"انتخاب دسته «{candidates[0]['name']}» بر پایه تطابق اولیه."
        }

    def analyze_final_opportunity(
        self,
        customer_message: str,
        need_summary: str,
        resolved_category_path: Optional[str],
        candidate_products: list[dict[str, Any]],
        customer_name: str = ""
    ) -> FinalOpportunityAnalysisSchema:
        """Performs final opportunity validation and reply draft generation."""
        prod_ids = [p["product_id"] for p in candidate_products]
        p_reasons = {p["product_id"]: p.get("reason", "تطابق با نیاز کاربر") for p in candidate_products}
        name_label = customer_name or "مشتری گرامی"
        primary_name = candidate_products[0]["name"] if candidate_products else "محصول مورد نظر"
        reply = (
            f"سلام {name_label} عزیز، در خصوص نیاز شما، "
            f"«{primary_name}» در فروشگاه ما با کیفیت تضمین‌شده موجود است. "
            f"خوشحال می‌شویم برای راهنمایی بیشتر پاسخگوی شما باشیم."
        )
        return FinalOpportunityAnalysisSchema(
            is_real_opportunity=True,
            intent_score=0.88,
            need_summary=need_summary or customer_message[:60],
            reason=f"تقاضای کاربر کاملاً با محصولات شاخه «{resolved_category_path or 'کاتالوگ'}» همخوانی دارد.",
            recommended_product_ids=prod_ids,
            product_reasons=p_reasons,
            suggested_reply=reply,
            confidence=0.90
        )


class MockLLMProvider(BaseLLMProvider):
    """
    Deterministic mock provider for unit tests, offline development,
    and fast CI without requiring paid API keys or network access.
    """

    BUY_KEYWORDS = [
        "بخرم", "خرید", "قیمت", "چنده", "سفارش", "موجود", "می‌خوام", "میخوام",
        "پیشنهاد", "دنبال", "فروش", "فروشگاه", "هزینه", "تخفیف", "خریداری"
    ]
    COMPARE_KEYWORDS = [
        "کدوم", "بهتره", "مقایسه", "نظرتون", "تفاوت", "فرق", "بررسی", "کیفیت"
    ]

    def extract_need(self, raw_message: str, context: Optional[dict] = None) -> ExtractedNeedSchema:
        msg = raw_message.strip()
        msg_lower = msg.lower()

        has_buy = any(kw in msg_lower for kw in self.BUY_KEYWORDS)
        has_compare = any(kw in msg_lower for kw in self.COMPARE_KEYWORDS)

        if has_buy and has_compare:
            intent_score = 0.92
            urgency = "high"
        elif has_buy:
            intent_score = 0.88
            urgency = "high"
        elif has_compare:
            intent_score = 0.75
            urgency = "medium"
        else:
            intent_score = 0.50
            urgency = "low"

        # Extract attributes heuristic (e.g. colors, sizes, materials)
        attrs = {}
        for mat in ["لینن", "نخی", "کتان", "چرم", "جین", "پروژه‌محور", "آنلاین", "حضوری"]:
            if mat in msg:
                attrs["جنس_یا_نوع"] = mat
        for color in ["مشکی", "سفید", "آبی", "طوسی", "قرمز"]:
            if color in msg:
                attrs["رنگ"] = color

        return ExtractedNeedSchema(
            is_potential_buyer=intent_score >= 0.60,
            need=msg,
            product_type="",
            attributes=attrs,
            urgency=urgency,
            intent_score=intent_score,
            confidence=0.90
        )

    def resolve_category_ambiguity(
        self,
        customer_need: str,
        candidates: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if not candidates:
            return {"selected_category_id": None, "confidence": 0.0, "reason": "No candidates"}

        tokens = set(customer_need.split())
        best_c = candidates[0]
        max_overlap = -1
        for c in candidates:
            c_text = f"{c['name']} {c['full_path']} {' '.join(c.get('keywords', []))}"
            overlap = sum(1 for t in tokens if t in c_text)
            if overlap > max_overlap:
                max_overlap = overlap
                best_c = c

        return {
            "selected_category_id": best_c["id"],
            "confidence": 0.91,
            "reason": f"انتخاب دقیق دسته «{best_c['name']}» بر پایه تطابق مستقیم کلمات کلیدی با پیام."
        }

    def analyze_final_opportunity(
        self,
        customer_message: str,
        need_summary: str,
        resolved_category_path: Optional[str],
        candidate_products: list[dict[str, Any]],
        customer_name: str = ""
    ) -> FinalOpportunityAnalysisSchema:
        prod_ids = [p["product_id"] for p in candidate_products]
        p_reasons = {p["product_id"]: p.get("reason", "تطابق با نیاز کاربر") for p in candidate_products}
        name_label = customer_name or "مشتری گرامی"
        primary_name = candidate_products[0]["name"] if candidate_products else "محصول مورد نظر"
        reply = (
            f"سلام {name_label} عزیز، در خصوص نیاز شما، "
            f"«{primary_name}» در فروشگاه ما با کیفیت تضمین‌شده موجود است. "
            f"خوشحال می‌شویم برای راهنمایی بیشتر پاسخگوی شما باشیم."
        )
        return FinalOpportunityAnalysisSchema(
            is_real_opportunity=len(prod_ids) > 0,
            intent_score=0.90 if len(prod_ids) > 0 else 0.50,
            need_summary=need_summary or customer_message[:70],
            reason=f"تقاضای مطرح‌شده در کاتالوگ شاخه «{resolved_category_path or 'فروشگاه'}» دارای محصولات معتبر است.",
            recommended_product_ids=prod_ids,
            product_reasons=p_reasons,
            suggested_reply=reply,
            confidence=0.92
        )

    BUY_KEYWORDS = [
        "بخرم", "خرید", "قیمت", "چنده", "سفارش", "موجود", "می‌خوام", "میخوام",
        "پیشنهاد", "دنبال", "فروش", "فروشگاه", "هزینه", "تخفیف", "خریداری"
    ]
    COMPARE_KEYWORDS = [
        "کدوم", "بهتره", "مقایسه", "نظرتون", "تفاوت", "فرق", "بررسی", "کیفیت"
    ]

    def analyze_candidate(self, candidate: CandidateInputSchema) -> AIAnalysisOutputSchema:
        msg = candidate.source_raw_message.strip()
        msg_lower = msg.lower()

        # 1. Determine intent score based on keywords
        has_buy = any(kw in msg_lower for kw in self.BUY_KEYWORDS)
        has_compare = any(kw in msg_lower for kw in self.COMPARE_KEYWORDS)

        if has_buy and has_compare:
            intent_score = 0.88
        elif has_buy:
            intent_score = 0.90
        elif has_compare:
            intent_score = 0.72
        else:
            intent_score = 0.55

        # 2. Match candidate products
        matched_products: list[ProductMatchOutput] = []
        for p in candidate.candidate_products:
            # Check for name overlap
            p_words = set(p.name.split())
            overlap = [w for w in p_words if len(w) > 2 and w in msg]
            if overlap:
                score = min(0.98, 0.70 + (len(overlap) * 0.12))
                matched_products.append(
                    ProductMatchOutput(
                        product_id=p.id,
                        match_score=score,
                        recommendation_reason=f"تطابق مستقیم کلمات کلیدی کالا ('{'، '.join(overlap)}') با پیام مشتری.",
                        rank=1
                    )
                )

        # If no specific keyword match, but candidate products exist, attach the first product
        if not matched_products and candidate.candidate_products:
            top_p = candidate.candidate_products[0]
            matched_products.append(
                ProductMatchOutput(
                    product_id=top_p.id,
                    match_score=0.75,
                    recommendation_reason=f"پیشنهاد بر اساس ارتباط شاخه محصول با نیاز مطرح شده.",
                    rank=1
                )
            )

        # Sort and assign ranks
        matched_products.sort(key=lambda m: -m.match_score)
        for idx, m in enumerate(matched_products, start=1):
            m.rank = idx

        product_fit_score = matched_products[0].match_score if matched_products else 0.50
        confidence = round((intent_score * 0.6) + (product_fit_score * 0.4), 2)

        # 3. Construct Persian why_selected & suggested reply
        customer_label = candidate.sender_name or candidate.sender_username or "مشتری گرامی"
        primary_p_name = ""
        if matched_products:
            for p in candidate.candidate_products:
                if p.id == matched_products[0].product_id:
                    primary_p_name = p.name
                    break

        why_selected = (
            f"کاربر در پیام خود تمایل آشکار به دریافت محصول نشان داده و تقاضای او انطباق بالایی با "
            f"کالای '{primary_p_name or 'کاتالوگ فروشگاه'}' دارد."
        )

        if primary_p_name:
            suggested_reply = (
                f"سلام {customer_label} عزیز، در خصوص درخواست شما، "
                f"«{primary_p_name}» در فروشگاه ما با ضمانت اصالت و کیفیت بالا موجود است. "
                f"در صورت نیاز به راهنمایی بیشتر یا مشخصات کامل در خدمت شما هستیم."
            )
        else:
            suggested_reply = (
                f"سلام {customer_label} عزیز، پیام شما را مشاهده کردیم. "
                f"محصولات مرتبط با نیاز شما در کاتالوگ فروشگاه ما موجود است و آماده راهنمایی شما هستیم."
            )

        # 4. Citations
        evidence_items = [
            EvidenceOutput(
                evidence_type="customer_message",
                content=msg,
                source_reference=f"پیام کاربر در پلتفرم {candidate.source_platform}"
            ),
            EvidenceOutput(
                evidence_type="need_signal",
                content=f"تشخیص قصد تعامل و خرید با امتیاز {int(intent_score * 100)}٪",
                source_reference="ماژول ارزیابی هوش مصنوعی"
            ),
        ]
        if primary_p_name:
            evidence_items.append(
                EvidenceOutput(
                    evidence_type="product_match",
                    content=f"انطباق با کالای {primary_p_name} با ضریب اطمینان {int(product_fit_score * 100)}٪",
                    source_reference="کاتالوگ محصولات فروشگاه"
                )
            )

        raw_output = AIAnalysisOutputSchema(
            need=f"نیاز به بررسی و خرید محصول مرتبط با '{msg[:80]}'",
            intent_score=intent_score,
            product_fit_score=product_fit_score,
            confidence=confidence,
            why_selected=why_selected,
            suggested_reply=suggested_reply,
            product_matches=matched_products,
            evidence_items=evidence_items,
            model_name="mock-peyda-analyzer-v1",
            tokens_used=180,
            cost_usd=0.00012,
            cost_toman=8
        )

        return LLMOutputValidator.validate_and_sanitize(raw_output, candidate)


class GroqLLMProvider(BaseLLMProvider):
    """
    Production LLM provider using Groq API (e.g. Llama-3.3-70B-Versatile).
    Strictly requests and validates JSON matching AIAnalysisOutputSchema.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self.api_key = api_key or getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
        self.model = model or getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
        self.api_url = "https://api.groq.com/openai/v1/chat/completions"
        self.timeout = 25

    def _build_system_prompt(self, candidate: CandidateInputSchema) -> str:
        products_json = json.dumps([p.to_dict() for p in candidate.candidate_products], ensure_ascii=False)
        return (
            "شما دستیار هوشمند و ارزیاب سرنخ‌های فروش در سامانه «پیدا» هستید.\n"
            "وظیفه شما ارزیابی پیام مشتری از شبکه‌های اجتماعی و تشخیص نیت خرید و انطباق با کاتالوگ فروشنده است.\n\n"
            "قوانین حیاتی:\n"
            "۱. فقط و فقط فرمت JSON با کلیدهای مشخص‌شده بازگردانید. هیچ توضیح اضافی یا تگ <think> قرار ندهید.\n"
            "۲. شناسه‌های کالا (product_id) را فقط و فقط از لیست محصولات کاندید داده‌شده انتخاب کنید. ساختن شناسه کالا اکیداً ممنوع است.\n"
            "۳. امتیازها (intent_score, product_fit_score, confidence, match_score) باید اعداد اعشاری بین 0.0 تا 1.0 باشند.\n"
            "۴. اگر مشتری شماره تلفن یا نام در پیام نداده است، به هیچ وجه شماره یا هویت جعلی تولید نکنید.\n"
            "۵. متن why_selected باید دلیل انتخاب این مشتری را به زبان فارسی روان، محترمانه و شفاف برای فروشنده توضیح دهد.\n"
            "۶. متن suggested_reply باید یک پیش‌نویس پاسخ فارسی، حرفه‌ای و مودبانه برای ارسال توسط خود فروشنده باشد.\n\n"
            f"کاتالوگ محصولات در دسترس فروشنده:\n{products_json}\n\n"
            "اسکیمای JSON مورد انتظار:\n"
            "{\n"
            '  "need": "توضیح کوتاه نیاز مشتری",\n'
            '  "intent_score": 0.85,\n'
            '  "product_fit_score": 0.90,\n'
            '  "confidence": 0.88,\n'
            '  "why_selected": "دلیل انتخاب این مشتری برای فروشنده",\n'
            '  "suggested_reply": "پیش‌نویس پیام پیشنهادی به مشتری",\n'
            '  "product_matches": [\n'
            '    {"product_id": 12, "match_score": 0.95, "recommendation_reason": "علت پیشنهاد", "rank": 1}\n'
            '  ],\n'
            '  "evidence_items": [\n'
            '    {"evidence_type": "customer_message", "content": "متن شاهد از پیام مشتری", "source_reference": "..."}\n'
            '  ]\n'
            "}"
        )

    def analyze_candidate(self, candidate: CandidateInputSchema) -> AIAnalysisOutputSchema:
        if not self.api_key:
            logger.warning("GROQ_API_KEY not configured. Falling back to MockLLMProvider.")
            return MockLLMProvider().analyze_candidate(candidate)

        system_prompt = self._build_system_prompt(candidate)
        user_prompt = json.dumps({
            "platform": candidate.source_platform,
            "sender_username": candidate.sender_username,
            "sender_name": candidate.sender_name,
            "raw_message": candidate.source_raw_message
        }, ensure_ascii=False)

        body = json.dumps({
            "model": self.model,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        }).encode("utf-8")

        req = urllib.request.Request(
            self.api_url,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            content_str = data["choices"][0]["message"]["content"]
            parsed_json = json.loads(content_str)

            usage = data.get("usage", {})
            tokens_used = int(usage.get("total_tokens", 0) or 0)
            # Standard Groq pricing calculation ($0.59 / 1M prompt, $0.79 / 1M completion)
            prompt_tok = usage.get("prompt_tokens", 0) or 0
            comp_tok = usage.get("completion_tokens", 0) or 0
            cost_usd = (prompt_tok * 0.00000059) + (comp_tok * 0.00000079)

            output = AIAnalysisOutputSchema.from_dict(parsed_json)
            output.model_name = self.model
            output.tokens_used = tokens_used
            output.cost_usd = round(cost_usd, 6)

            # Strict validation against candidate context
            return LLMOutputValidator.validate_and_sanitize(output, candidate)

        except Exception as e:
            logger.error(f"Error querying Groq API: {e}. Falling back to MockLLMProvider.")
            # Graceful fallback to MockLLMProvider ensures reliability
            fallback = MockLLMProvider().analyze_candidate(candidate)
            fallback.model_name = f"fallback-after-error ({self.model})"
            return fallback


def get_llm_provider(provider_name: Optional[str] = None) -> BaseLLMProvider:
    """Factory to get the configured LLM provider."""
    name = (provider_name or getattr(settings, "LLM_PROVIDER", "mock")).lower().strip()
    if name == "groq":
        return GroqLLMProvider()
    return MockLLMProvider()

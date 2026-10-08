"""Offline heuristic stand-in for the LLM (``--mock-llm``).

⚠️  NOT a real model. It exists so the whole pipeline (queue, funnel, costs,
guards, panel) can be demonstrated and tested without an API key. Its decisions
come from simple word overlap and cue lists; token counts are estimated from
character length. Never report mock results as model quality.
"""

from __future__ import annotations

import json
import re

from analysis.config import AnalysisSettings
from analysis.llm import BaseLLM, LLMResponse
from analysis.text import normalize

_STOP = set("""برای این آن که با از به در را و یا تا هم اما اگر هست است نیست دارد دارند کنند کند می‌خواهند میخواهند
کسانی افراد همراه مناسب نیست هستند حتی اسم نیاورده باشند ساعت‌های طولانی دنبال مدل""".split())
_BUY = ["دنبال", "میخوام", "می خوام", "میخواستم", "خرید", "بخرم", "پیشنهاد", "سراغ", "قیمت", "کجا", "معرفی", "کسی میشناسه", "لازم دارم", "نیاز دارم"]
_PROBLEM = ["مشکل", "درد", "میسوز", "خسته", "اذیت", "سردرد", "خراب", "کلافه", "نمیتونم"]
_SELLER = ["فروش ویژه", "موجود است", "سفارش در دایرکت", "ارسال به سراسر", "تخفیف ویژه", "جهت سفارش"]
_EXPERT = ["پیشرفته", "distributed", "حرفه ای", "سال سابقه", "سال تجربه", "senior", "nccl", "kubernetes"]
_SOLVED = ["مرسی پیدا کردم", "خریدم", "حل شد", "گرفتمش"]


def _stems(text: str) -> set[str]:
    words = re.findall(r"[\w\u0600-\u06FF]+", normalize(text))
    return {w[:4] for w in words if len(w) >= 4 and w not in _STOP}


def _has(text: str, cues: list[str]) -> bool:
    norm = normalize(text)
    return any(c in norm for c in cues)


class HeuristicMockLLM(BaseLLM):
    def __init__(self, settings: AnalysisSettings, max_calls: int | None = None) -> None:
        super().__init__(settings, max_calls)

    def _complete(self, model: str, messages: list[dict[str, str]], max_tokens: int) -> LLMResponse:
        payload = json.loads(messages[-1]["content"])
        if "messages" in payload:
            data = self._triage(payload)
        else:
            data = self._deep(payload)
        content = json.dumps(data, ensure_ascii=False)
        prompt_chars = sum(len(m["content"]) for m in messages)
        return LLMResponse(data=data, raw_content=content, model=model,
                           prompt_tokens=max(1, int(prompt_chars / 3.2)), completion_tokens=max(1, int(len(content) / 3.2)),
                           latency_ms=5)

    def _overlap(self, text: str, product: dict) -> int:
        product_stems = _stems(" ".join(str(product.get(k, "")) for k in ("name", "target_customer", "description")))
        return len(_stems(text) & product_stems)

    def _triage(self, payload: dict) -> dict:
        results = []
        for m in payload["messages"]:
            text = f"{m['text']} {m.get('context', '')}"
            matches = []
            for p in payload["products"]:
                ov = self._overlap(m["text"], p)
                if not _has(text, _SELLER) and (ov >= 2 or (ov >= 1 and _has(text, _BUY + _PROBLEM))):
                    matches.append({"product_id": p["product_id"], "reason": f"mock: {ov} shared terms"})
            results.append({"id": m["id"], "matches": matches})
        return {"results": results}

    def _deep(self, payload: dict) -> dict:
        product, conv = payload["product"], payload["conversation"]
        text = conv["target"]["text"]
        replies = " ".join(r["text"] for r in conv.get("replies", []))
        ov = self._overlap(text, product)
        buy, problem = _has(text, _BUY), _has(text, _PROBLEM)
        beginner_product = "مبتدی" in normalize(product.get("target_customer", ""))
        if _has(text, _SELLER):
            return self._discard("فروشنده یا تبلیغ است (mock).")
        if beginner_product and _has(text, _EXPERT):
            return self._discard("سطح فرد حرفه‌ای است و محصول برای مبتدی‌هاست (mock).", level="expert")
        if _has(replies, _SOLVED):
            return self._discard("نیاز در جواب‌ها برطرف شده است (mock).")
        fit = min(95, 30 + 15 * ov + (20 if buy else 0) + (12 if problem else 0))
        need = "explicit" if buy else ("implicit" if problem else "none")
        if need == "none" or fit < 50:
            return self._discard("نیاز روشنی به این محصول دیده نشد (mock).", fit=fit, need=need)
        stage = "ready_to_buy" if _has(text, ["قیمت", "بخرم", "خرید"]) else ("comparing" if buy else "initial_need")
        author = conv["target"].get("author") or ""
        greet = f"سلام {author} جان! " if author and author != "کاربر" else "سلام! "
        draft = f"{greet}فکر می‌کنم «{product['name']}» به کارت بیاد؛ دقیقاً برای همین نیاز طراحی شده. اگه خواستی جزئیاتش رو ببین: {{{{PRODUCT_LINK}}}}"
        return {"need_type": need, "intent_stage": stage, "user_level": "beginner" if "تازه" in normalize(text) else None,
                "fit_score": fit, "decision": "respond", "reason": f"هم‌پوشانی {ov} واژه و نشانه نیاز (mock).", "reply_draft": draft}

    @staticmethod
    def _discard(reason: str, fit: int = 10, need: str = "none", level: str | None = None) -> dict:
        return {"need_type": need, "intent_stage": "none", "user_level": level, "fit_score": min(fit, 40),
                "decision": "discard", "reason": reason, "reply_draft": None}

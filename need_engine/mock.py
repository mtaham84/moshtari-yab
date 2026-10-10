"""Deterministic offline LLM for tests and demos (``--mock``). Results are meaningless; it only exercises the pipeline."""
from __future__ import annotations

import re

_NEED_WORDS = ("دنبال", "میخوام", "می‌خوام", "سراغ", "لازم", "خراب", "سرده", "نیاز")
_RESOLVED = ("گرفتمش", "خریدم", "حل شد")


def mock_llm(stage: str, system: str, user: str) -> dict:
    if stage == "product_cards":
        ids = re.findall(r'"product_id":\s*"([^"]+)"', user)
        return {"cards": [{"product_id": i, "what_it_is": "محصول", "aliases": [], "problems_solved": []} for i in ids]}
    if stage == "need_extraction":
        new = user.split("NEW messages:", 1)[-1]
        out = []
        for mid, au, txt in re.findall(r"#(\d+) \[[^\]]*\] [^(\n]*\(([^)]+)\)[^:\n]*: (.*)", new):
            if any(w in txt for w in _RESOLVED):
                out.append({"author_id": au, "evidence_message_ids": [int(mid)], "label": "resolved", "is_opportunity": False,
                            "situation": txt[:80], "need": None, "solution_queries": [], "problem_queries": [], "strength": "weak"})
            elif any(w in txt for w in _NEED_WORDS):
                budget = re.search(r"(\d+)\s*(?:میلیون|م\b)", txt)
                out.append({"author_id": au, "evidence_message_ids": [int(mid)], "label": "explicit_need", "is_opportunity": True,
                            "situation": txt[:80], "need": txt[:60], "solution_queries": [txt[:40]], "problem_queries": [txt[:40]],
                            "requirements": [{"text": "سالم", "must": True}],
                            "constraints": {"budget_toman": int(budget.group(1)) * 1_000_000 if budget else None},
                            "strength": "medium"})
        return {"needs": out[:5]}
    if stage == "verify":
        ids = re.findall(r"^\[([^\]]+)\] ", user, re.M)[:2]
        return {"matches": [{"product_id": i, "solves": "yes", "req": ["met"], "reason": "آزمایشی"} for i in ids]}
    if stage == "new_product_verify":
        ids = re.findall(r"^\[(need_[^\]]+)\]", user, re.M)[:1]
        return {"matches": [{"need_id": i, "solves": "partly", "req": ["unknown"], "reason": "آزمایشی"} for i in ids]}
    if stage == "product_extract":
        return {"name": "محصول نمونه", "price_toman": 1200000, "description": "توضیح آزمایشی", "features": [
            {"name": "رنگ", "value": "مشکی"}]}
    if stage.startswith("reply"):
        return {"reply": "سلام! شاید این به کارت بیاد: {{LINK}}"}
    return {}

"""Data contracts. ``Opportunity`` is the output contract agreed with the backend."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Strength = Literal["strong", "medium", "weak"]
STRENGTH_RANK = {"weak": 0, "medium": 1, "strong": 2}
STRENGTH_WEIGHT = {"weak": 0.4, "medium": 0.7, "strong": 1.0}
OPPORTUNITY_LABELS = {"explicit_need", "implicit_need", "on_behalf_need"}


# ── inputs ───────────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    chat_id: str
    message_id: int
    row_id: int = 0                      # monotonic id in the source table (fetch cursor)
    author_id: str = ""
    author_name: str | None = None
    author_username: str | None = None
    is_bot: bool = False
    text: str = ""
    date: datetime
    reply_to: int | None = None
    chat_title: str | None = None
    chat_username: str | None = None     # public group username → public message links
    reply_to_text: str | None = None     # parent message (joined from the archive, may be older than the engine's state)
    reply_to_author_id: str | None = None
    reply_to_author_name: str | None = None
    reply_to_date: datetime | None = None
    platform: str = "telegram"
    url: str | None = None
    profile_url: str | None = None
    search_query: str | None = None
    author_verified: bool = False
    author_bio: str | None = None


class Product(BaseModel):
    product_id: str
    business_id: str | None = None
    title: str
    description: str = ""
    product_type: str = ""
    price_toman: float | None = None
    city: str | None = None
    ships_nationwide: bool = True
    attributes: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    category_path: str = ""
    category_keywords: list[str] = Field(default_factory=list)
    discovery_priority: int = 1

    def content_hash(self) -> str:
        payload = self.model_dump(exclude={"business_id"})
        return hashlib.sha1(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


class ProductCard(BaseModel):
    product_id: str
    what_it_is: str = ""
    aliases: list[str] = Field(default_factory=list)
    problems_solved: list[str] = Field(default_factory=list)
    audience: str | None = None
    use: str | None = None
    level: str | None = None


# ── need card (engine-internal) ─────────────────────────────────────────────
class Requirement(BaseModel):
    text: str
    must: bool = False


class Constraints(BaseModel):
    budget_toman: int | None = None
    city: str | None = None
    use: str | None = None
    level: str | None = None
    other: str | None = None


class NeedCard(BaseModel):
    need_id: str
    chat_id: str
    author_id: str
    author_name: str | None = None
    author_username: str | None = None
    label: str
    is_opportunity: bool
    buyer_intent_confirmed: bool = False
    situation: str | None = None
    need: str | None = None
    solution_queries: list[str] = Field(default_factory=list)
    problem_queries: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    constraints: Constraints = Field(default_factory=Constraints)
    strength: Strength = "weak"
    emotion: str | None = None
    evidence_ids: list[int] = Field(default_factory=list)
    status: Literal["open", "resolved", "expired", "not_opportunity"] = "open"
    created_at: datetime
    updated_at: datetime
    cost_toman: float = 0.0
    llm_calls: int = 0

    def summary_text(self) -> str:
        return " ".join([self.situation or "", self.need or ""] + self.solution_queries[:3])


# ── verification / output ───────────────────────────────────────────────────
class Verdict(BaseModel):
    solves: Literal["yes", "partly"]
    met: int = 0
    unmet: int = 0
    unknown: int = 0
    total: int = 0
    conflicts: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    reason: str | None = None


class MatchedProduct(BaseModel):
    product_id: str
    match_score: float                   # 0..1, computed in code
    similarity: float                    # retrieval similarity (not a quality score)
    verdict: Verdict
    reply_draft: str | None = None


class Candidate(BaseModel):
    external_user_id: str
    customer_name: str | None = None
    username: str | None = None
    profile_url: str | None = None
    phone_number: str | None = None      # optional; only if public


class Evidence(BaseModel):
    message_id: str
    timestamp: datetime
    author: str | None = None
    text: str
    url: str | None = None


class Source(BaseModel):
    platform: str = "telegram"
    chat_id: str
    chat_title: str | None = None
    profile_url: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class NeedOut(BaseModel):
    label: str
    strength: Strength
    situation: str | None = None
    summary: str | None = None
    requirements: list[Requirement] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    priority: float = 0.0


class Cost(BaseModel):
    toman: float = 0.0
    llm_calls: int = 0


class Opportunity(BaseModel):
    opportunity_id: str
    status: Literal["open", "resolved", "expired"] = "open"
    created_at: datetime
    expires_at: datetime
    candidate: Candidate
    source: Source
    need: NeedOut
    matched_products: list[MatchedProduct] = Field(default_factory=list)
    cost: Cost = Field(default_factory=Cost)

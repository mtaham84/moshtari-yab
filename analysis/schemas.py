"""Shared data contracts between crawlers, the analysis agent and the Django panel."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Source = Literal["telegram", "x", "divar", "file", "other"]
Stage = Literal["prefilter", "triage", "deep"]
NeedType = Literal["explicit", "implicit", "none"]
IntentStage = Literal["ready_to_buy", "comparing", "initial_need", "none"]
Decision = Literal["respond", "discard"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Input: one normalized social message with its conversation context
# ---------------------------------------------------------------------------
class Author(BaseModel):
    """Message author. Never contains phone numbers or other private contact data."""

    id: str | None = None
    handle: str | None = None          # @username when public
    display_name: str | None = None
    is_bot: bool = False


class ContextMessage(BaseModel):
    """A message surrounding the target message in the same conversation."""

    id: str
    relation: Literal["previous", "parent", "reply"]
    author_name: str | None = None
    text: str = ""
    created_at: datetime | None = None


class SocialMessage(BaseModel):
    """Source-agnostic message handed from any crawler to the analysis agent."""

    uid: str                            # globally unique, e.g. "telegram:123456:789"
    source: Source
    channel_id: str | None = None       # group / chat / thread identifier
    channel_title: str | None = None
    text: str
    author: Author = Field(default_factory=Author)
    created_at: datetime | None = None
    url: str | None = None
    context: list[ContextMessage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("uid")
    @classmethod
    def _uid_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("uid must not be empty")
        return value.strip()

    def context_of(self, relation: str) -> list[ContextMessage]:
        return [c for c in self.context if c.relation == relation]


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------
class ProductProfile(BaseModel):
    product_id: int
    name: str
    description: str = ""
    target_customer: str = ""           # the most important field for matching
    price: int | None = None
    url: str                            # public product card link used in replies
    attributes: dict[str, Any] = Field(default_factory=dict)

    def short(self, desc_limit: int = 220) -> dict[str, Any]:
        """Compact version used in the cheap triage prompt."""
        from analysis.text import truncate

        return {
            "product_id": self.product_id,
            "name": self.name,
            "target_customer": truncate(self.target_customer, desc_limit),
            "description": truncate(self.description, desc_limit),
        }


# ---------------------------------------------------------------------------
# Costs and agent outputs
# ---------------------------------------------------------------------------
class StageCost(BaseModel):
    stage: Stage
    model: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    cost_toman: float = 0.0
    latency_ms: int = 0
    shared_call: bool = False           # True when the call was batched and the cost is an allocated share

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class TriageResult(BaseModel):
    message_uid: str
    product_id: int
    reason: str = ""


class DeepAnalysis(BaseModel):
    need_type: NeedType
    intent_stage: IntentStage
    user_level: str | None = None       # e.g. beginner / intermediate / expert, free text
    fit_score: int = Field(ge=0, le=100)
    decision: Decision
    reason: str
    reply_draft: str | None = None

    @model_validator(mode="after")
    def _draft_when_responding(self) -> "DeepAnalysis":
        if self.decision == "respond" and not (self.reply_draft or "").strip():
            raise ValueError("reply_draft is required when decision is 'respond'")
        if self.decision == "discard":
            self.reply_draft = None
        return self


class AgentVerdict(BaseModel):
    """Final outcome for one message (and one product, when a product was matched)."""

    message_uid: str
    source: Source
    product_id: int | None = None
    reached_stage: Stage
    final_decision: Decision = "discard"
    skip_reason: str | None = None      # why the message stopped at an earlier stage
    analysis: DeepAnalysis | None = None
    model_decision: Decision | None = None
    guard_issues: list[str] = Field(default_factory=list)
    reply_draft: str | None = None      # draft after guards (link inserted, cleaned)
    costs: list[StageCost] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @property
    def total_cost_toman(self) -> float:
        return round(sum(c.cost_toman for c in self.costs), 4)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(c.cost_usd for c in self.costs), 8)

    @property
    def total_tokens(self) -> int:
        return sum(c.total_tokens for c in self.costs)

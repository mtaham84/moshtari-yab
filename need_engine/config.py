"""All tunables in one place; every field can be overridden with an NE_* environment variable."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

try:  # optional, the crawler already depends on python-dotenv
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


def _env(name: str, default: Any) -> Any:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int) and not isinstance(default, bool):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, (dict, list)):
        return json.loads(raw)
    return raw


def _f(name: str, default: Any):
    return field(default_factory=lambda: _env(name, default))


# Similarity floors differ per embedding model; calibrate with the Colab notebook (section «کالیبراسیون»).
DEFAULT_SIM_FLOOR = {"bge-m3": 0.40, "cloudflare": 0.40, "gemini": 0.55, "hash": 0.0}


@dataclass
class EngineConfig:
    # ── LLM (OpenAI-compatible endpoint; Gemini by default) ────────────────
    llm_base_url: str = _f("NE_LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    llm_api_key: str = field(default_factory=lambda: os.environ.get("NE_LLM_API_KEY") or os.environ.get("GEMINI_API_KEY", ""))
    extract_model: str = _f("NE_EXTRACT_MODEL", "gemini-3.5-flash-lite")   # product cards + need cards
    verify_model: str = _f("NE_VERIFY_MODEL", "gemini-3.5-flash-lite")     # checklist verification
    reply_model: str = _f("NE_REPLY_MODEL", "gemini-3.5-flash-lite")       # reply drafts
    reasoning_effort: str = _f("NE_REASONING_EFFORT", "")                   # e.g. "low"; empty = provider default
    llm_timeout: int = _f("NE_LLM_TIMEOUT", 150)
    llm_max_retries: int = _f("NE_LLM_MAX_RETRIES", 6)
    max_workers: int = _f("NE_MAX_WORKERS", 2)
    # USD per 1M tokens (input, output)
    prices: dict = _f("NE_PRICES", {
        "gemini-3.5-flash-lite": [0.30, 2.50], "gemini-3.5-flash": [1.50, 9.00], "gemini-2.5-flash-lite": [0.10, 0.40],
    })
    # rpm / tpm / rpd per model (None = unlimited). Copy real numbers from AI Studio → Rate limits.
    rate_limits: dict = _f("NE_RATE_LIMITS", {
        "gemini-3.5-flash-lite": {"rpm": 10, "tpm": 200000, "rpd": 500},
        "gemini-3.5-flash": {"rpm": 5, "tpm": 200000, "rpd": 100},
        "gemini-2.5-flash-lite": {"rpm": 15, "tpm": 250000, "rpd": 1000},
        "gemini-embedding-001": {"rpm": 50, "tpm": None, "rpd": None},
    })
    default_rate_limit: dict = field(default_factory=lambda: {"rpm": 5, "tpm": 100000, "rpd": None})
    rate_safety: float = _f("NE_RATE_SAFETY", 0.9)
    usd_to_toman: float = _f("NE_USD_TO_TOMAN", 100000.0)

    # ── Embeddings (API only; no local model on the server) ────────────────
    embed_backend: str = _f("NE_EMBED_BACKEND", "gemini")                   # gemini | cloudflare | hash (tests)
    embed_model: str = _f("NE_EMBED_MODEL", "gemini-embedding-001")
    embed_price_per_m: float = _f("NE_EMBED_PRICE_PER_M", 0.0)
    cf_account_id: str = _f("NE_CF_ACCOUNT_ID", "")
    cf_api_token: str = _f("NE_CF_API_TOKEN", "")
    cf_model: str = _f("NE_CF_MODEL", "@cf/baai/bge-m3")

    # ── Sources (read-only) ─────────────────────────────────────────────────
    # sqlite:///data/leads.db  |  postgresql://user:pass@host:5432/db  |  jsonl:path/to/chats.jsonl
    messages_dsn: str = _f("NE_MESSAGES_DSN", "sqlite:///data/leads.db")
    messages_table: str = _f("NE_MESSAGES_TABLE", "messages")
    groups_table: str = _f("NE_GROUPS_TABLE", "group_monitors")
    # column names in the messages table (override if the Postgres schema renames them)
    messages_columns: dict = _f("NE_MESSAGES_COLUMNS", {
        "row_id": "id", "chat_id": "group_id", "message_id": "msg_id", "author_id": "sender_id",
        "author_name": "sender_name", "author_username": "sender_username", "is_bot": "sender_is_bot",
        "text": "text", "date": "date", "reply_to": "reply_to_msg_id",
    })
    # jsonl:path/to/products.jsonl  |  sql:<dsn>  (reads Django's products_product table read-only)
    products_source: str = _f("NE_PRODUCTS_SOURCE", "sql:sqlite:///db.sqlite3")  # Django panel DB, or jsonl:path
    products_table: str = _f("NE_PRODUCTS_TABLE", "products_product")
    fetch_batch: int = _f("NE_FETCH_BATCH", 2000)

    # ── Engine-private storage and output ───────────────────────────────────
    state_path: str = _f("NE_STATE_PATH", "data/need_engine_state.db")
    output_path: str = _f("NE_OUTPUT_PATH", "data/opportunities.jsonl")    # JSONL sink (DB write comes later)
    poll_seconds: int = _f("NE_POLL_SECONDS", 20)

    # ── When to analyse a chat (streaming triggers) ─────────────────────────
    window_size: int = _f("NE_WINDOW_SIZE", 40)          # new messages per LLM call
    context_messages: int = _f("NE_CONTEXT_MESSAGES", 10)  # already-analysed messages shown as context
    trigger_count: int = _f("NE_TRIGGER_COUNT", 40)        # analyse when this many new messages are pending…
    silence_minutes: float = _f("NE_SILENCE_MINUTES", 10.0)  # …or the chat has been quiet this long…
    max_wait_minutes: float = _f("NE_MAX_WAIT_MINUTES", 30.0)  # …or the oldest pending message is this old
    gap_marker_minutes: float = _f("NE_GAP_MARKER_MINUTES", 20.0)  # show «⏸ N hours later» markers
    min_text_chars: int = _f("NE_MIN_TEXT_CHARS", 2)

    # ── Needs ───────────────────────────────────────────────────────────────
    person_merge_sim: float = _f("NE_PERSON_MERGE_SIM", 0.70)
    need_ttl_days: float = _f("NE_NEED_TTL_DAYS", 7.0)
    verify_min_strength: str = _f("NE_VERIFY_MIN_STRENGTH", "weak")  # weak | medium | strong

    # ── Retrieval (adaptive threshold, no fixed K) ──────────────────────────
    sim_top_n: int = _f("NE_SIM_TOP_N", 10)
    sim_gap: float = _f("NE_SIM_GAP", 0.10)
    sim_floor: float = _f("NE_SIM_FLOOR", -1.0)            # -1 → DEFAULT_SIM_FLOOR[embed_backend]
    bm25_extra: int = _f("NE_BM25_EXTRA", 5)
    cand_min: int = _f("NE_CAND_MIN", 5)
    cand_max: int = _f("NE_CAND_MAX", 200)
    bm25_weight: float = _f("NE_BM25_WEIGHT", 0.5)
    rrf_k: int = _f("NE_RRF_K", 60)
    verify_batch: int = _f("NE_VERIFY_BATCH", 25)
    new_product_top_needs: int = _f("NE_NEW_PRODUCT_TOP_NEEDS", 20)

    # ── Scoring (computed in code, never by the LLM) ────────────────────────
    min_score: float = _f("NE_MIN_SCORE", 15.0)
    solves_factor: dict = field(default_factory=lambda: {"yes": 1.0, "partly": 0.6})
    req_value: dict = field(default_factory=lambda: {"met": 1.0, "unknown": 0.5, "unmet": 0.0})
    req_weight: dict = field(default_factory=lambda: {"must": 2.0, "nice": 1.0})
    must_unmet_factor: float = _f("NE_MUST_UNMET_FACTOR", 0.6)
    budget_tolerance: float = _f("NE_BUDGET_TOLERANCE", 1.25)
    budget_factors: list = field(default_factory=lambda: [(1.25, 1.0), (1.6, 0.8), (2.0, 0.6), (3.0, 0.4)])
    budget_hard_factor: float = _f("NE_BUDGET_HARD_FACTOR", 3.0)
    city_factor: float = _f("NE_CITY_FACTOR", 0.6)
    max_products_per_opportunity: int = _f("NE_MAX_PRODUCTS_PER_OPPORTUNITY", 10)

    # ── Replies ─────────────────────────────────────────────────────────────
    write_replies: bool = _f("NE_WRITE_REPLIES", True)
    reply_top_n: int = _f("NE_REPLY_TOP_N", 3)
    product_url_template: str = _f("NE_PRODUCT_URL_TEMPLATE", "https://customerweb.ir/p/{product_id}?ref={opportunity_id}")

    def effective_sim_floor(self) -> float:
        if self.sim_floor >= 0:
            return self.sim_floor
        key = "bge-m3" if self.embed_backend == "cloudflare" else self.embed_backend
        return DEFAULT_SIM_FLOOR.get(key, 0.4)

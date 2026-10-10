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


def database_dsn() -> str:
    url = os.environ.get("NE_DATABASE_URL") or os.environ.get("DATABASE_URL")
    if url:
        return url
    from psycopg.conninfo import make_conninfo

    params = {"host": os.environ.get("POSTGRES_HOST", "127.0.0.1"), "port": os.environ.get("POSTGRES_PORT", "5432"),
              "dbname": os.environ.get("POSTGRES_DB", "customer_yab"), "user": os.environ.get("POSTGRES_USER", "postgres"),
              "password": os.environ.get("POSTGRES_PASSWORD", "")}
    return make_conninfo(**{k: v for k, v in params.items() if v})


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
    # Providers / models / prices / wallets from the admin panel (/ops/); NE_* values above are the fallback
    model_registry: bool = _f("NE_MODEL_REGISTRY", True)
    registry_ttl: float = _f("NE_REGISTRY_TTL", 60.0)
    # a chat watched privately by several sellers is analysed ONCE, but every owner is charged the full extraction cost
    charge_each_owner: bool = _f("NE_CHARGE_EACH_OWNER", True)
    release_batch: int = _f("NE_RELEASE_BATCH", 200)        # queued needs re-matched per seller per run after a top-up
    providers_table: str = _f("NE_PROVIDERS_TABLE", "public.billing_provider")
    models_table: str = _f("NE_MODELS_TABLE", "public.billing_aimodel")
    billing_settings_table: str = _f("NE_BILLING_SETTINGS_TABLE", "public.billing_billingsettings")
    wallets_table: str = _f("NE_WALLETS_TABLE", "public.billing_wallet")

    # ── Embeddings (API only; no local model on the server) ────────────────
    embed_backend: str = _f("NE_EMBED_BACKEND", "gemini")                   # gemini | cloudflare | hash (tests)
    embed_model: str = _f("NE_EMBED_MODEL", "gemini-embedding-001")
    embed_price_per_m: float = _f("NE_EMBED_PRICE_PER_M", 0.0)
    cf_account_id: str = _f("NE_CF_ACCOUNT_ID", "")
    cf_api_token: str = _f("NE_CF_API_TOKEN", "")
    cf_model: str = _f("NE_CF_MODEL", "@cf/baai/bge-m3")

    # ── Storage: one PostgreSQL database (Django + crawler + engine) ─────────
    # NE_DATABASE_URL / DATABASE_URL, or the POSTGRES_* variables shared with Django.
    database_url: str = field(default_factory=lambda: database_dsn())
    # sources (read-only): "db" = crawler tables / Django products in the same database, or "jsonl:path" (tests, demo)
    messages_source: str = _f("NE_MESSAGES_SOURCE", "db")
    crawler_schema: str = _f("NE_CRAWLER_SCHEMA", "crawler")
    products_source: str = _f("NE_PRODUCTS_SOURCE", "db")
    products_table: str = _f("NE_PRODUCTS_TABLE", "public.products_product")
    fetch_batch: int = _f("NE_FETCH_BATCH", 2000)
    # which sellers may see a chat's needs (need_engine/access.py): "panel" = discovery_monitoredcommunity, "open" = all
    source_access: str = _f("NE_SOURCE_ACCESS", "panel")
    communities_table: str = _f("NE_COMMUNITIES_TABLE", "public.discovery_monitoredcommunity")
    styles_table: str = _f("NE_STYLES_TABLE", "public.businesses_messagestyle")   # sellers' reply style
    x_enabled: bool = _f("NE_X_ENABLED", False)
    # product: every seller product with «جستجوی مشتری در X» has its own queries and analysis, billed to its seller
    # public: one generic search/extraction for all products, platform pays (old behaviour) | both
    x_mode: str = _f("NE_X_MODE", "product")
    x_bio_chars: int = _f("NE_X_BIO_CHARS", 120)              # bio sent to the LLM (spots shops; costs tokens)
    x_fit_yes_score: float = _f("NE_X_FIT_YES_SCORE", 0.9)      # per-product mode: match score of fit yes / partly
    x_fit_partly_score: float = _f("NE_X_FIT_PARTLY_SCORE", 0.7)
    x_max_per_run: int = _f("NE_X_MAX_PER_RUN", 200)
    x_min_match_score: float = _f("NE_X_MIN_MATCH_SCORE", 65.0)
    x_prefilter: str = _f("NE_X_PREFILTER", "shadow")
    x_min_chars: int = _f("NE_X_MIN_CHARS", 15)
    x_min_persian_ratio: float = _f("NE_X_MIN_PERSIAN_RATIO", 0.5)
    x_allow_finglish: bool = _f("NE_X_ALLOW_FINGLISH", True)
    x_min_relevance: float = _f("NE_X_MIN_RELEVANCE", 0.05)
    x_max_posts_per_author_per_run: int = _f("NE_X_MAX_POSTS_PER_AUTHOR_PER_RUN", 3)
    x_batch_size: int = _f("NE_X_BATCH_SIZE", 8)
    x_batch_max_chars: int = _f("NE_X_BATCH_MAX_CHARS", 6000)
    x_reply_top_n: int = _f("NE_X_REPLY_TOP_N", 1)
    x_reply_max_chars: int = _f("NE_X_REPLY_MAX_CHARS", 280)
    x_public_target_chars: int = _f("NE_X_PUBLIC_TARGET_CHARS", 240)
    x_dedup_days: int = _f("NE_X_DEDUP_DAYS", 7)
    x_dedup_threshold: float = _f("NE_X_DEDUP_THRESHOLD", 0.8)
    x_intent_base_url: str = _f("X_INTENT_BASE_URL", "https://x.com/intent/post")
    x_ingest_max_per_run: int = _f("NE_X_INGEST_MAX_PER_RUN", 2000)
    x_process_order: str = _f("NE_X_PROCESS_ORDER", "newest")
    x_max_post_age_hours: float = _f("NE_X_MAX_POST_AGE_HOURS", 168.0)   # matches X_QUERY_FIRST_LOOKBACK_HOURS
    # most real people have no bio, so "unknown" author type is accepted; organisations/verified/shop bios still rejected
    x_allow_unknown_author: bool = _f("NE_X_ALLOW_UNKNOWN_AUTHOR", True)
    x_need_ttl_hours: float = _f("NE_X_NEED_TTL_HOURS", 168.0)
    x_freshness_halflife_hours: float = _f("NE_X_FRESHNESS_HALFLIFE_HOURS", 12.0)
    x_refresh_batch_size: int = _f("NE_X_REFRESH_BATCH_SIZE", 200)
    x_thread_replies: bool = _f("NE_X_THREAD_REPLIES", False)
    x_thread_min_match_score: float = _f("NE_X_THREAD_MIN_MATCH_SCORE", 70.0)
    x_thread_max_age_hours: float = _f("X_THREAD_MAX_AGE_HOURS", 36.0)
    # engine-private state, pgvector vectors and the opportunities table the panel syncs from
    state_schema: str = _f("NE_STATE_SCHEMA", "need_engine")
    output_jsonl: str = _f("NE_OUTPUT_JSONL", "")          # optional extra copy of every opportunity (debug/demo)
    poll_seconds: int = _f("NE_POLL_SECONDS", 20)

    # ── When to analyse a chat (streaming triggers) ─────────────────────────
    window_size: int = _f("NE_WINDOW_SIZE", 50)          # new messages per LLM call
    context_messages: int = _f("NE_CONTEXT_MESSAGES", 10)  # already-analysed messages shown as context
    trigger_count: int = _f("NE_TRIGGER_COUNT", 50)        # analyse a chat every N new messages (count only, no timers)
    max_wait_minutes: float = _f("NE_MAX_WAIT_MINUTES", 0.0)  # optional fallback, 0 = off: analyse leftovers this old
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
    # links in drafts go to the seller's own page through the panel's click counter: {base}/r/<product>/?ref=<opp>
    public_base_url: str = _f("NE_PUBLIC_BASE_URL", "http://localhost:8000")

    def click_url(self, product_id: str, opportunity_id: str) -> str:
        return f"{self.public_base_url.rstrip('/')}/r/{product_id}/?ref={opportunity_id}"

    def effective_sim_floor(self) -> float:
        if self.sim_floor >= 0:
            return self.sim_floor
        key = "bge-m3" if self.embed_backend == "cloudflare" else self.embed_backend
        return DEFAULT_SIM_FLOOR.get(key, 0.4)

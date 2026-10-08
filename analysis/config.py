"""Configuration for the analysis agent (read from environment / .env)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

try:  # python-dotenv is optional
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if load_dotenv and (PROJECT_ROOT / ".env").exists():
    load_dotenv(PROJECT_ROOT / ".env")

log = logging.getLogger("analysis.config")


def _int(key: str, default: int) -> int:
    try:
        return int((os.getenv(key) or "").strip())
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float((os.getenv(key) or "").strip())
    except ValueError:
        return default


def parse_model_prices(raw: str) -> dict[str, tuple[float, float]]:
    """Parse "model:input_usd_per_M:output_usd_per_M,model2:..." into a dict."""
    prices: dict[str, tuple[float, float]] = {}
    for chunk in (raw or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            model, inp, out = chunk.rsplit(":", 2)
            prices[model.strip()] = (float(inp), float(out))
        except ValueError:
            log.warning("Ignoring malformed MODEL_PRICES entry: %r", chunk)
    return prices


@dataclass
class AnalysisSettings:
    # Storage
    db_path: str = field(default_factory=lambda: os.getenv("ANALYSIS_DB_PATH", str(PROJECT_ROOT / "data" / "analysis.sqlite3")))

    # Catalog: a JSON file path or an http(s) URL of the Django agent feed
    catalog_source: str = field(default_factory=lambda: os.getenv("CATALOG_SOURCE", str(PROJECT_ROOT / "data" / "sample" / "catalog.json")))
    catalog_token: str = field(default_factory=lambda: os.getenv("AGENT_API_TOKEN", ""))
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/"))

    # LLM provider (any OpenAI-compatible API: Groq, xAI, OpenAI, ...)
    llm_base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"))
    llm_api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    triage_model: str = field(default_factory=lambda: os.getenv("TRIAGE_MODEL", "llama-3.1-8b-instant"))
    deep_model: str = field(default_factory=lambda: os.getenv("DEEP_MODEL", "llama-3.3-70b-versatile"))
    llm_timeout: int = field(default_factory=lambda: _int("LLM_TIMEOUT", 45))
    llm_max_retries: int = field(default_factory=lambda: _int("LLM_MAX_RETRIES", 3))
    model_prices: dict[str, tuple[float, float]] = field(default_factory=lambda: parse_model_prices(os.getenv("MODEL_PRICES", "")))
    usd_to_toman: float = field(default_factory=lambda: _float("USD_TO_TOMAN", 0.0) or _float("DOLLAR_TO_TOMAN_RATE", 0.0))

    # Funnel behaviour
    triage_batch_size: int = field(default_factory=lambda: max(1, _int("TRIAGE_BATCH_SIZE", 15)))
    fit_threshold: int = field(default_factory=lambda: _int("FIT_THRESHOLD", 60))
    max_llm_calls_per_run: int = field(default_factory=lambda: _int("MAX_LLM_CALLS_PER_RUN", 200))
    max_attempts: int = field(default_factory=lambda: max(1, _int("ANALYSIS_MAX_ATTEMPTS", 3)))
    reply_max_chars: int = field(default_factory=lambda: _int("REPLY_MAX_CHARS", 600))
    context_char_budget: int = field(default_factory=lambda: _int("CONTEXT_CHAR_BUDGET", 2500))

    @property
    def pricing_configured(self) -> bool:
        return bool(self.model_prices) and self.usd_to_toman > 0

    def price_for(self, model: str) -> tuple[float, float]:
        return self.model_prices.get(model, (0.0, 0.0))


def get_settings() -> AnalysisSettings:
    return AnalysisSettings()

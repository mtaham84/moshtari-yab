"""need_engine — conversation-level customer discovery core.

Reads raw group messages (written by the crawler), understands each conversation window once,
builds need cards, matches them against product cards (embeddings + BM25 + adaptive threshold),
verifies with an LLM checklist, scores in code, and emits Opportunity objects.

It never writes to the crawler's or the panel's tables (read-only connections). Its own state (cursors,
open needs, product cards, pgvector vectors, LLM cache, costs, published opportunities) lives in a separate
PostgreSQL schema (``NE_STATE_SCHEMA``, default ``need_engine``).
"""
from need_engine.config import EngineConfig

__all__ = ["EngineConfig", "NeedEngine"]

def __getattr__(name: str):
    if name == "NeedEngine":
        from need_engine.engine import NeedEngine

        return NeedEngine
    raise AttributeError(name)

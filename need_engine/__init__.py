"""need_engine — conversation-level customer discovery core.

Reads raw group messages (written by the crawler), understands each conversation window once,
builds need cards, matches them against product cards (embeddings + BM25 + adaptive threshold),
verifies with an LLM checklist, scores in code, and emits Opportunity objects.

It never writes to the main application database. Its own state (cursors, open needs, product
cards/vectors, LLM cache, costs) lives in a separate SQLite file (``NE_STATE_PATH``).
"""
from need_engine.config import EngineConfig
from need_engine.engine import NeedEngine

__all__ = ["EngineConfig", "NeedEngine"]

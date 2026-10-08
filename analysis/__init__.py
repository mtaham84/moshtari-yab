"""Source-agnostic message analysis agent for Moshtari-Yab.

Every crawler (Telegram, X dataset, ...) converts its native data into
``analysis.schemas.SocialMessage`` and enqueues it in ``analysis.store.AnalysisStore``.
The analysis worker then runs the funnel (prefilter -> triage -> deep analysis)
and records a verdict plus the exact token/cost breakdown for every message.
"""

__all__ = ["schemas", "config", "store", "catalog"]

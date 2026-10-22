"""Shared score threshold for Jev selection and its reports."""
from __future__ import annotations

import os


def jev_selection_threshold() -> float:
    try:
        threshold = float(os.getenv("IG_JEV_SELECTION_THRESHOLD", "0.7"))
    except ValueError:
        return 0.7
    return threshold if 0.0 <= threshold <= 1.0 else 0.7

"""Read-only access to the need_engine tables (costs, groups, sources) from the panel."""
from __future__ import annotations

import logging
import re
import time

from django.conf import settings
from django.db import connection



log = logging.getLogger(__name__)


# ── engine tables (read-only from here) ───────────────────────────────────────
def engine_schema() -> str:
    s = settings.NEED_ENGINE_SCHEMA
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", s):
        raise ValueError(f"invalid schema: {s!r}")
    return s


def engine_table_exists(name: str) -> bool:
    with connection.cursor() as c:
        c.execute("SELECT to_regclass(%s)", [f"{engine_schema()}.{name}"])
        return c.fetchone()[0] is not None


def engine_has_column(table: str, column: str) -> bool:
    with connection.cursor() as c:
        c.execute("SELECT 1 FROM information_schema.columns WHERE table_schema = %s AND table_name = %s AND column_name = %s",
                  [engine_schema(), table, column])
        return c.fetchone() is not None


def billed_usd_expr() -> str:
    """Billed share of a cost row: a chat watched privately by several sellers is analysed once, each owner pays it all."""
    return "usd * charge_factor" if engine_has_column("costs", "charge_factor") else "usd"


def engine_rows(sql: str, params=()) -> list[dict]:
    with connection.cursor() as c:
        c.execute(sql.replace("{s}", engine_schema()), list(params))
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, r)) for r in c.fetchall()]


# ── numbers for the admin panel ───────────────────────────────────────────────
def since_ts(days: float) -> float:
    return time.time() - days * 86400

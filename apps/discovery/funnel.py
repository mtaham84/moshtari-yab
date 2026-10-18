from __future__ import annotations

import os
import re
from datetime import datetime

from django.conf import settings
from django.db import connection
from django.db.models import Count, Sum
from .models import Opportunity


def _schema() -> str:
    schema = settings.NEED_ENGINE_SCHEMA
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError(f"invalid schema: {schema!r}")
    return schema


def _table_exists(name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f"{_schema()}.{name}"])
        return cursor.fetchone()[0] is not None


def _engine_count(table: str, source: str, since: datetime, until: datetime, business=None,
                  time_column: str = "decided_at", filters: str = "") -> int | None:
    if not _table_exists(table):
        return None
    timestamp_value = f"to_timestamp(e.{time_column})" if time_column == "ts" else f"e.{time_column}"
    params = [since, until]
    from_clause = f"{_schema()}.{table} e"
    where = [f"{timestamp_value} >= %s", f"{timestamp_value} < %s"]
    if source == "instagram":
        return None
    if table == "chat_analysed":
        where.append("e.chat_id = 'x:public'" if source == "x" else "e.chat_id NOT LIKE 'x:%'")
    elif source == "x":
        where.append("e.chat_id LIKE 'x:%'")
    else:
        if table == "chat_analysed":
            where.append("e.chat_id NOT LIKE 'x:%'")
        else:
            return None
    if business is not None:
        if source != "x" or table not in {"x_filter_decisions", "x_hit_results"}:
            return None
        from apps.products.models import Product

        product_table = connection.ops.quote_name(Product._meta.db_table)
        from_clause += f" JOIN {product_table} p ON e.chat_id = 'x:p:' || p.id::text"
        where.append("p.business_id = %s")
        params.append(business.pk)
    if filters:
        where.append(filters.replace("outcome", "e.outcome").replace("keep", "e.keep").replace("need_id", "e.need_id").replace("mode", "e.mode"))
    sql = f"SELECT COUNT(*) FROM {from_clause} WHERE {' AND '.join(where)}"
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        return int(cursor.fetchone()[0])


def funnel(source: str, since: datetime, until: datetime, business=None) -> dict:
    if source not in {"telegram", "x", "instagram"}:
        raise ValueError("unsupported source")
    if since.tzinfo is None or until.tzinfo is None:
        raise ValueError("since and until must be timezone-aware")
    if until <= since:
        raise ValueError("until must be after since")

    opportunities = Opportunity.objects.filter(source_platform=source, created_at__gte=since, created_at__lt=until)
    if business is not None:
        opportunities = opportunities.filter(business=business)

    collected = None
    extraction_count = None
    crawler_schema = settings.TG_DB_SCHEMA
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", crawler_schema):
        raise ValueError(f"invalid crawler schema: {crawler_schema!r}")
    if business is None and source == "telegram":
        if _schema_table_exists(crawler_schema, "tg_messages"):
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {crawler_schema}.tg_messages WHERE date >= %s AND date < %s AND NOT is_context AND NOT is_service",
                    [since, until],
                )
                collected = int(cursor.fetchone()[0])
    elif business is None and source == "x":
        x_schema = os.getenv("NE_CRAWLER_SCHEMA", crawler_schema)
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", x_schema):
            raise ValueError(f"invalid X crawler schema: {x_schema!r}")
        if _schema_table_exists(x_schema, "x_posts"):
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) FROM {x_schema}.x_posts WHERE COALESCE(created_at, loaded_at) >= %s AND COALESCE(created_at, loaded_at) < %s",
                    [since, until],
                )
                collected = int(cursor.fetchone()[0])

    engine_counts = {"prefiltered_in": None}
    if source == "x" and os.getenv("NE_X_PREFILTER", "shadow") == "on":
        engine_counts["prefiltered_in"] = _engine_count("x_filter_decisions", source, since, until, business, filters="keep AND mode = 'on'")
    matched = None
    x_enabled = os.getenv("NE_X_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    x_mode = os.getenv("NE_X_MODE", "product")
    if source == "x" and x_enabled and x_mode == "product" and business is None:
        matched = _engine_count("x_hit_results", source, since, until, filters="outcome = 'customer'")
    elif source == "x" and business is not None and x_enabled and x_mode == "product":
        matched = _engine_count("x_hit_results", source, since, until, business, filters="outcome = 'customer'")
    if source == "x" and x_enabled and x_mode == "product":
        extraction_count = _engine_count("x_hit_results", source, since, until, business, filters="need_id IS NOT NULL")
    shown = opportunities.count()
    opportunity_costs = opportunities.aggregate(
        total=Sum("ai_analysis__cost_toman"),
        analyses=Count("ai_analysis"),
    )
    contacted = opportunities.filter(status__in=["CONTACTED", "CONVERTED"]).count()
    sold = opportunities.filter(status="CONVERTED").count()
    feedback_opportunities = opportunities.filter(feedback__in=["relevant", "irrelevant"])
    labels = feedback_opportunities.count()
    relevant = feedback_opportunities.filter(feedback="relevant").count()
    return {
        "source": source,
        "collected": collected if business is None else None,
        "prefiltered_in": engine_counts["prefiltered_in"] if source == "x" and os.getenv("NE_X_PREFILTER", "shadow") == "on" else None,
        "extracted": extraction_count,
        "matched": matched,
        "shown": shown,
        "contacted": contacted,
        "sold": sold,
        "cost_total": None,
        "cost_per_message": None,
        "cost_per_shown_opportunity": (
            opportunity_costs["total"] / shown
            if business is not None and shown and opportunity_costs["analyses"] == shown
            and opportunity_costs["total"] is not None
            else None
        ),
        "precision_from_feedback": relevant / labels if labels >= 20 else None,
    }


def _schema_table_exists(schema: str, name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT to_regclass(%s)", [f"{schema}.{name}"])
        return cursor.fetchone()[0] is not None





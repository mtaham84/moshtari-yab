"""Data retention for source archives and transient need_engine state."""
from __future__ import annotations

import re
import json
import hashlib
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
from django.db.models.functions import Coalesce

from file_lock import exclusive_file_lock


def _schema_name(value: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", value):
        raise ValueError(f"invalid PostgreSQL schema name: {value!r}")
    return value


def _table_exists(conn, schema: str, table: str) -> bool:
    return conn.execute("SELECT to_regclass(%s)", [f"{schema}.{table}"]).fetchone()[0] is not None


def _count(conn, table: str, where: str, params: tuple) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0])


def _postgres_retention_lock(conn, schema: str) -> None:
    """Serialize concurrent retention commands against the same PostgreSQL schemas."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"moshtari-yab-retention:{schema}"])


def scrub_engine_opportunity_payload(payload: dict) -> dict:
    """Retain analytical and sales fields while removing source/customer text and identifiers."""
    clean = dict(payload)
    # Payloads are stored as JSON, so keep the sentinel as an ISO string rather than a
    # datetime object (which psycopg's Jsonb encoder cannot serialize by default).
    clean["created_at"] = "1970-01-01T00:00:00+00:00"
    clean["expires_at"] = clean["created_at"]
    if clean.get("status") == "open":
        clean["status"] = "expired"
    candidate = dict(clean.get("candidate") or {})
    for field in ("external_user_id", "customer_name", "username", "profile_url", "phone_number"):
        candidate[field] = "" if field in {"external_user_id", "customer_name", "username"} else None
    candidate["contacts"], candidate["linked_identities"] = [], []
    clean["candidate"] = candidate

    source = dict(clean.get("source") or {})
    source["chat_id"], source["chat_title"], source["profile_url"] = "", None, None
    source["platform"] = source.get("platform") or "telegram"
    source["search_query"] = None
    source["evidence"] = []
    clean["source"] = source
    clean["source_query"] = None
    clean["source_posted_at"] = None
    clean["source_posted_at_estimated"] = False
    clean["retention_scrubbed"] = True

    need = dict(clean.get("need") or {})
    need.update(label="", situation=None, summary=None, requirements=[], constraints={}, priority=0)
    clean["need"] = need

    matches = []
    for item in clean.get("matched_products") or []:
        match = dict(item)
        match.pop("reply_draft", None)
        match.pop("reply_variants", None)
        verdict = dict(match.get("verdict") or {})
        verdict.update(reason=None, conflicts=[], checks=[])
        match["verdict"] = verdict
        matches.append(match)
    clean["matched_products"] = matches
    clean["thread"] = None
    return clean


def scrub_panel_opportunities(cutoff: datetime, *, delete: bool) -> dict[str, int]:
    """Anonymize source identity and quotes while keeping sales records and opportunity-level metrics."""
    from django.db import transaction
    from apps.discovery.retention import customer_ids_to_anonymize

    from apps.discovery.models import AIAnalysis, Customer, Evidence, Opportunity, OpportunityProductMatch

    qs = Opportunity.objects.filter(
        source_platform__in=("telegram", "x"),
        trace_metadata__retention_scrubbed__isnull=True,
    ).annotate(_retention_age=Coalesce("source_posted_at", "source_message_timestamp", "created_at")).filter(
        _retention_age__lt=cutoff
    )
    report = {
        "panel_source_opportunities": qs.count(),
        "panel_source_evidence": Evidence.objects.filter(opportunity__in=qs).count(),
        "panel_customers_to_anonymize": 0,
    }
    if report["panel_source_opportunities"]:
        report["panel_customers_to_anonymize"] = customer_ids_to_anonymize(qs).count()
    if not delete or not report["panel_source_opportunities"]:
        return report
    with transaction.atomic():
        opportunity_ids = list(qs.values_list("pk", flat=True))
        if not opportunity_ids:
            return report
        Opportunity.objects.filter(pk__in=opportunity_ids, status__in=("NEW", "REVIEWED")).update(status="EXPIRED")
        # Update once per affected business and preserve a customer row if that seller has a
        # retained opportunity or an actual order. Avoid per-opportunity nested scans.
        customer_business_rows = list(Opportunity.objects.filter(pk__in=opportunity_ids).values_list("customer_id", "business_id"))
        customer_businesses: dict[int, set[int]] = {}
        for customer_id, business_id in customer_business_rows:
            customer_businesses.setdefault(customer_id, set()).add(business_id)
        customers_to_anonymize = set(customer_ids_to_anonymize(
            Opportunity.objects.filter(pk__in=opportunity_ids)
        ).values_list("pk", flat=True))
        Opportunity.objects.filter(pk__in=opportunity_ids).update(source_raw_message="", normalized_message="", source_message_id="", source_query="",
                  category=None, category_name_snapshot="", category_path_snapshot="", expires_at=None,
                  source_message_timestamp=None, source_posted_at=None, source_posted_at_estimated=False,
                  thread_root_tweet_id=None, parent_opportunity=None,
                  demand_count=0, competitor_replies=[], thread_fetched_at=None, trace_metadata={"retention_scrubbed": True})
        Evidence.objects.filter(opportunity_id__in=opportunity_ids).delete()
        AIAnalysis.objects.filter(opportunity_id__in=opportunity_ids).update(need="", why_selected="", suggested_reply="", model_name="", model_version="")
        OpportunityProductMatch.objects.filter(opportunity_id__in=opportunity_ids).update(
            recommendation_reason="", matching_attributes={}, reply_variants=None
        )
        # Customer identity is seller scoped, even if two sellers saw the same source person.
        for customer_id, business_ids in customer_businesses.items():
            for business_id in business_ids:
                if customer_id not in customers_to_anonymize:
                    continue
                Customer.objects.filter(pk=customer_id, business_id=business_id).update(
                    name="", phone_number=None, external_user_id="", source_username="", source_profile_url="",
                    metadata={}, contact_channels=[], linked_identities=[]
                )
        Opportunity.objects.filter(pk__in=opportunity_ids).update(normalized_message="")
    return report


def purge_panel_x_posts(cutoff: datetime, *, delete: bool, _retention_locked: bool = False) -> dict[str, int]:
    """Remove stale X collector exports from the shared volume after DB records are purged."""
    import os

    from django.conf import settings
    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    configured_root = Path(os.getenv("X_COLLECT_OUTPUT_DIR", "data/x_collected"))
    root = (configured_root if configured_root.is_absolute() else settings.BASE_DIR / configured_root).resolve()
    if not root.is_dir():
        return {"x_collector_files": 0}
    targets = [root / "status.json", *root.glob("????-??-??.jsonl")]
    latest = root / "latest.jsonl"
    guard = nullcontext() if _retention_locked else exclusive_file_lock(root / ".retention_guard.lock")
    with guard:
        return _purge_panel_x_posts_locked(root, cutoff, delete, targets, latest)


def _purge_panel_x_posts_locked(root, cutoff, delete, targets, latest):
    import json
    import os
    from datetime import datetime, timezone as dt_timezone

    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    file_count = 0
    record_count = 0
    for path in [latest, *targets[1:]]:
        if not path.is_file():
            continue
        with exclusive_file_lock(path.with_suffix(path.suffix + ".lock")):
            record_count += _scrub_stale_jsonl(path, cutoff, delete=delete)

    status_path = root / "status.json"
    stale_status_queries = 0
    if status_path.is_file():
        with exclusive_file_lock(status_path.with_suffix(status_path.suffix + ".lock")):
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                if not isinstance(status, dict):
                    raise ValueError("invalid X collector status object")
                updated = status.get("updated_at")
                modified = parse_datetime(updated) if updated else None
                if modified and timezone.is_naive(modified):
                    modified = timezone.make_aware(modified, timezone.get_default_timezone())
                if modified and modified < cutoff:
                    file_count += 1
                    if delete:
                        status_path.unlink(missing_ok=True)
                elif isinstance(status, dict) and isinstance(status.get("queries"), list):
                    kept_queries = []
                    for query in status["queries"]:
                        ran_at = parse_datetime(str(query.get("last_run_at") or "")) if isinstance(query, dict) else None
                        if ran_at and timezone.is_naive(ran_at):
                            ran_at = timezone.make_aware(ran_at, timezone.get_default_timezone())
                        if ran_at and ran_at < cutoff:
                            stale_status_queries += 1
                        else:
                            kept_queries.append(query)
                    if delete and stale_status_queries:
                        status["queries"] = kept_queries
                        temporary = status_path.with_suffix(status_path.suffix + ".tmp")
                        temporary.write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
                        os.replace(temporary, status_path)
            except (OSError, ValueError, TypeError):
                pass

    for path in targets[1:]:
        if not path.is_file():
            continue
        try:
            path.resolve().relative_to(root)
            day = datetime.strptime(path.name[:10], "%Y-%m-%d").replace(tzinfo=dt_timezone.utc)
        except (ValueError, OSError):
            continue
        if day + timedelta(days=1) > cutoff:
            continue
        file_count += 1
        if delete:
            path.unlink(missing_ok=True)
    return {"x_collector_files": file_count, "x_collector_records": record_count,
            "x_collector_status_queries": stale_status_queries}


def _scrub_stale_jsonl(path: Path, cutoff: datetime, *, delete: bool) -> int:
    import json

    from django.utils import timezone
    from django.utils.dateparse import parse_datetime

    stale_records = 0
    if not delete:
        with path.open("rb") as source:
            for line in source:
                try:
                    record = json.loads(line)
                    raw_date = record.get("created_at")
                    if isinstance(raw_date, (int, float)):
                        stamp = raw_date / 1000 if raw_date > 10_000_000_000 else raw_date
                        record_date = datetime.fromtimestamp(stamp, tz=dt_timezone.utc)
                    else:
                        record_date = parse_datetime(str(raw_date or ""))
                        if record_date and timezone.is_naive(record_date):
                            record_date = timezone.make_aware(record_date, timezone=dt_timezone.utc)
                except (ValueError, TypeError, OverflowError, OSError):
                    continue
                if record_date and record_date < cutoff:
                    stale_records += 1
        return stale_records

    with path.open("r+b") as source:
        while True:
            start = source.tell()
            line = source.readline()
            if not line:
                break
            try:
                record = json.loads(line)
                raw_date = record.get("created_at")
                if isinstance(raw_date, (int, float)):
                    stamp = raw_date / 1000 if raw_date > 10_000_000_000 else raw_date
                    record_date = datetime.fromtimestamp(stamp, tz=dt_timezone.utc)
                else:
                    record_date = parse_datetime(str(raw_date or ""))
                    if record_date and timezone.is_naive(record_date):
                        record_date = timezone.make_aware(record_date, timezone=dt_timezone.utc)
            except (ValueError, TypeError, OverflowError, OSError):
                continue
            if not record_date or record_date >= cutoff:
                continue
            stale_records += 1
            # Preserve byte offsets used by the collector's incremental ingest, including a
            # final line that was only partially flushed and has no newline yet.
            if delete:
                end = source.tell()
                source.seek(start)
                newline = line.endswith(b"\n")
                source.write((b"\n" if newline else b"") + b" " * (len(line) - int(newline)))
                source.seek(end)
    return stale_records


def purge_x_collector_state(state_path: str | Path, cutoff: datetime, *, delete: bool,
                            _retention_locked: bool = False) -> dict[str, int]:
    """Expire local X deduplication and query history, while retaining daily billing/cap totals."""
    import sqlite3
    from contextlib import closing

    path = Path(state_path)
    if not path.is_file():
        return {"x_collector_seen_keys": 0, "x_collector_query_history": 0, "x_collector_thread_state": 0}
    from django.conf import settings
    import os

    configured_root = Path(os.getenv("X_COLLECT_OUTPUT_DIR", "data/x_collected"))
    output_root = (configured_root if configured_root.is_absolute() else settings.BASE_DIR / configured_root).resolve()
    cutoff_iso = cutoff.astimezone(dt_timezone.utc).isoformat()
    report = {}
    guard = nullcontext() if _retention_locked else exclusive_file_lock(output_root / ".retention_guard.lock")
    with guard:
        with exclusive_file_lock(path.with_suffix(path.suffix + ".lock")):
            with closing(sqlite3.connect(path, timeout=30)) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if delete:
                    conn.execute("BEGIN IMMEDIATE")
                for table, column, key in (("seen_tweets", "first_seen", "x_collector_seen_keys"),
                                           ("query_runs", "last_run_at", "x_collector_query_history"),
                                           ("thread_fetches", "last_fetched_at", "x_collector_thread_state")):
                    if table not in tables:
                        report[key] = 0
                        continue
                    if table == "seen_tweets":
                        report[key] = int(conn.execute("SELECT COUNT(*) FROM seen_tweets WHERE first_seen < ?", (cutoff_iso,)).fetchone()[0])
                        delete_filter, delete_params = "first_seen < ?", (cutoff_iso,)
                    elif table == "query_runs":
                        # Scheduled queries that never ran still retain their query text.
                        report[key] = int(conn.execute("SELECT COUNT(*) FROM query_runs WHERE last_run_at IS NULL OR last_run_at < ?", (cutoff_iso,)).fetchone()[0])
                        delete_filter, delete_params = "last_run_at IS NULL OR last_run_at < ?", (cutoff_iso,)
                    else:
                        report[key] = int(conn.execute("SELECT COUNT(*) FROM thread_fetches WHERE last_fetched_at IS NOT NULL AND last_fetched_at < ?", (cutoff_iso,)).fetchone()[0])
                        delete_filter, delete_params = "last_fetched_at IS NOT NULL AND last_fetched_at < ?", (cutoff_iso,)
                    if delete:
                        conn.execute(f"DELETE FROM {table} WHERE {delete_filter}", delete_params)
                if delete:
                    conn.commit()
    return report


def _scrub_old_engine_needs(conn, engine_schema: str, cutoff: datetime) -> int:
    needs = f"{engine_schema}.needs"
    rows = conn.execute(
        f"SELECT need_id, payload FROM {needs} WHERE status <> 'retention_scrubbed' AND COALESCE(NULLIF(payload->>'evidence_posted_at', '')::timestamptz, NULLIF(payload->>'created_at', '')::timestamptz) < %s",
        (cutoff,),
    ).fetchall()
    for need_id, payload in rows:
        payload = payload if isinstance(payload, dict) else {}
        payload.update(need_id=need_id, chat_id="", author_id="", author_name=None, author_username=None,
                       author_bio=None, evidence_ids=[], evidence_posted_at=None, evidence_time_estimated=False,
                       situation=None, need=None, solution_queries=[], problem_queries=[], requirements=[],
                       constraints={}, label="", priority=0, emotion=None, status="expired", created_at=None,
                       updated_at=None, retention_scrubbed=True)
        conn.execute(
            f"UPDATE {needs} SET chat_id='', author_id='', status='retention_scrubbed', updated_at=0, payload=%s, summary_vec=NULL WHERE need_id=%s",
            (Jsonb(payload), need_id),
        )
        if _table_exists(conn, engine_schema, "emitted"):
            emitted = f"{engine_schema}.emitted"
            previous = conn.execute(f"SELECT payload FROM {emitted} WHERE opportunity_id=%s", (need_id,)).fetchone()
            if previous:
                original = previous[0] if isinstance(previous[0], dict) else {}
                clean = scrub_engine_opportunity_payload(original)
                encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True)
                fingerprint = hashlib.sha1(encoded.encode()).hexdigest()
                conn.execute(f"UPDATE {emitted} SET payload=%s, fingerprint=%s, ts=%s WHERE opportunity_id=%s",
                             (Jsonb(clean), fingerprint, datetime.now(dt_timezone.utc).timestamp(), need_id))
        if _table_exists(conn, engine_schema, "need_vectors"):
            conn.execute(f"DELETE FROM {engine_schema}.need_vectors WHERE need_id=%s", (need_id,))
        if _table_exists(conn, engine_schema, "deferred"):
            conn.execute(f"DELETE FROM {engine_schema}.deferred WHERE need_id=%s", (need_id,))
        # These auxiliary X rows can be refreshed after an old post is replayed; their own
        # timestamps are therefore not a safe proxy for the age of their source need.
        if _table_exists(conn, engine_schema, "x_hit_results"):
            conn.execute(f"DELETE FROM {engine_schema}.x_hit_results WHERE need_id=%s", (need_id,))
        if _table_exists(conn, engine_schema, "x_reply_fingerprints"):
            conn.execute(f"DELETE FROM {engine_schema}.x_reply_fingerprints WHERE need_id=%s", (need_id,))
        if _table_exists(conn, engine_schema, "x_thread_requests"):
            conn.execute(f"DELETE FROM {engine_schema}.x_thread_requests WHERE root_need_id=%s OR opportunity_id=%s",
                         (need_id, need_id))
    return len(rows)


def purge_retained_data(dsn: str, crawler_schema: str, engine_schema: str, cutoff: datetime, *, delete: bool) -> dict:
    """Count expired records and optionally purge them. Business opportunities and billing rows are never touched."""
    crawler_schema, engine_schema = _schema_name(crawler_schema), _schema_name(engine_schema)
    report: dict[str, int] = {}
    with psycopg.connect(dsn) as conn:
        with conn.transaction():
            _postgres_retention_lock(conn, crawler_schema)
            for state_table in ("pending", "recent"):
                if not _table_exists(conn, engine_schema, state_table):
                    continue
                table = f"{engine_schema}.{state_table}"
                old_payload = "COALESCE(NULLIF(payload->>'date', '')::timestamptz, NULLIF(payload->>'created_at', '')::timestamptz) < %s"
                key = "telegram_pending_messages" if state_table == "pending" else "telegram_recent_messages"
                report[key] = _count(conn, table, old_payload, (cutoff,))
                if delete:
                    conn.execute(f"DELETE FROM {table} WHERE {old_payload}", (cutoff,))
            for cache_name, expression in (("llm_cache", "created_at"), ("x_filter_decisions", "decided_at"),
                                           ("x_hit_results", "decided_at"), ("x_reply_fingerprints", "created_at"),
                                           ("x_thread_requests", "created_at")):
                if not _table_exists(conn, engine_schema, cache_name):
                    continue
                table = f"{engine_schema}.{cache_name}"
                key = f"engine_{cache_name}"
                report[key] = _count(conn, table, f"{expression} < %s", (cutoff,))
                if delete:
                    conn.execute(f"DELETE FROM {table} WHERE {expression} < %s", (cutoff,))
            if _table_exists(conn, crawler_schema, "tg_messages"):
                messages = f"{crawler_schema}.tg_messages"
                if delete:
                    conn.execute(f"CREATE TEMP TABLE retention_message_removals ON COMMIT DROP AS "
                                 f"SELECT m.id FROM {messages} m WHERE m.date < %s",
                                 (cutoff,))
                    report["telegram_messages"] = int(conn.execute(
                        "SELECT COUNT(*) FROM retention_message_removals"
                    ).fetchone()[0])
                else:
                    report["telegram_messages"] = _count(conn, messages, "date < %s", (cutoff,))
                report["telegram_context_messages"] = _count(
                    conn, messages, "is_context AND date < %s", (cutoff,)
                )
                report["telegram_messages_to_delete"] = report["telegram_messages"]
                if delete:
                    conn.execute(f"CREATE TEMP TABLE retention_message_removals ON COMMIT DROP AS "
                                 f"SELECT m.id FROM {messages} m WHERE m.date < %s",
                                 (cutoff,))
                if delete:
                    conn.execute(f"DELETE FROM {messages} m USING retention_message_removals r WHERE m.id=r.id")

                users = f"{crawler_schema}.tg_users"
                if _table_exists(conn, crawler_schema, "tg_users"):
                    user_filter = (
                        "first_seen_at < %s AND NOT EXISTS ("
                        f"SELECT 1 FROM {messages} m WHERE m.sender_user_id = tg_users.user_id "
                        "AND NOT EXISTS (SELECT 1 FROM retention_message_removals r WHERE r.id=m.id))"
                    )
                    report["telegram_orphan_users"] = _count(conn, users, user_filter, (cutoff,))
                    if delete:
                        conn.execute(f"DELETE FROM {users} WHERE {user_filter}", (cutoff,))

                chats = f"{crawler_schema}.tg_chats"
                if _table_exists(conn, crawler_schema, "tg_chats"):
                    chat_filter = (
                        "updated_at < %s AND NOT is_monitored AND NOT EXISTS ("
                        f"SELECT 1 FROM {messages} m WHERE m.chat_id = tg_chats.chat_id "
                        "AND NOT EXISTS (SELECT 1 FROM retention_message_removals r WHERE r.id=m.id)) "
                        "AND NOT EXISTS (SELECT 1 FROM " + messages + " m WHERE m.sender_chat_id=tg_chats.chat_id "
                        "AND NOT EXISTS (SELECT 1 FROM retention_message_removals r WHERE r.id=m.id))"
                    )
                    report["telegram_orphan_chats"] = _count(conn, chats, chat_filter, (cutoff,))
                    if delete:
                        conn.execute(f"DELETE FROM {chats} c WHERE c.updated_at < %s AND NOT c.is_monitored "
                                     f"AND NOT EXISTS (SELECT 1 FROM {messages} m WHERE m.chat_id=c.chat_id OR m.sender_chat_id=c.chat_id)",
                                     (cutoff,))

            # Product-scoped X hits use chat_id=x:p:<product_id>; source tweet ids let us
            # remove stale decisions even if a replay refreshed their own timestamps.
            stale_x_needs = []
            if delete and _table_exists(conn, engine_schema, "needs") and _table_exists(conn, engine_schema, "emitted"):
                stale_x_needs = conn.execute(
                    f"SELECT need_id,payload FROM {engine_schema}.needs "
                    "WHERE chat_id LIKE 'x:%' AND status <> 'retention_scrubbed' "
                    "AND COALESCE(NULLIF(payload->>'evidence_posted_at','')::timestamptz,"
                    "NULLIF(payload->>'created_at','')::timestamptz) < %s", (cutoff,)
                ).fetchall()
                report["engine_x_needs"] = len(stale_x_needs)
            if _table_exists(conn, crawler_schema, "x_post_products") and _table_exists(conn, crawler_schema, "x_posts"):
                links = f"{crawler_schema}.x_post_products"
                posts = f"{crawler_schema}.x_posts"
                for table_name in ("x_filter_decisions", "x_hit_results"):
                    if not _table_exists(conn, engine_schema, table_name):
                        continue
                    decisions = f"{engine_schema}.{table_name}"
                    stale_product_decision = (
                        f"chat_id LIKE 'x:p:%' AND EXISTS (SELECT 1 FROM {links} h JOIN {posts} x USING (tweet_id) "
                        f"WHERE h.product_id=substring({decisions}.chat_id from 5) "
                        f"AND x.tweet_id={decisions}.message_id AND COALESCE(x.created_at,x.loaded_at) < %s)"
                    )
                    report[f"engine_{table_name}_for_expired_product_posts"] = _count(
                        conn, decisions, stale_product_decision, (cutoff,)
                    )
                    if delete:
                        conn.execute(
                            f"DELETE FROM {decisions} d USING {links} h, {posts} x "
                            "WHERE d.chat_id LIKE 'x:p:%' AND h.product_id=substring(d.chat_id from 5) "
                            "AND h.tweet_id=x.tweet_id AND d.message_id=x.tweet_id "
                            "AND COALESCE(x.created_at,x.loaded_at) < %s", (cutoff,)
                        )

            if _table_exists(conn, crawler_schema, "x_posts"):
                posts = f"{crawler_schema}.x_posts"
                stale_posts = "COALESCE(created_at, loaded_at) < %s"
                report["x_posts"] = _count(conn, posts, stale_posts, (cutoff,))
                for table_name in ("x_filter_decisions", "x_hit_results"):
                    if not _table_exists(conn, engine_schema, table_name):
                        continue
                    decisions = f"{engine_schema}.{table_name}"
                    public_filter = (
                        f"chat_id='x:public' AND EXISTS (SELECT 1 FROM {posts} x "
                        f"WHERE x.tweet_id={decisions}.message_id AND COALESCE(x.created_at,x.loaded_at) < %s)"
                    )
                    report[f"engine_{table_name}_for_expired_public_posts"] = _count(
                        conn, decisions, public_filter, (cutoff,)
                    )
                    if delete:
                        conn.execute(
                            f"DELETE FROM {decisions} d USING {posts} x "
                            "WHERE d.chat_id='x:public' AND d.message_id=x.tweet_id "
                            "AND COALESCE(x.created_at,x.loaded_at) < %s", (cutoff,)
                        )
                if delete:
                    if _table_exists(conn, engine_schema, "x_thread_requests"):
                        conn.execute(
                            f"DELETE FROM {engine_schema}.x_thread_requests r USING {posts} x "
                            f"WHERE r.root_tweet_id=x.tweet_id AND {stale_posts.replace('created_at', 'x.created_at').replace('loaded_at', 'x.loaded_at')}",
                            (cutoff,),
                        )
                    if _table_exists(conn, crawler_schema, "x_post_products"):
                        conn.execute(
                            f"DELETE FROM {crawler_schema}.x_post_products p USING {posts} x "
                            f"WHERE p.tweet_id=x.tweet_id AND {stale_posts.replace('created_at', 'x.created_at').replace('loaded_at', 'x.loaded_at')}",
                            (cutoff,),
                        )
                    conn.execute(f"DELETE FROM {posts} WHERE {stale_posts}", (cutoff,))
            if delete and _table_exists(conn, engine_schema, "needs") and _table_exists(conn, engine_schema, "emitted"):
                for need_id, payload in stale_x_needs:
                    clean = None
                    payload = payload if isinstance(payload, dict) else {}
                    payload.update(need_id=need_id, chat_id="", author_id="", author_name=None,
                                   author_username=None, author_bio=None, evidence_ids=[],
                                   evidence_posted_at=None, evidence_time_estimated=False, situation=None,
                                   need=None, solution_queries=[], problem_queries=[], requirements=[],
                                   constraints={}, label="", priority=0, emotion=None, status="expired",
                                   created_at=None, updated_at=None, retention_scrubbed=True)
                    conn.execute(f"UPDATE {engine_schema}.needs SET chat_id='',author_id='',status='retention_scrubbed',"
                                 f"updated_at=0,payload=%s,summary_vec=NULL WHERE need_id=%s", (Jsonb(payload), need_id))
                    original_row = conn.execute(f"SELECT payload FROM {engine_schema}.emitted WHERE opportunity_id=%s",
                                                (need_id,)).fetchone()
                    if original_row:
                        clean = scrub_engine_opportunity_payload(original_row[0] if isinstance(original_row[0], dict) else {})
                        clean["opportunity_id"] = need_id
                        encoded = json.dumps(clean, ensure_ascii=False, sort_keys=True)
                        conn.execute(f"UPDATE {engine_schema}.emitted SET payload=%s,fingerprint=%s,ts=%s WHERE opportunity_id=%s",
                                     (Jsonb(clean), hashlib.sha1(encoded.encode()).hexdigest(),
                                      datetime.now(dt_timezone.utc).timestamp(), need_id))
                    if _table_exists(conn, engine_schema, "opportunities"):
                        if original_row and conn.execute(
                            f"SELECT 1 FROM {engine_schema}.opportunities WHERE opportunity_id=%s", (need_id,)
                        ).fetchone():
                            conn.execute(f"UPDATE {engine_schema}.opportunities SET seq=nextval('{engine_schema}.opportunities_seq'),"
                                         f"status='expired',payload=%s,updated_at=now() WHERE opportunity_id=%s",
                                         (Jsonb(clean), need_id))
                    if _table_exists(conn, engine_schema, "need_vectors"):
                        conn.execute(f"DELETE FROM {engine_schema}.need_vectors WHERE need_id=%s", (need_id,))
                    if _table_exists(conn, engine_schema, "deferred"):
                        conn.execute(f"DELETE FROM {engine_schema}.deferred WHERE need_id=%s", (need_id,))

            if _table_exists(conn, engine_schema, "needs"):
                needs = f"{engine_schema}.needs"
                need_age = "(status <> 'retention_scrubbed' AND COALESCE(NULLIF(payload->>'evidence_posted_at', '')::timestamptz, NULLIF(payload->>'created_at', '')::timestamptz) < %s)"
                report["engine_needs"] = _count(conn, needs, need_age, (cutoff,))
                if delete:
                    report["engine_needs_scrubbed"] = _scrub_old_engine_needs(conn, engine_schema, cutoff)

            for table_name in ("emitted", "opportunities"):
                if not _table_exists(conn, engine_schema, table_name):
                    continue
                table = f"{engine_schema}.{table_name}"
                age_filter = "(payload->>'retention_scrubbed' IS DISTINCT FROM 'true' AND COALESCE(NULLIF(payload->>'source_posted_at', '')::timestamptz, NULLIF(payload->>'created_at', '')::timestamptz) < %s)"
                age_value = (cutoff,)
                rows = conn.execute(f"SELECT opportunity_id, payload FROM {table} WHERE {age_filter}", age_value).fetchall()
                changed_rows = []
                for opportunity_id, original in rows:
                    original = original if isinstance(original, dict) else {}
                    if table_name == "opportunities" and _table_exists(conn, engine_schema, "needs"):
                        need_row = conn.execute(
                            f"SELECT payload FROM {engine_schema}.needs WHERE need_id=%s AND status='retention_scrubbed'",
                            (opportunity_id,),
                        ).fetchone()
                        if need_row:
                            clean = scrub_engine_opportunity_payload(original)
                            # An explicit source-date tombstone should supersede the retention
                            # operation's earlier bulk row update, even after partial retries.
                        else:
                            continue
                    elif table_name == "emitted" and _table_exists(conn, engine_schema, "needs"):
                        need_row = conn.execute(
                            f"SELECT payload FROM {engine_schema}.needs WHERE need_id=%s AND status='retention_scrubbed'",
                            (opportunity_id,),
                        ).fetchone()
                        if not need_row:
                            continue
                        clean = scrub_engine_opportunity_payload(original)
                    else:
                        clean = scrub_engine_opportunity_payload(original)
                    clean["retention_scrubbed"] = True
                    clean["opportunity_id"] = opportunity_id
                    if clean != original:
                        changed_rows.append((opportunity_id, clean))
                report[f"engine_{table_name}_opportunities_scrubbed"] = len(changed_rows)
                if delete:
                    for opportunity_id, clean in changed_rows:
                        if table_name == "opportunities":
                            encoded = json.dumps(clean, sort_keys=True, ensure_ascii=False)
                            conn.execute(f"UPDATE {table} SET seq=nextval('{engine_schema}.opportunities_seq'), updated_at=now(), payload=%s, status=%s WHERE opportunity_id=%s",
                                         (Jsonb(clean), clean.get("status"), opportunity_id))
                            if _table_exists(conn, engine_schema, "emitted"):
                                conn.execute(f"INSERT INTO {engine_schema}.emitted (opportunity_id,fingerprint,ts,payload) VALUES (%s,%s,%s,%s) "
                                             "ON CONFLICT (opportunity_id) DO UPDATE SET fingerprint=EXCLUDED.fingerprint,ts=EXCLUDED.ts,payload=EXCLUDED.payload",
                                             (opportunity_id, hashlib.sha1(encoded.encode()).hexdigest(),
                                              datetime.now(dt_timezone.utc).timestamp(), Jsonb(clean)))
                        else:
                            encoded = json.dumps(clean, sort_keys=True, ensure_ascii=False)
                            fp = hashlib.sha1(encoded.encode()).hexdigest()
                            conn.execute(f"UPDATE {table} SET payload=%s, fingerprint=%s, ts=%s WHERE opportunity_id=%s",
                                         (Jsonb(clean), fp, datetime.now(dt_timezone.utc).timestamp(), opportunity_id))
            if (_table_exists(conn, engine_schema, "opportunities")
                    and _table_exists(conn, engine_schema, "emitted")):
                pending_tombstones = []
                if _table_exists(conn, engine_schema, "needs"):
                    pending_tombstones = conn.execute(
                        f"SELECT n.need_id FROM {engine_schema}.needs n WHERE n.status='retention_scrubbed' "
                        f"AND NOT EXISTS (SELECT 1 FROM {engine_schema}.opportunities o WHERE o.opportunity_id=n.need_id "
                        "AND o.payload->>'retention_scrubbed'='true')"
                    ).fetchall()
                if delete:
                    already_scrubbed = conn.execute(
                        f"SELECT e.opportunity_id FROM {engine_schema}.emitted e "
                        "WHERE e.payload->>'retention_scrubbed'='true' "
                        f"AND NOT EXISTS (SELECT 1 FROM {engine_schema}.opportunities o "
                        "WHERE o.opportunity_id=e.opportunity_id AND o.payload->>'retention_scrubbed'='true')"
                    ).fetchall()
                    pending_tombstones.extend(already_scrubbed)
                    for (need_id,) in pending_tombstones:
                        emitted_row = conn.execute(f"SELECT payload FROM {engine_schema}.emitted WHERE opportunity_id=%s", (need_id,)).fetchone()
                        if not emitted_row:
                            continue
                        original = emitted_row[0] if isinstance(emitted_row[0], dict) else {}
                        clean = scrub_engine_opportunity_payload(original)
                        clean["opportunity_id"] = need_id
                        clean["retention_scrubbed"] = True
                        encoded = json.dumps(clean, sort_keys=True, ensure_ascii=False)
                        conn.execute(
                            f"INSERT INTO {engine_schema}.opportunities(opportunity_id,seq,status,payload) "
                            f"VALUES (%s,nextval('{engine_schema}.opportunities_seq'),%s,%s) "
                            "ON CONFLICT (opportunity_id) DO UPDATE SET seq=EXCLUDED.seq,status=EXCLUDED.status, "
                            "payload=EXCLUDED.payload,updated_at=now()",
                            (need_id, clean.get("status"), Jsonb(clean)),
                        )
                        conn.execute(f"UPDATE {engine_schema}.emitted SET payload=%s,fingerprint=%s,ts=%s WHERE opportunity_id=%s",
                                     (Jsonb(clean), hashlib.sha1(encoded.encode()).hexdigest(),
                                      datetime.now(dt_timezone.utc).timestamp(), need_id))
            if delete and _table_exists(conn, engine_schema, "needs"):
                needs = f"{engine_schema}.needs"
                tombstone_filter = "status='retention_scrubbed'"
                report["engine_needs_already_scrubbed"] = _count(conn, needs, tombstone_filter, ())

            if _table_exists(conn, crawler_schema, "x_search_runs"):
                search_runs = f"{crawler_schema}.x_search_runs"
                report["x_search_history"] = _count(conn, search_runs, "ran_at < %s", (cutoff,))
                if delete:
                    conn.execute(f"DELETE FROM {search_runs} WHERE ran_at < %s", (cutoff,))

            if _table_exists(conn, engine_schema, "costs"):
                costs = f"{engine_schema}.costs"
                report["engine_cost_references"] = _count(conn, costs, "ts < %s AND ref <> ''", (cutoff.timestamp(),))
                if delete:
                    conn.execute(f"UPDATE {costs} SET ref='' WHERE ts < %s AND ref <> ''", (cutoff.timestamp(),))
    return report



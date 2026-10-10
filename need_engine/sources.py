"""Read-only sources: messages from the crawler archive, products from Django's table (same PostgreSQL database),
or JSONL files for tests and the offline demo. Connections are READ ONLY; nothing here ever writes."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Protocol

from need_engine.config import EngineConfig
from need_engine.schemas import ChatMessage, Product


def parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        dt = v
    else:
        s = str(v).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── read-only PostgreSQL access ──────────────────────────────────────────────
class _DB:
    """Read-only connection: every transaction is READ ONLY, so the engine cannot modify crawler/Django tables."""

    def __init__(self, dsn: str):
        import psycopg
        from psycopg.rows import dict_row

        self.dsn = dsn
        self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
        self.conn.execute("SET default_transaction_read_only = on")

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        import psycopg

        try:
            return self.conn.execute(sql, params).fetchall()
        except psycopg.errors.UndefinedTable:   # crawler / panel not migrated yet → nothing to read
            return []
        except psycopg.OperationalError:        # database restarted → reconnect once
            self.__init__(self.dsn)
            return self.conn.execute(sql, params).fetchall()

    def close(self) -> None:
        self.conn.close()


def _ident(name: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)?", name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return name


# ── messages ─────────────────────────────────────────────────────────────────
class MessageSource(Protocol):
    def fetch_after(self, cursor: int, limit: int) -> list[ChatMessage]: ...


class SQLMessageSource:
    """Reads the crawler archive (``crawler.tg_messages`` + users + chats) incrementally by its row id.

    Service messages and parents fetched only as context are skipped as input; a reply's parent text is
    joined in so the model sees what the message answers even when the parent is old.
    """

    def __init__(self, cfg: EngineConfig, dsn: str | None = None, conn: Any = None):
        self.cfg = cfg
        self.db = _DB(dsn or cfg.database_url) if conn is None else conn
        s = _ident(cfg.crawler_schema)
        self.sql = f"""
            SELECT m.id AS row_id, m.chat_id, m.message_id, m.date, m.text, m.reply_to_msg_id,
                   COALESCE(m.sender_user_id, m.sender_chat_id) AS author_id,
                   COALESCE(NULLIF(concat_ws(' ', u.first_name, u.last_name), ''), sc.title, u.username) AS author_name,
                   COALESCE(u.username, sc.username) AS author_username, COALESCE(u.is_bot, FALSE) AS is_bot,
                   c.title AS chat_title, c.username AS chat_username,
                   p.text AS parent_text, p.date AS parent_date, COALESCE(p.sender_user_id, p.sender_chat_id) AS parent_author_id,
                   COALESCE(NULLIF(concat_ws(' ', pu.first_name, pu.last_name), ''), pu.username) AS parent_author_name
            FROM {s}.tg_messages m
            JOIN {s}.tg_chats c ON c.chat_id = m.chat_id
            LEFT JOIN {s}.tg_users u ON u.user_id = m.sender_user_id
            LEFT JOIN {s}.tg_chats sc ON sc.chat_id = m.sender_chat_id
            LEFT JOIN {s}.tg_messages p ON p.chat_id = m.chat_id AND p.message_id = m.reply_to_msg_id
            LEFT JOIN {s}.tg_users pu ON pu.user_id = p.sender_user_id
            WHERE m.id > %s AND NOT m.is_context AND NOT m.is_service
            ORDER BY m.id LIMIT %s"""

    def fetch_after(self, cursor: int, limit: int) -> list[ChatMessage]:
        rows = self.db.all(self.sql, (cursor, limit))
        return [ChatMessage(
            chat_id=str(r["chat_id"]), message_id=int(r["message_id"]), row_id=int(r["row_id"]),
            author_id=str(r["author_id"] or ""), author_name=r.get("author_name"), author_username=r.get("author_username"),
            is_bot=bool(r.get("is_bot")), text=r.get("text") or "", date=parse_dt(r["date"]),
            reply_to=int(r["reply_to_msg_id"]) if r.get("reply_to_msg_id") is not None else None,
            chat_title=r.get("chat_title"), chat_username=r.get("chat_username"),
            reply_to_text=r.get("parent_text"), reply_to_author_id=str(r["parent_author_id"]) if r.get("parent_author_id") else None,
            reply_to_author_name=r.get("parent_author_name"),
            reply_to_date=parse_dt(r["parent_date"]) if r.get("parent_date") else None) for r in rows]


class XMessageSource:
    """Read X posts from the collector-owned crawler table, ordered by its independent row id."""

    def __init__(self, cfg: EngineConfig, dsn: str | None = None, conn: Any = None):
        self.cfg = cfg
        self.db = _DB(dsn or cfg.database_url) if conn is None else conn
        schema = _ident(cfg.crawler_schema)
        self.sql = f"""SELECT x.row_id, x.tweet_id, x.author_id, x.author_handle, x.author_name, x.text, x.created_at, x.loaded_at, x.url, x.query, x.lang, x.author_verified, x.author_bio,
                                x.kind, x.conversation_id, x.in_reply_to_tweet_id, x.in_reply_to_author_id, p.text AS parent_text, p.created_at AS parent_date, p.author_id AS parent_author_id
                         FROM {schema}.x_posts x LEFT JOIN {schema}.x_posts p ON p.tweet_id=x.in_reply_to_tweet_id
                         WHERE x.row_id > %s AND x.author_id IS NOT NULL AND x.author_id <> ''
                         ORDER BY x.row_id LIMIT %s"""

    def fetch_after(self, cursor: int, limit: int) -> list[ChatMessage]:
        rows = self.db.all(self.sql, (cursor, limit))
        messages = []
        for row in rows:
            handle = str(row.get("author_handle") or "").lstrip("@") or None
            messages.append(ChatMessage(
                chat_id="x:public", message_id=int(row["tweet_id"]), row_id=int(row["row_id"]),
                author_id=f"x_{row['author_id']}",
                author_name=row.get("author_name"), author_username=handle, text=row.get("text") or "",
                date=parse_dt(row.get("created_at") or row.get("loaded_at") or datetime.now(timezone.utc)),
                chat_title="X", platform="x", url=row.get("url"),
                profile_url=f"https://x.com/{handle}" if handle else None, search_query=row.get("query"),
                author_verified=bool(row.get("author_verified")), author_bio=row.get("author_bio"), lang=row.get("lang"),
                date_estimated=not bool(row.get("created_at")), kind=row.get("kind") or "post",
                conversation_id=row.get("conversation_id"), reply_to=row.get("in_reply_to_tweet_id"),
                in_reply_to_author_id=row.get("in_reply_to_author_id"), reply_to_text=row.get("parent_text"),
                reply_to_author_id=str(row.get("parent_author_id") or row.get("in_reply_to_author_id") or "") or None,
                reply_to_date=parse_dt(row["parent_date"]) if row.get("parent_date") else None))
        return messages


class JsonlMessageSource:
    """Test/demo source: the labelled chats.jsonl format (one chat per line) or flat message lines."""

    def __init__(self, path: str):
        self.msgs: list[ChatMessage] = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            o = json.loads(line)
            if "messages" in o:
                for m in o["messages"]:
                    self.msgs.append(self._msg(o["chat_id"], o.get("group_title"), m))
            else:
                self.msgs.append(self._msg(o.get("chat_id") or o.get("channel_id"), o.get("channel_title"), o))
        self.msgs.sort(key=lambda m: (m.date, m.chat_id, m.message_id))
        for i, m in enumerate(self.msgs, 1):
            m.row_id = i

    @staticmethod
    def _msg(chat_id: Any, title: str | None, m: dict) -> ChatMessage:
        mid = m.get("message_id", m.get("id"))
        if isinstance(mid, str) and ":" in mid:
            mid = mid.split(":")[-1]
        return ChatMessage(chat_id=str(chat_id), message_id=int(mid), author_id=str(m.get("author_id") or ""),
                           author_name=m.get("author_name"), text=m.get("text") or "",
                           date=parse_dt(m.get("date") or m.get("created_at")), reply_to=m.get("reply_to"), chat_title=title)

    def fetch_after(self, cursor: int, limit: int) -> list[ChatMessage]:
        return [m for m in self.msgs if m.row_id > cursor][:limit]


def message_source(cfg: EngineConfig) -> MessageSource:
    if cfg.messages_source.startswith("jsonl:"):
        return JsonlMessageSource(cfg.messages_source[6:])
    if cfg.messages_source != "db":
        raise ValueError(f"unsupported NE_MESSAGES_SOURCE: {cfg.messages_source}")
    if not cfg.x_enabled:
        return SQLMessageSource(cfg)
    return SQLMessageSource(cfg)


# ── products ─────────────────────────────────────────────────────────────────
class ProductSource(Protocol):
    def all(self) -> list[Product]: ...


def _price(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


class JsonlProductSource:
    def __init__(self, path: str):
        self.path = path

    def all(self) -> list[Product]:
        out = []
        for line in Path(self.path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            o = json.loads(line)
            out.append(Product(
                product_id=str(o["product_id"]), business_id=str(o.get("seller_id") or o.get("business_id") or "") or None,
                title=o.get("title") or o.get("name") or "", description=o.get("description") or "",
                product_type=o.get("product_type") or "", price_toman=_price(o.get("price_toman", o.get("price"))),
                city=o.get("city"), ships_nationwide=bool(o.get("ships_nationwide", True)),
                attributes=o.get("attributes") if isinstance(o.get("attributes"), dict) else {},
                tags=o.get("tags") if isinstance(o.get("tags"), list) else [], category_path=str(o.get("category_path") or ""),
                category_keywords=o.get("category_keywords") if isinstance(o.get("category_keywords"), list) else [],
                discovery_priority=int(o.get("discovery_priority") or 1),
                x_outreach_enabled=bool(o.get("x_outreach_enabled", True)), url=o.get("url") or None,
                card_override=_json_dict(o.get("card_override"))))
        return out


def _json_dict(v: Any) -> dict | None:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            return None
    return v if isinstance(v, dict) and v else None


class SQLProductSource:
    """Read-only view of Django's ``products_product`` (active, discovery-enabled products)."""

    def __init__(self, cfg: EngineConfig, dsn: str | None = None):
        self.cfg, self.db = cfg, _DB(dsn or cfg.database_url)

    def all(self) -> list[Product]:
        # x_outreach_enabled is read, not filtered: it only limits X opportunities (engine._x_allowed), never Telegram
        rows = self.db.all(f"""SELECT p.id, p.business_id, p.name, p.description, p.product_type, p.price, p.attributes, p.target_customer,
                                   p.url, p.agent_card_override, p.discovery_priority, p.x_outreach_enabled,
                                   COALESCE(c.full_path, c.name, '') AS category_path, COALESCE(c.keywords, '[]'::jsonb) AS category_keywords
                               FROM {_ident(self.cfg.products_table)} p
                               LEFT JOIN public.products_category c ON c.id = p.category_id
                               WHERE p.status = 'ACTIVE' AND p.is_discovery_active = %s""", (True,))
        out = []
        for r in rows:
            attrs = r.get("attributes")
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except Exception:
                    attrs = {}
            desc = " ".join(x for x in [r.get("description") or "", r.get("target_customer") or ""] if x)
            keywords = r.get("category_keywords") or []
            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except json.JSONDecodeError:
                    keywords = []
            out.append(Product(product_id=str(r["id"]), business_id=str(r.get("business_id") or "") or None, title=r.get("name") or "",
                               description=desc, product_type=str(r.get("product_type") or ""), price_toman=_price(r.get("price")),
                               attributes=attrs if isinstance(attrs, dict) else {}, category_path=str(r.get("category_path") or ""),
                               category_keywords=keywords if isinstance(keywords, list) else [],
                               discovery_priority=int(r.get("discovery_priority") or 1),
                               x_outreach_enabled=r.get("x_outreach_enabled") is not False, url=(r.get("url") or None),
                               card_override=_json_dict(r.get("agent_card_override"))))
        return out


class StyleSource:
    """Sellers' reply styles from Django's ``businesses_messagestyle`` (read-only). {} when not available."""

    def __init__(self, cfg: EngineConfig, db: Any = None):
        self.cfg, self._db = cfg, db

    def all(self) -> dict[str, dict]:
        if self.cfg.products_source != "db" and self._db is None:
            return {}
        if self._db is None:
            self._db = _DB(self.cfg.database_url)
        rows = self._db.all(f"""SELECT business_id, tone, max_sentences, use_emoji, signature, include_link, extra_instructions
                                FROM {_ident(self.cfg.styles_table)}""")
        return {str(r["business_id"]): dict(r) for r in rows}


def product_source(cfg: EngineConfig) -> ProductSource:
    s = cfg.products_source
    if s.startswith("jsonl:"):
        return JsonlProductSource(s[6:])
    if s == "db":
        return SQLProductSource(cfg)
    raise ValueError(f"unsupported NE_PRODUCTS_SOURCE: {s}")


def iter_chunks(items: list, n: int) -> Iterator[list]:
    for i in range(0, len(items), n):
        yield items[i:i + n]

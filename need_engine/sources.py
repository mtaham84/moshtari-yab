"""Read-only sources. Messages come from the crawler's table (SQLite now, PostgreSQL later); products from
JSONL or Django's products table. Nothing here ever writes to those databases."""
from __future__ import annotations

import json
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


# ── DB-API helpers (sqlite3 / psycopg) ─────────────────────────────────────────
class _DB:
    def __init__(self, dsn: str):
        self.dsn = dsn
        if dsn.startswith("sqlite:///") or dsn.startswith("sqlite://"):
            import sqlite3

            path = dsn.split("sqlite:///", 1)[-1] if "sqlite:///" in dsn else dsn.split("sqlite://", 1)[-1]
            self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False) if path != ":memory:" else sqlite3.connect(":memory:")
            self.conn.row_factory = sqlite3.Row
            self.ph = "?"
        elif dsn.startswith("postgres"):
            import psycopg
            from psycopg.rows import dict_row

            self.conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=True)
            try:
                self.conn.execute("SET default_transaction_read_only = on")
            except Exception:  # pragma: no cover
                pass
            self.ph = "%s"
        else:
            raise ValueError(f"unsupported DSN: {dsn}")

    def all(self, sql: str, params: tuple = ()) -> list[dict]:
        sql = sql.replace("?", self.ph)
        cur = self.conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


# ── messages ─────────────────────────────────────────────────────────────────
class MessageSource(Protocol):
    def fetch_after(self, cursor: int, limit: int) -> list[ChatMessage]: ...


class SQLMessageSource:
    """Reads ``messages`` incrementally by its monotonic row id (arrival order)."""

    def __init__(self, cfg: EngineConfig, dsn: str | None = None, conn: Any = None):
        self.cfg = cfg
        self.db = _DB(dsn or cfg.messages_dsn) if conn is None else conn
        self.c = cfg.messages_columns
        self._groups: dict[str, dict] = {}

    def _load_groups(self) -> None:
        try:
            rows = self.db.all(f"SELECT group_id, title, link FROM {self.cfg.groups_table}")
        except Exception:
            return
        for r in rows:
            link = r.get("link") or ""
            username = None
            if link and "/+" not in link and "joinchat" not in link:
                username = link.rstrip("/").split("/")[-1].lstrip("@") or None
            self._groups[str(r["group_id"])] = {"title": r.get("title"), "username": username}

    def fetch_after(self, cursor: int, limit: int) -> list[ChatMessage]:
        c = self.c
        cols = ", ".join(f"{v} AS {k}" for k, v in c.items())
        rows = self.db.all(f"SELECT {cols} FROM {self.cfg.messages_table} WHERE {c['row_id']} > ? ORDER BY {c['row_id']} LIMIT ?",
                           (cursor, limit))
        if rows:
            self._load_groups()
        out = []
        for r in rows:
            g = self._groups.get(str(r["chat_id"]), {})
            out.append(ChatMessage(
                chat_id=str(r["chat_id"]), message_id=int(r["message_id"]), row_id=int(r["row_id"]),
                author_id=str(r["author_id"] or ""), author_name=r.get("author_name"), author_username=r.get("author_username"),
                is_bot=bool(r.get("is_bot")), text=r.get("text") or "", date=parse_dt(r["date"]),
                reply_to=int(r["reply_to"]) if r.get("reply_to") not in (None, "") else None,
                chat_title=g.get("title"), chat_username=g.get("username")))
        return out


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
    if cfg.messages_dsn.startswith("jsonl:"):
        return JsonlMessageSource(cfg.messages_dsn[6:])
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
                tags=o.get("tags") if isinstance(o.get("tags"), list) else []))
        return out


class SQLProductSource:
    """Read-only view of Django's ``products_product`` (active, discovery-enabled products)."""

    def __init__(self, cfg: EngineConfig, dsn: str):
        self.cfg, self.db = cfg, _DB(dsn)

    def all(self) -> list[Product]:
        rows = self.db.all(f"""SELECT id, business_id, name, description, product_type, price, attributes, target_customer
                               FROM {self.cfg.products_table} WHERE status = 'ACTIVE' AND is_discovery_active = ?""", (True,))
        out = []
        for r in rows:
            attrs = r.get("attributes")
            if isinstance(attrs, str):
                try:
                    attrs = json.loads(attrs)
                except Exception:
                    attrs = {}
            desc = " ".join(x for x in [r.get("description") or "", r.get("target_customer") or ""] if x)
            out.append(Product(product_id=str(r["id"]), business_id=str(r.get("business_id") or "") or None, title=r.get("name") or "",
                               description=desc, product_type=str(r.get("product_type") or ""), price_toman=_price(r.get("price")),
                               attributes=attrs if isinstance(attrs, dict) else {}))
        return out


def product_source(cfg: EngineConfig) -> ProductSource:
    s = cfg.products_source
    if s.startswith("jsonl:"):
        return JsonlProductSource(s[6:])
    if s.startswith("sql:"):
        return SQLProductSource(cfg, s[4:])
    raise ValueError(f"unsupported NE_PRODUCTS_SOURCE: {s}")


def iter_chunks(items: list, n: int) -> Iterator[list]:
    for i in range(0, len(items), n):
        yield items[i:i + n]

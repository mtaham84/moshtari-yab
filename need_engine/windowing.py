"""When to analyse a chat, and how a batch of new messages is shown to the model."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from need_engine.config import EngineConfig
from need_engine.schemas import ChatMessage
from need_engine.store import Store
from need_engine.text import is_noise

try:
    from zoneinfo import ZoneInfo

    _TZ = ZoneInfo("Asia/Tehran")
except Exception:  # pragma: no cover
    _TZ = None


@dataclass
class Window:
    chat_id: str
    chat_title: str | None
    new: list[ChatMessage]                       # messages to analyse (noise removed)
    context: list[ChatMessage] = field(default_factory=list)   # already analysed, for understanding only
    parents: list[ChatMessage] = field(default_factory=list)   # older messages that new ones reply to
    consumed: list[ChatMessage] = field(default_factory=list)  # all pending messages this window marks as analysed

    @property
    def new_ids(self) -> set[int]:
        return {m.message_id for m in self.new}

    @property
    def all_ids(self) -> set[int]:
        return self.new_ids | {m.message_id for m in self.context} | {m.message_id for m in self.parents}


def ready_batch(pending: list[ChatMessage], now: datetime, cfg: EngineConfig) -> tuple[list[ChatMessage], str]:
    """Messages of a chat to analyse now: whole batches of ``trigger_count`` (oldest first); the rest keeps waiting.

    Purely count based — no waiting for the conversation to go quiet. Optional fallback: when
    ``max_wait_minutes`` > 0 and the oldest pending message waited that long, everything pending is analysed.
    """
    if not pending:
        return [], "empty"
    pending = sorted(pending, key=lambda m: m.message_id)
    step = max(int(cfg.trigger_count), 1)
    full = len(pending) // step * step
    if full:
        return pending[:full], f"{full} new messages"
    if cfg.max_wait_minutes and cfg.max_wait_minutes > 0:
        waited = (now - min(m.date for m in pending)).total_seconds() / 60
        if waited >= cfg.max_wait_minutes:
            return pending, f"oldest waited {waited:.0f} min"
    return [], f"waiting ({len(pending)}/{step})"


def build_windows(chat_id: str, pending: list[ChatMessage], store: Store, cfg: EngineConfig) -> list[Window]:
    pending = sorted(pending, key=lambda m: m.message_id)
    useful = [m for m in pending if not m.is_bot and not is_noise(m.text, cfg.min_text_chars)]
    title = next((m.chat_title for m in pending if m.chat_title), None)
    if not useful:
        return [Window(chat_id, title, [], consumed=pending)]
    windows: list[Window] = []
    is_x = any(m.platform == "x" for m in pending)
    prev: list[ChatMessage] = [] if is_x else store.recent(chat_id, useful[0].message_id, cfg.context_messages)
    chunks = [useful[i:i + cfg.window_size] for i in range(0, len(useful), cfg.window_size)]
    bounds = [c[-1].message_id for c in chunks]
    for k, chunk in enumerate(chunks):
        lo = chunks[k - 1][-1].message_id if k else -1
        consumed = [m for m in pending if lo < m.message_id <= bounds[k]] if k < len(chunks) - 1 else [m for m in pending if m.message_id > lo]
        context = prev[-cfg.context_messages:] if cfg.context_messages and not is_x else []
        seen = {m.message_id for m in chunk} | {m.message_id for m in context}
        parents = []
        for m in chunk:
            if not is_x and m.reply_to and m.reply_to not in seen:
                p = store.message(chat_id, m.reply_to)
                if p is None and m.reply_to_text:   # parent older than the engine's state: use the archived copy
                    p = ChatMessage(chat_id=chat_id, message_id=m.reply_to, author_id=m.reply_to_author_id or "",
                                    author_name=m.reply_to_author_name, text=m.reply_to_text, date=m.reply_to_date or m.date)
                if p and p.text.strip():
                    parents.append(p)
                    seen.add(p.message_id)
        windows.append(Window(chat_id, title, chunk, context, parents[-5:], consumed))
        prev = (prev + chunk)[-cfg.context_messages:] if cfg.context_messages else []
    return windows


def _ts(dt: datetime) -> str:
    return (dt.astimezone(_TZ) if _TZ else dt).strftime("%m-%d %H:%M")


def _gap(minutes: float) -> str:
    if minutes >= 1440:
        return f"{minutes / 1440:.0f} روز بعد"
    if minutes >= 60:
        return f"{minutes / 60:.0f} ساعت بعد"
    return f"{minutes:.0f} دقیقه بعد"


def _line(m: ChatMessage) -> str:
    rt = f" ↩︎#{m.reply_to}" if m.reply_to else ""
    return f"#{m.message_id} [{_ts(m.date)}] {m.author_name or '?'} ({m.author_id}){rt}: {m.text.strip()}"


def render(w: Window, cfg: EngineConfig) -> str:
    if w.new and w.new[0].platform == "x":
        out = ["Independent public X posts from different authors; do not treat them as a conversation or use another author's post as context.", "NEW messages:"]
        out.extend(f"{_line(m)} URL: {m.url or ''}" for m in w.new)
        return "\n".join(out)
    out = [f"Group: {w.chat_title or w.chat_id}"]
    if w.parents:
        out.append("Older messages that new messages reply to:")
        out += ["  " + _line(p) for p in w.parents]
    if w.context:
        out.append("EARLIER messages (context only, already analysed):")
        out += ["  " + _line(m) for m in w.context]
    out.append("NEW messages:")
    last = w.context[-1].date if w.context else None
    for m in w.new:
        if last is not None:
            gap = (m.date - last).total_seconds() / 60
            if gap >= cfg.gap_marker_minutes:
                out.append(f"⏸ ── {_gap(gap)} ──")
        out.append(_line(m))
        last = m.date
    return "\n".join(out)

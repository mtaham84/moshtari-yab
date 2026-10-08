"""Backfill + live monitoring of one or more Telegram groups.

For every monitored group:
  1. resolve/join the group and resume from the last scanned message id;
  2. backfill: collect history (time + count limits), archive ALL messages
     locally, then process candidates oldest -> newest so context is local;
  3. live: archive every new message, attach late replies to still-pending
     inbox items, and enqueue new candidates.

Candidates (messages that pass the basic filter) are converted to the shared
``SocialMessage`` format and enqueued in the analysis inbox, where the agent
decides whether they are real opportunities.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import Message

from telegram_crawler import db as local_db
from telegram_crawler.config import settings
from telegram_crawler.detector import DetectorCallable, is_target_message
from telegram_crawler.extractor import build_lead_context, message_to_snippet
from telegram_crawler.models import GroupInfo, LeadContext
from telegram_crawler.ratelimit import FloodWaitTooLong, pace
from telegram_crawler.telegram_client import join_and_resolve_group
from telegram_crawler.webhook import DjangoWebhookDispatcher

log = logging.getLogger("telegram_crawler.monitor")

LeadCallback = Callable[[LeadContext], Awaitable[None]]


@dataclass
class GroupState:
    link: str | None
    entity: Any
    info: GroupInfo
    resume_from: int = 0          # last message id already scanned in a previous run
    max_seen: int = 0

    @property
    def peer_id(self) -> int | None:
        try:
            from telethon import utils

            return utils.get_peer_id(self.entity)
        except Exception:
            return None


class CrawlerMonitor:
    def __init__(
        self,
        client: TelegramClient,
        group_links: list[str] | str | None = None,
        detector: DetectorCallable | None = None,
        backfill_hours: float | None = None,
        backfill_limit: int | None = None,
        context_msg_count: int | None = None,
        on_lead_detected: LeadCallback | None = None,
        db_path: str | None = None,
        webhook: DjangoWebhookDispatcher | None = None,
        analysis_store: Any = None,
        enqueue: bool | None = None,
        resume: bool = True,
    ) -> None:
        self.client = client
        if isinstance(group_links, str):
            group_links = [group_links]
        self.group_links = [g for g in (group_links or []) if g and g.strip()]
        self.detector = detector or is_target_message
        self.backfill_hours = settings.backfill_hours if backfill_hours is None else backfill_hours
        self.backfill_limit = settings.backfill_limit if backfill_limit is None else backfill_limit
        self.context_msg_count = settings.context_msg_count if context_msg_count is None else context_msg_count
        self.on_lead_detected = on_lead_detected
        self.db_path = db_path or settings.db_path
        self.webhook = webhook or DjangoWebhookDispatcher(db_path=self.db_path)
        self.resume = resume
        self.enqueue_enabled = settings.analysis_enabled if enqueue is None else enqueue
        self._store = analysis_store
        self.groups: list[GroupState] = []
        self.stats = {"archived": 0, "candidates": 0, "enqueued": 0, "late_replies_attached": 0}
        local_db.init_db(self.db_path)

    # ----------------------------------------------------------------- setup
    @property
    def store(self):
        if self._store is None and self.enqueue_enabled:
            from analysis.store import AnalysisStore

            self._store = AnalysisStore()
        return self._store

    def add_resolved_group(self, entity: Any, info: GroupInfo, link: str | None = None) -> GroupState:
        state = GroupState(link=link, entity=entity, info=info)
        if self.resume:
            saved = local_db.get_group_monitor(info.group_id, db_path=self.db_path)
            if saved:
                state.resume_from = int(saved.get("last_scanned_msg_id") or 0)
        state.max_seen = state.resume_from
        self.groups.append(state)
        return state

    async def initialize(self) -> None:
        if self.webhook.enabled:
            await self.webhook.sync_pending()
        for link in self.group_links:
            try:
                entity, info = await join_and_resolve_group(self.client, link)
            except FloodWaitTooLong as exc:
                log.error("Skipping %s: %s", link, exc)
                continue
            except Exception as exc:
                log.error("Could not join/resolve %s: %s", link, exc)
                continue
            state = self.add_resolved_group(entity, info, link)
            log.info("Group ready: '%s' (id=%s, resume after msg %s)", info.title, info.group_id, state.resume_from)
            await pace()
        if not self.groups:
            raise RuntimeError("No group could be resolved; nothing to monitor.")

    # ------------------------------------------------------------- internals
    def _archive(self, state: GroupState, msg: Message, is_candidate: bool = False) -> bool:
        snippet = message_to_snippet(msg)
        sender = getattr(msg, "sender", None)
        username = getattr(sender, "username", None)
        is_bot = getattr(sender, "bot", False) is True
        inserted = local_db.save_message(
            state.info.group_id, snippet,
            sender_username=username if isinstance(username, str) else None,
            sender_is_bot=is_bot, is_candidate=is_candidate, db_path=self.db_path,
        )
        if inserted:
            self.stats["archived"] += 1
        if msg.id > state.max_seen:
            state.max_seen = msg.id
        return inserted

    async def _is_candidate(self, state: GroupState, msg: Message) -> bool:
        text = (getattr(msg, "message", None) or "").strip()
        if not text:
            return False
        sender = getattr(msg, "sender", None)
        metadata = {
            "msg_id": msg.id,
            "date": msg.date.isoformat() if getattr(msg, "date", None) else None,
            "group_id": state.info.group_id,
            "is_bot": getattr(sender, "bot", False) is True,
        }
        return bool(await self.detector(text, metadata))

    async def _process_candidate(self, state: GroupState, msg: Message, fetch_children_remote: bool) -> LeadContext | None:
        if local_db.is_message_enqueued(state.info.group_id, msg.id, db_path=self.db_path):
            return None
        self.stats["candidates"] += 1
        lead = await build_lead_context(
            client=self.client,
            entity=state.entity,
            group_info=state.info,
            target_msg=msg,
            context_msg_count=self.context_msg_count,
            db_path=self.db_path,
            fetch_children_remote=fetch_children_remote,
        )
        if lead.user.is_bot:
            return None
        local_db.save_lead(lead, db_path=self.db_path)

        if self.enqueue_enabled and self.store is not None:
            from sources.telegram_adapter import lead_context_to_social_message

            if self.store.enqueue(lead_context_to_social_message(lead)):
                self.stats["enqueued"] += 1
        local_db.mark_message_enqueued(state.info.group_id, msg.id, db_path=self.db_path)

        if self.webhook.enabled:  # legacy path, disabled by default
            await self.webhook.send_lead(lead)
        if self.on_lead_detected:
            try:
                await self.on_lead_detected(lead)
            except Exception as exc:
                log.error("Error in on_lead_detected callback: %s", exc)
        return lead

    def _save_progress(self, state: GroupState) -> None:
        local_db.update_group_monitor(
            group_id=state.info.group_id, title=state.info.title, link=state.link,
            last_msg_id=state.max_seen, db_path=self.db_path,
        )

    async def _collect_history(self, state: GroupState) -> list[Message]:
        """Newest -> oldest, stopping at the time cutoff, the count limit, or the resume point."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.backfill_hours)
        collected: list[Message] = []
        offset_id = 0
        retries = 0
        while len(collected) < self.backfill_limit:
            reached_end = True
            try:
                kwargs: dict[str, Any] = {"limit": self.backfill_limit - len(collected), "min_id": state.resume_from}
                if offset_id:
                    kwargs["offset_id"] = offset_id
                async for msg in self.client.iter_messages(state.entity, **kwargs):
                    msg_date = msg.date if msg.date.tzinfo else msg.date.replace(tzinfo=timezone.utc)
                    if msg_date < cutoff:
                        break
                    collected.append(msg)
                    offset_id = msg.id
                    if len(collected) % 50 == 0:
                        await asyncio.sleep(0)
            except FloodWaitError as exc:
                retries += 1
                wait = int(getattr(exc, "seconds", 0) or 0) + 1
                if wait > settings.flood_max_wait or retries > settings.flood_max_retries:
                    log.error("Backfill of '%s' stopped by FloodWait (%ss). Partial history kept.", state.info.title, wait)
                    break
                log.warning("FloodWait during backfill of '%s': sleeping %ss", state.info.title, wait)
                await asyncio.sleep(wait)
                reached_end = False
            if reached_end:
                break
        return collected

    # ------------------------------------------------------------ public API
    async def run_backfill(self, state: GroupState | None = None) -> int:
        """Backfill one group (or all). Returns the number of candidates found."""
        if state is None:
            total = 0
            for st in self.groups:
                total += await self.run_backfill(st)
            return total
        if self.backfill_limit <= 0:
            log.info("Backfill limit is 0; skipping history for '%s'.", state.info.title)
            return 0

        history = await self._collect_history(state)
        history.reverse()  # oldest first
        for msg in history:  # archive everything first so context lookups stay local
            self._archive(state, msg)

        found = 0
        for i, msg in enumerate(history, 1):
            if await self._is_candidate(state, msg):
                local_db.mark_message_candidate(state.info.group_id, msg.id, db_path=self.db_path)
                if await self._process_candidate(state, msg, fetch_children_remote=False):
                    found += 1
            if i % 20 == 0:
                await asyncio.sleep(0)
        self._save_progress(state)
        log.info(
            "Backfill '%s': %s messages scanned (after msg %s), %s candidates enqueued.",
            state.info.title, len(history), state.resume_from, found,
        )
        return found

    async def handle_live_message(self, state: GroupState, msg: Message) -> LeadContext | None:
        self._archive(state, msg)
        parent_id = getattr(getattr(msg, "reply_to", None), "reply_to_msg_id", None)
        if isinstance(parent_id, int) and self.enqueue_enabled and self.store is not None:
            from analysis.schemas import ContextMessage
            from sources.telegram_adapter import telegram_uid

            snippet = message_to_snippet(msg)
            if snippet.text and self.store.add_context(
                telegram_uid(state.info.group_id, parent_id),
                ContextMessage(id=str(msg.id), relation="reply", author_name=snippet.sender_name, text=snippet.text, created_at=snippet.date),
            ):
                self.stats["late_replies_attached"] += 1
        lead = None
        if await self._is_candidate(state, msg):
            local_db.mark_message_candidate(state.info.group_id, msg.id, db_path=self.db_path)
            lead = await self._process_candidate(state, msg, fetch_children_remote=False)
        self._save_progress(state)
        return lead

    def _state_for_chat(self, chat_id: Any) -> GroupState | None:
        for st in self.groups:
            if chat_id is not None and chat_id in (st.peer_id, st.info.group_id):
                return st
        return self.groups[0] if len(self.groups) == 1 else None

    async def start_live_monitoring(self) -> None:
        entities = [st.entity for st in self.groups]
        log.info("🟢 Live monitoring %s group(s): %s", len(entities), ", ".join(st.info.title for st in self.groups))

        @self.client.on(events.NewMessage(chats=entities))
        async def handler(event: events.NewMessage.Event) -> None:
            state = self._state_for_chat(getattr(event, "chat_id", None))
            if state is None:
                return
            try:
                await self.handle_live_message(state, event.message)
            except Exception as exc:  # never kill the listener
                log.exception("Failed to handle live message %s: %s", getattr(event.message, "id", "?"), exc)

    async def run(self, live: bool = True) -> None:
        await self.initialize()
        await self.run_backfill()
        log.info("Stats after backfill: %s", self.stats)
        if live:
            await self.start_live_monitoring()
            log.info("Listening for new messages (Ctrl+C to stop)...")
            await self.client.run_until_disconnected()


class LeadMonitor(CrawlerMonitor):
    """Backwards-compatible single-group entry point."""

    def __init__(self, client: TelegramClient, group_link: str | None = None, **kwargs: Any) -> None:
        super().__init__(client, [group_link] if group_link else [], **kwargs)

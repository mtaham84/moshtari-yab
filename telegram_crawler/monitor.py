"""Backfill + live archiving of Telegram groups into PostgreSQL.

For every message the write order is: chat → sender (user or chat) → parent message → message,
so a stored message always points to a user/chat (and, when it is a reply, a parent) that already exists.
Parents older than the backfill window are fetched once and stored with ``is_context = TRUE``.
A background worker fills each new user's bio once (users.GetFullUser), slowly, to avoid FloodWait.

The crawler does no filtering or analysis: need_engine reads the archive and decides what is a need.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl import types
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.functions.users import GetFullUserRequest

from telegram_crawler.config import settings
from telegram_crawler.db import Archive
from telegram_crawler.extractor import chat_record, message_record, peer_id, user_record
from telegram_crawler.panel import PanelCommunities, normalize_link
from telegram_crawler.ratelimit import FloodWaitTooLong, pace, with_flood_retry
from telegram_crawler.telegram_client import join_and_resolve_group

log = logging.getLogger("telegram_crawler.monitor")
PARENT_BATCH = 100
JOIN_RETRY_SECONDS = 600     # a panel link that failed to join is retried after this long


@dataclass
class GroupState:
    entity: Any
    chat_id: int
    title: str
    link: str | None = None
    resume_from: int = 0          # last message id already scanned in a previous run
    max_seen: int = 0
    backfilled: bool = False


async def fetch_full_chat(client: TelegramClient, entity: Any) -> Any:
    """``ChannelFull``/``ChatFull`` (about, members count, linked chat) or None if not allowed."""
    try:
        if isinstance(entity, types.Channel):
            res = await with_flood_retry(lambda: client(GetFullChannelRequest(entity)), what="get_full_channel")
        elif isinstance(entity, types.Chat):
            res = await with_flood_retry(lambda: client(GetFullChatRequest(entity.id)), what="get_full_chat")
        else:
            return None
        return getattr(res, "full_chat", None)
    except FloodWaitTooLong:
        raise
    except Exception as exc:
        log.warning("Could not fetch full info of %s: %s", getattr(entity, "title", entity), exc)
        return None


class CrawlerMonitor:
    def __init__(
        self,
        client: TelegramClient,
        group_links: list[str] | str | None = None,
        backfill_hours: float | None = None,
        backfill_limit: int | None = None,
        archive: Archive | None = None,
        resume: bool = True,
        fetch_profiles: bool | None = None,
        parent_depth: int | None = None,
        panel: PanelCommunities | None = None,
    ) -> None:
        self.client = client
        if isinstance(group_links, str):
            group_links = [group_links]
        self.group_links = [g for g in (group_links or []) if g and g.strip()]
        self.backfill_hours = settings.backfill_hours if backfill_hours is None else backfill_hours
        self.backfill_limit = settings.backfill_limit if backfill_limit is None else backfill_limit
        self.archive = archive or Archive()
        self.resume = resume
        self.fetch_profiles = settings.fetch_profiles if fetch_profiles is None else fetch_profiles
        self.parent_depth = settings.parent_depth if parent_depth is None else parent_depth
        self.store_raw = settings.store_raw
        self.groups: list[GroupState] = []
        self.panel = panel
        self.paused: set[int] = set()            # chats deactivated in the panel: live messages are ignored
        self._cli_chats: set[int] = set()        # groups given on the command line / groups file (always active)
        self._links: dict[str, int] = {}         # normalized panel link → chat id (joined in this run)
        self._join_failed: dict[int, float] = {}  # community id → retry after (monotonic time)
        self.stats = {"archived": 0, "context": 0, "users": 0, "profiles": 0}

    # ----------------------------------------------------------------- setup
    def add_resolved_group(self, entity: Any, link: str | None = None, full: Any = None) -> GroupState:
        rec = chat_record(entity, full, store_raw=self.store_raw)
        self.archive.upsert_chat(rec, monitored=True, join_link=link)
        existing = self._state_for_chat(rec["chat_id"])
        if existing is not None:                 # same group reached through another link
            return existing
        state = GroupState(entity=entity, chat_id=rec["chat_id"], title=rec["title"] or str(rec["chat_id"]), link=link)
        if self.resume:
            saved = self.archive.get_chat(state.chat_id)
            state.resume_from = int((saved or {}).get("last_scanned_msg_id") or 0)
        state.max_seen = state.resume_from
        self.groups.append(state)
        return state

    async def add_link(self, link: str) -> tuple[GroupState, Any]:
        """Join (if needed) and register one group. Raises when the link cannot be joined/resolved."""
        entity, _ = await join_and_resolve_group(self.client, link)
        full = await fetch_full_chat(self.client, entity)
        state = self.add_resolved_group(entity, link, full)
        log.info("Group ready: '%s' (id=%s, resume after msg %s)", state.title, state.chat_id, state.resume_from)
        return state, full

    async def initialize(self) -> None:
        for link in self.group_links:
            try:
                state, _ = await self.add_link(link)
            except FloodWaitTooLong as exc:
                log.error("Skipping %s: %s", link, exc)
                continue
            except Exception as exc:
                log.error("Could not join/resolve %s: %s", link, exc)
                continue
            self._cli_chats.add(state.chat_id)
            await pace()
        if not self.groups and self.panel is None:
            raise RuntimeError("No group could be resolved; nothing to monitor.")

    # ----------------------------------------------------------------- panel
    async def sync_panel(self) -> int:
        """One pass over the panel communities: join new links, pause/resume, refresh counters.
        Returns the number of groups joined in this pass."""
        if self.panel is None:
            return 0
        rows = self.panel.communities()
        active: set[int] = set(self._cli_chats)
        joined = 0
        for c in rows:
            if not c.is_active:
                continue
            key = normalize_link(c.link)
            chat_id = self._links.get(key)
            if chat_id is None:
                if self._join_failed.get(c.id, 0) > time.monotonic():
                    continue
                try:
                    state, full = await self.add_link(c.link)
                except FloodWaitTooLong as exc:
                    log.warning("Panel community %s (%s) postponed: %s", c.id, c.link, exc)
                    self._join_failed[c.id] = time.monotonic() + JOIN_RETRY_SECONDS
                    continue
                except Exception as exc:
                    log.error("Panel community %s (%s) could not be joined: %s", c.id, c.link, exc)
                    self.panel.mark_error(c.id, str(exc) or exc.__class__.__name__)
                    self._join_failed[c.id] = time.monotonic() + JOIN_RETRY_SECONDS
                    continue
                self._join_failed.pop(c.id, None)
                self._links[key] = chat_id = state.chat_id
                members = getattr(full, "participants_count", None) or getattr(state.entity, "participants_count", None)
                self.panel.mark_joined(c.id, chat_id, state.title, members)
                joined += 1
                await pace()
            elif c.chat_id != chat_id or c.status != "ACTIVE":
                self.panel.mark_joined(c.id, chat_id, None, None)
            active.add(chat_id)
        newly_paused = {st.chat_id for st in self.groups} - active
        resumed = self.paused - newly_paused
        self.paused = newly_paused
        self.panel.mark_paused([c.id for c in rows if not c.is_active])
        for st in self.groups:   # new groups: history; resumed groups: the messages missed while paused
            if st.chat_id in active and (st.chat_id in resumed or not st.backfilled):
                st.resume_from = st.max_seen
                await self.run_backfill(st)
        self.panel.refresh_counters()
        return joined

    async def _panel_worker(self) -> None:
        while True:
            await asyncio.sleep(settings.panel_poll)
            try:
                await self.sync_panel()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Panel sync error: %s", exc)

    # ------------------------------------------------------------- internals
    async def _sender(self, msg: Any) -> Any:
        sender = getattr(msg, "sender", None)
        if sender is None and getattr(msg, "from_id", None) is not None and hasattr(msg, "get_sender"):
            try:
                sender = await msg.get_sender()
            except Exception:
                sender = None
        return sender

    async def _store_sender(self, state: GroupState, msg: Any, rec: dict[str, Any]) -> None:
        sender = await self._sender(msg) if (rec["sender_user_id"] or rec["sender_chat_id"]) else None
        if rec["sender_user_id"]:
            if isinstance(sender, types.User) and sender.id == rec["sender_user_id"]:
                if self.archive.add_user(user_record(sender, store_raw=self.store_raw)):
                    self.stats["users"] += 1
            else:
                self.archive.ensure_user_id(rec["sender_user_id"])
        if rec["sender_chat_id"] and rec["sender_chat_id"] != state.chat_id:
            if isinstance(sender, (types.Channel, types.Chat)) and peer_id(sender) == rec["sender_chat_id"]:
                self.archive.upsert_chat(chat_record(sender, store_raw=self.store_raw))
            else:
                self.archive.ensure_chat_id(rec["sender_chat_id"])

    @staticmethod
    def _parent_id(msg: Any) -> int | None:
        reply = getattr(msg, "reply_to", None)
        if reply is None or getattr(reply, "reply_to_peer_id", None) is not None:  # no reply / reply into another chat
            return None
        rid = getattr(reply, "reply_to_msg_id", None)
        return rid if isinstance(rid, int) else None

    async def _fetch_parents(self, state: GroupState, ids: set[int], depth: int) -> None:
        """Fetch and store (as context) replied-to messages that are not archived yet."""
        if depth > self.parent_depth:
            return
        missing = sorted(set(ids) - self.archive.existing_message_ids(state.chat_id, ids))
        for i in range(0, len(missing), PARENT_BATCH):
            chunk = missing[i:i + PARENT_BATCH]
            try:
                found = await with_flood_retry(lambda: self.client.get_messages(state.entity, ids=chunk), what="get_parents")
            except FloodWaitTooLong as exc:
                log.warning("Parents of '%s' skipped: %s", state.title, exc)
                return
            except Exception as exc:
                log.warning("Could not fetch parent messages %s of '%s': %s", chunk, state.title, exc)
                continue
            found = [m for m in (found if isinstance(found, list) else [found]) if m is not None and not isinstance(m, types.MessageEmpty)]
            for m in sorted(found, key=lambda m: m.id):
                await self._store(state, m, is_context=True, depth=depth)
            await pace()

    async def _store(self, state: GroupState, msg: Any, *, is_context: bool = False, depth: int = 0) -> bool:
        rec = message_record(msg, state.chat_id, is_context=is_context, store_raw=self.store_raw)
        await self._store_sender(state, msg, rec)
        parent = self._parent_id(msg)
        if parent and self.parent_depth > depth:
            await self._fetch_parents(state, {parent}, depth + 1)
        inserted = self.archive.add_message(rec)
        if inserted:
            self.stats["context" if is_context else "archived"] += 1
        if not is_context and msg.id > state.max_seen:
            state.max_seen = msg.id
        return inserted

    async def _collect_history(self, state: GroupState) -> list[Any]:
        """Newest -> oldest, stopping at the time cutoff, the count limit, or the resume point."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.backfill_hours)
        collected: list[Any] = []
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
                    log.error("Backfill of '%s' stopped by FloodWait (%ss). Partial history kept.", state.title, wait)
                    break
                log.warning("FloodWait during backfill of '%s': sleeping %ss", state.title, wait)
                await asyncio.sleep(wait)
                reached_end = False
            if reached_end:
                break
        return collected

    # ------------------------------------------------------------ public API
    async def run_backfill(self, state: GroupState | None = None) -> int:
        """Archive recent history of one group (or all). Returns the number of newly archived messages."""
        if state is None:
            total = 0
            for st in self.groups:
                total += await self.run_backfill(st)
            return total
        state.backfilled = True
        if self.backfill_limit <= 0:
            log.info("Backfill limit is 0; skipping history for '%s'.", state.title)
            return 0
        history = await self._collect_history(state)
        history.reverse()  # oldest first → archive ids follow conversation order, parents before replies
        in_window = {m.id for m in history}
        older_parents = {p for p in (self._parent_id(m) for m in history) if p and p not in in_window}
        if older_parents and self.parent_depth > 0:
            await self._fetch_parents(state, older_parents, 1)       # one batched request instead of one per reply
        before = self.stats["archived"]
        for msg in history:
            await self._store(state, msg)
        new = self.stats["archived"] - before
        self.archive.save_progress(state.chat_id, state.max_seen)
        log.info("Backfill '%s': %s messages scanned (after msg %s), %s new archived, %s parents as context.",
                 state.title, len(history), state.resume_from, new, self.stats["context"])
        return new

    async def handle_live_message(self, state: GroupState, msg: Any) -> bool:
        inserted = await self._store(state, msg)
        self.archive.save_progress(state.chat_id, state.max_seen)
        return inserted

    async def fetch_profiles_once(self, batch: int = 20) -> int:
        """Fill the bio of users we have not profiled yet. Returns how many were processed."""
        done = 0
        for uid in self.archive.users_without_profile(batch):
            try:
                full = await with_flood_retry(lambda: self.client(GetFullUserRequest(uid)), what="get_full_user")
                bio = getattr(getattr(full, "full_user", None), "about", None)
            except FloodWaitTooLong as exc:
                log.warning("Profile worker paused: %s", exc)
                break
            except Exception as exc:  # privacy, deleted account, unknown access hash → don't retry forever
                log.debug("No profile for user %s: %s", uid, exc)
                bio = None
            self.archive.set_profile(uid, bio if isinstance(bio, str) and bio else None)
            done += 1
            self.stats["profiles"] += 1
            if settings.profile_delay > 0:
                await asyncio.sleep(settings.profile_delay)
        return done

    async def _profile_worker(self) -> None:
        while True:
            try:
                if not await self.fetch_profiles_once():
                    await asyncio.sleep(60)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.exception("Profile worker error: %s", exc)
                await asyncio.sleep(60)

    def _state_for_chat(self, chat_id: Any) -> GroupState | None:
        for st in self.groups:
            if chat_id is not None and chat_id == st.chat_id:
                return st
        return None

    async def start_live_monitoring(self) -> None:
        log.info("🟢 Live monitoring %s group(s): %s", len(self.groups), ", ".join(st.title for st in self.groups) or "-")

        # No ``chats=`` filter: groups joined later from the panel are picked up without re-registering.
        @self.client.on(events.NewMessage())
        async def handler(event: events.NewMessage.Event) -> None:
            state = self._state_for_chat(getattr(event, "chat_id", None))
            if state is None or state.chat_id in self.paused:
                return
            try:
                await self.handle_live_message(state, event.message)
            except Exception as exc:  # never kill the listener
                log.exception("Failed to archive live message %s: %s", getattr(event.message, "id", "?"), exc)

    async def run(self, live: bool = True) -> None:
        await self.initialize()
        await self.run_backfill()
        await self.sync_panel()
        log.info("Stats after backfill: %s", self.stats)
        if not live:
            if self.fetch_profiles:
                while await self.fetch_profiles_once():
                    pass
            return
        await self.start_live_monitoring()
        workers = []
        if self.fetch_profiles:
            workers.append(asyncio.create_task(self._profile_worker()))
        if self.panel is not None:
            workers.append(asyncio.create_task(self._panel_worker()))
            log.info("Watching the panel communities every %ss.", settings.panel_poll)
        log.info("Listening for new messages (Ctrl+C to stop)...")
        try:
            await self.client.run_until_disconnected()
        finally:
            for w in workers:
                w.cancel()


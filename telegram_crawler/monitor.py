"""Orchestrator for backfilling history and live monitoring of Telegram groups."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, events
from telethon.tl.types import Message

from telegram_crawler.config import settings
from telegram_crawler.db import init_db, save_lead, update_group_monitor
from telegram_crawler.detector import DetectorCallable, is_target_message
from telegram_crawler.extractor import build_lead_context
from telegram_crawler.models import GroupInfo, LeadContext
from telegram_crawler.telegram_client import join_and_resolve_group
from telegram_crawler.webhook import DjangoWebhookDispatcher

log = logging.getLogger("telegram_crawler.monitor")

LeadCallback = Callable[[LeadContext], Awaitable[None]]


class LeadMonitor:
    """Monitors a Telegram group, evaluates messages, extracts context, and saves leads."""

    def __init__(
        self,
        client: TelegramClient,
        group_link: str,
        detector: DetectorCallable | None = None,
        backfill_hours: float | None = None,
        backfill_limit: int | None = None,
        context_msg_count: int | None = None,
        on_lead_detected: LeadCallback | None = None,
        db_path: str | None = None,
        webhook: DjangoWebhookDispatcher | None = None,
    ) -> None:
        self.client = client
        self.group_link = group_link
        self.detector = detector or is_target_message
        self.backfill_hours = (
            backfill_hours if backfill_hours is not None else settings.backfill_hours
        )
        self.backfill_limit = (
            backfill_limit if backfill_limit is not None else settings.backfill_limit
        )
        self.context_msg_count = (
            context_msg_count if context_msg_count is not None else settings.context_msg_count
        )
        self.on_lead_detected = on_lead_detected
        self.db_path = db_path or settings.db_path
        self.webhook = webhook or DjangoWebhookDispatcher()

        self.entity: Any = None
        self.group_info: GroupInfo | None = None
        self._max_seen_msg_id: int = 0

    async def initialize(self) -> None:
        """Initialize database, resolve group entity, and join if needed."""
        init_db(self.db_path)
        # Attempt to sync any unsynced leads from previous runs
        if self.webhook.enabled:
            await self.webhook.sync_pending()
        log.info("Resolving and joining group: %s", self.group_link)
        self.entity, self.group_info = await join_and_resolve_group(
            self.client, self.group_link
        )
        log.info(
            "Target group ready: '%s' (ID: %s)",
            self.group_info.title,
            self.group_info.group_id,
        )

    async def _process_candidate_message(self, msg: Message) -> LeadContext | None:
        """Check if message matches target criteria, extract context, and store."""
        text = (msg.message or "").strip()
        if not text:
            return None

        metadata = {
            "msg_id": msg.id,
            "date": msg.date.isoformat() if getattr(msg, "date", None) else None,
            "group_id": self.group_info.group_id if self.group_info else None,
        }

        # 1. Run detection filter
        is_match = await self.detector(text, metadata)
        if not is_match:
            return None

        log.info("🎯 TARGET MATCH DETECTED! msg_id=%s, text='%s...'", msg.id, text[:60])

        # 2. Extract deep context: User info, 5 previous messages, reply thread
        assert self.group_info is not None
        lead = await build_lead_context(
            client=self.client,
            entity=self.entity,
            group_info=self.group_info,
            target_msg=msg,
            context_msg_count=self.context_msg_count,
        )

        # 3. Persist into SQLite
        inserted = save_lead(lead, db_path=self.db_path)
        if inserted:
            log.info("Lead %s successfully stored in database.", lead.lead_id)
        else:
            log.info("Lead %s was already stored previously.", lead.lead_id)

        # 4. Dispatch to Django webhook (Microservice integration)
        if self.webhook.enabled:
            await self.webhook.send_lead(lead)

        # 5. Dispatch to callback method if supplied
        if self.on_lead_detected:
            try:
                await self.on_lead_detected(lead)
            except Exception as exc:
                log.error("Error in on_lead_detected callback: %s", exc)

        return lead

    async def run_backfill(self) -> int:
        """
        Scan historical messages in the group respecting backfill_hours and backfill_limit.
        Returns count of target leads found.
        """
        if self.backfill_limit <= 0:
            log.info("Backfill limit is 0; skipping historical scan.")
            return 0

        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=self.backfill_hours)
        log.info(
            "Starting backfill: up to %s messages, newer than %s (%s hours ago)",
            self.backfill_limit,
            cutoff_time.strftime("%Y-%m-%d %H:%M:%S UTC"),
            self.backfill_hours,
        )

        leads_found = 0
        scanned_count = 0

        async for msg in self.client.iter_messages(self.entity, limit=self.backfill_limit):
            scanned_count += 1
            if msg.id > self._max_seen_msg_id:
                self._max_seen_msg_id = msg.id

            # Check time cutoff
            msg_date = msg.date
            if msg_date.tzinfo is None:
                msg_date = msg_date.replace(tzinfo=timezone.utc)

            if msg_date < cutoff_time:
                log.info(
                    "Reached cutoff time at message %s (%s). Finishing backfill.",
                    msg.id,
                    msg_date,
                )
                break

            lead = await self._process_candidate_message(msg)
            if lead:
                leads_found += 1

            # Give control back to event loop
            if scanned_count % 20 == 0:
                await asyncio.sleep(0.1)

        assert self.group_info is not None
        update_group_monitor(
            group_id=self.group_info.group_id,
            title=self.group_info.title,
            link=self.group_info.invite_link,
            last_msg_id=self._max_seen_msg_id,
            db_path=self.db_path,
        )

        log.info(
            "Backfill finished: %s messages scanned, %s leads discovered.",
            scanned_count,
            leads_found,
        )
        return leads_found

    async def start_live_monitoring(self) -> None:
        """Register live event handler for incoming messages."""
        assert self.entity is not None
        log.info("🟢 Starting Live Real-time monitoring on '%s'...", self.group_info.title)

        @self.client.on(events.NewMessage(chats=self.entity))
        async def handler(event: events.NewMessage.Event) -> None:
            msg = event.message
            if msg.id > self._max_seen_msg_id:
                self._max_seen_msg_id = msg.id

            await self._process_candidate_message(msg)

            assert self.group_info is not None
            update_group_monitor(
                group_id=self.group_info.group_id,
                title=self.group_info.title,
                link=self.group_info.invite_link,
                last_msg_id=self._max_seen_msg_id,
                db_path=self.db_path,
            )

        log.info("Listening for new live messages (Press Ctrl+C to stop)...")

    async def run(self, live: bool = True) -> None:
        """Execute full monitoring pipeline (Backfill then Live streaming)."""
        await self.initialize()
        await self.run_backfill()

        if live:
            await self.start_live_monitoring()
            await self.client.run_until_disconnected()

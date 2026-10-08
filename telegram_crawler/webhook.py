"""(Legacy) Dispatcher that pushes raw, *un-analysed* Telegram candidates to Django.

Disabled by default since milestone 1: candidates now go to the shared analysis
inbox (analysis.store) and only agent verdicts will be sent to Django (milestone 3).
Enable with DJANGO_WEBHOOK_ENABLED=true only for backwards-compatible demos.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from telegram_crawler.config import settings
from telegram_crawler.db import get_unsynced_leads, mark_lead_synced
from telegram_crawler.models import LeadContext

log = logging.getLogger("telegram_crawler.webhook")


def format_intent_reasoning(lead: LeadContext) -> str:
    """(Legacy) Format rich conversation context into a human-readable reasoning string for Django."""
    lines = [
        f"گروه: {lead.group.title} (ID: {lead.group.group_id})",
        f"پیام هدف: «{lead.target_message.text}»",
    ]

    if lead.previous_messages:
        lines.append("کانتکست چند پیام قبل در چت:")
        for prev in lead.previous_messages[-3:]:
            lines.append(f"  • {prev.sender_name or 'کاربر'}: {prev.text}")

    if lead.reply_thread.parent_messages:
        lines.append("ریپلای به پیام‌های قبلی:")
        for parent in lead.reply_thread.parent_messages:
            lines.append(f"  ↑ {parent.sender_name or 'کاربر'}: {parent.text}")

    return "\n".join(lines)


def build_django_payload(lead: LeadContext) -> dict[str, Any]:
    """Build the JSON payload structure matching Django's api_submit_lead_view."""
    from sources.telegram_adapter import telegram_message_url

    post_url = telegram_message_url(lead.group, lead.target_message.message_id) or ""

    lead_handle = (
        f"@{lead.user.username}" if lead.user.username else str(lead.user.user_id)
    )

    return {
        "channel": "TELEGRAM",
        "lead_handle": lead_handle,
        "lead_display_name": lead.user.display_name,
        "post_url": post_url,
        "content_snippet": lead.target_message.text,
        "intent_score": 0,  # not analysed yet; real scores come from the analysis agent
        "intent_reasoning": format_intent_reasoning(lead),
        "context_messages": [m.model_dump(mode="json") for m in lead.previous_messages],
        "reply_thread": lead.reply_thread.model_dump(mode="json"),
        "detected_at": lead.detected_at.isoformat(),
        "group_id": lead.group.group_id,
        "group_title": lead.group.title,
    }


def _http_post_sync(url: str, payload: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    """Execute synchronous HTTP POST to the Django webhook."""
    data_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data_bytes,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "TelegramLeadCrawler/1.0",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else {}


class DjangoWebhookDispatcher:
    """Sends candidate leads to the Django REST endpoint."""

    def __init__(
        self,
        url: str | None = None,
        enabled: bool | None = None,
        db_path: str | None = None,
        timeout: float = 5.0,
    ) -> None:
        self.url = url or settings.django_webhook_url
        self.enabled = enabled if enabled is not None else settings.django_webhook_enabled
        self.db_path = db_path or settings.db_path
        self.timeout = timeout

    async def send_lead(self, lead: LeadContext) -> bool:
        """
        Send a discovered lead to Django.
        Returns True if successfully received, False otherwise.
        Does not raise exceptions on connection failure.
        """
        if not self.enabled:
            log.debug("Django webhook is disabled in settings.")
            return False

        payload = build_django_payload(lead)

        try:
            log.info("Dispatching lead %s to Django webhook at %s...", lead.lead_id, self.url)
            response = await asyncio.to_thread(_http_post_sync, self.url, payload, self.timeout)
            status = response.get("status")

            if status in ("success", "duplicate"):
                log.info("✅ Django accepted lead %s (status=%s)", lead.lead_id, status)
                mark_lead_synced(lead.lead_id, is_synced=True, db_path=self.db_path)
                return True
            else:
                log.warning("Django returned non-success response: %s", response)
                return False

        except urllib.error.URLError as exc:
            log.info(
                "ℹ️ Django server is currently offline or unreachable at %s (%s). "
                "Lead %s is preserved locally in SQLite for future sync.",
                self.url,
                exc.reason if hasattr(exc, "reason") else exc,
                lead.lead_id,
            )
            return False
        except Exception as exc:
            log.warning("Unexpected error sending lead %s to Django webhook: %s", lead.lead_id, exc)
            return False

    async def sync_pending(self, limit: int = 50) -> int:
        """Attempt to sync any leads previously saved in SQLite that were not yet sent to Django."""
        if not self.enabled:
            return 0

        unsynced = get_unsynced_leads(limit=limit, db_path=self.db_path)
        if not unsynced:
            return 0

        log.info("Found %s unsynced leads in SQLite. Attempting sync to Django...", len(unsynced))
        synced_count = 0
        for lead in unsynced:
            success = await self.send_lead(lead)
            if success:
                synced_count += 1
            else:
                # If server is down, stop retrying immediately to avoid spamming
                break
        return synced_count

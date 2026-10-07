"""Unit tests for models, db, detector, extractor, and link parsing."""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
import pytest

from telegram_crawler.db import (
    get_connection,
    get_group_monitor,
    get_lead,
    init_db,
    list_leads,
    save_lead,
    update_group_monitor,
)
from telegram_crawler.detector import KeywordTargetDetector, normalize_text
from telegram_crawler.extractor import (
    build_lead_context,
    fetch_preceding_messages,
    fetch_reply_thread,
    message_to_snippet,
)
from telegram_crawler.models import (
    GroupInfo,
    LeadContext,
    MessageSnippet,
    ReplyThread,
    UserProfile,
)
from telegram_crawler.telegram_client import parse_group_link


# =====================================================================
# 1. Models Tests
# =====================================================================
def test_lead_context_serialization():
    dt = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
    user = UserProfile(
        user_id=12345,
        first_name="Ali",
        last_name="Rezaei",
        username="alirez",
        phone="+989123456789",
    )
    assert user.display_name == "Ali Rezaei"

    target_msg = MessageSnippet(
        message_id=100,
        sender_id=12345,
        sender_name="Ali Rezaei",
        text="سلام کسی روغن موتور کاسترول سراغ داره؟",
        date=dt,
        reply_to_msg_id=98,
    )

    lead = LeadContext(
        lead_id="999_100",
        group=GroupInfo(group_id=999, title="گروه خودرو و لوازم یدکی"),
        target_message=target_msg,
        user=user,
        previous_messages=[
            MessageSnippet(
                message_id=98,
                sender_id=54321,
                sender_name="Mohammad",
                text="سلام دوستان",
                date=dt,
            )
        ],
        reply_thread=ReplyThread(
            parent_messages=[
                MessageSnippet(
                    message_id=98,
                    sender_id=54321,
                    sender_name="Mohammad",
                    text="سلام دوستان",
                    date=dt,
                )
            ],
            child_replies=[],
        ),
        detected_at=dt,
    )

    # Test serialization to JSON
    json_data = lead.model_dump_json()
    assert "روغن موتور" in json_data
    assert "999_100" in json_data

    # Test deserialization
    recovered = LeadContext.model_validate_json(json_data)
    assert recovered.lead_id == "999_100"
    assert recovered.user.username == "alirez"
    assert len(recovered.previous_messages) == 1
    assert recovered.reply_thread.parent_messages[0].message_id == 98


# =====================================================================
# 2. Database Tests
# =====================================================================
def test_db_operations(tmp_path: Path):
    db_file = str(tmp_path / "test_leads.db")
    init_db(db_file)

    dt = datetime(2026, 10, 7, 12, 0, 0, tzinfo=timezone.utc)
    lead = LeadContext(
        lead_id="g1_m10",
        group=GroupInfo(group_id=1, title="Test Group", username="testgrp"),
        target_message=MessageSnippet(
            message_id=10,
            sender_id=555,
            sender_name="Tester",
            text="خریدار قطعات ۲۰۶ هستم",
            date=dt,
        ),
        user=UserProfile(user_id=555, first_name="Tester", username="tester1"),
        previous_messages=[],
        reply_thread=ReplyThread(),
        detected_at=dt,
    )

    # First insert -> True
    saved = save_lead(lead, db_path=db_file)
    assert saved is True

    # Duplicate insert -> False
    duplicate = save_lead(lead, db_path=db_file)
    assert duplicate is False

    # Get single lead
    retrieved = get_lead("g1_m10", db_path=db_file)
    assert retrieved is not None
    assert retrieved.lead_id == "g1_m10"
    assert retrieved.user.username == "tester1"
    assert retrieved.target_message.text == "خریدار قطعات ۲۰۶ هستم"

    # List leads
    leads = list_leads(group_id=1, db_path=db_file)
    assert len(leads) == 1
    assert leads[0].lead_id == "g1_m10"

    # Group monitor test
    update_group_monitor(1, "Test Group", "https://t.me/testgrp", last_msg_id=100, db_path=db_file)
    monitor_info = get_group_monitor(1, db_path=db_file)
    assert monitor_info is not None
    assert monitor_info["title"] == "Test Group"
    assert monitor_info["last_scanned_msg_id"] == 100


# =====================================================================
# 3. Detector Tests
# =====================================================================
@pytest.mark.asyncio
async def test_detector_matching():
    detector = KeywordTargetDetector(keywords=("روغن موتور", "خریدارم", "قیمت"))

    # Persian normalization check
    assert normalize_text("روغن\u200cموتور") == "روغن موتور"
    assert normalize_text("قيمت") == "قیمت"

    # Positive matches
    assert await detector("سلام من خریدارم کسی داره؟") is True
    assert await detector("قیمت روغن موتور چنده؟") is True
    assert await detector("خيلي وقته دنبال روغن موتورم") is True

    # Negative matches
    assert await detector("سلام عصر بخیر") is False
    assert await detector("فردا هوا چطوره؟") is False
    assert await detector("") is False


# =====================================================================
# 4. Link Parser Tests
# =====================================================================
def test_parse_group_link():
    # Private invite link with +
    t, ident = parse_group_link("https://t.me/+AbCdEfGh123")
    assert t == "invite"
    assert ident == "AbCdEfGh123"

    # Private invite link with joinchat
    t, ident = parse_group_link("https://t.me/joinchat/XyZ12345")
    assert t == "invite"
    assert ident == "XyZ12345"

    # Public group link
    t, ident = parse_group_link("https://t.me/my_supergroup")
    assert t == "public"
    assert ident == "my_supergroup"

    # @ username
    t, ident = parse_group_link("@my_channel")
    assert t == "public"
    assert ident == "my_channel"

    # Numeric id
    t, ident = parse_group_link("-1001234567890")
    assert t == "id"
    assert ident == "-1001234567890"


# =====================================================================
# 5. Extractor Mock Tests
# =====================================================================
@pytest.mark.asyncio
async def test_extractor_logic():
    dt = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)

    # Create mock target message
    mock_target = MagicMock()
    mock_target.id = 50
    mock_target.sender_id = 999
    mock_target.message = "کسی روغن موتور داره؟"
    mock_target.date = dt

    mock_sender = MagicMock()
    mock_sender.id = 999
    mock_sender.first_name = "Reza"
    mock_sender.last_name = "Ahmadi"
    mock_sender.username = "reza_ah"
    mock_sender.phone = None
    mock_sender.bot = False
    mock_sender.premium = True
    mock_target.get_sender = AsyncMock(return_value=mock_sender)

    # Reply to msg 45
    reply_header = MagicMock()
    reply_header.reply_to_msg_id = 45
    mock_target.reply_to = reply_header

    # Mock client
    mock_client = MagicMock()

    # Preceding messages (max_id=50 returns msg 49, 48 newest first)
    msg49 = MagicMock(id=49, sender_id=111, message="پیام قبلی ۱", date=dt, reply_to=None, sender=None)
    msg48 = MagicMock(id=48, sender_id=222, message="پیام قبلی ۲", date=dt, reply_to=None, sender=None)

    # Parent msg 45
    msg45 = MagicMock(id=45, sender_id=333, message="سلام بله روغن داریم", date=dt, reply_to=None, sender=None)

    # Child msg 52 (replied to 50)
    msg52 = MagicMock(id=52, sender_id=444, message="من دارم دایرکت پیام بده", date=dt, reply_to=None, sender=None)

    async def mock_get_messages(entity, **kwargs):
        if "max_id" in kwargs:
            # Preceding messages
            return [msg49, msg48]
        if "ids" in kwargs and kwargs["ids"] == 45:
            # Parent message
            return msg45
        if kwargs.get("reply_to") == 50:
            # Child replies
            return [msg52]
        return []

    mock_client.get_messages = AsyncMock(side_effect=mock_get_messages)

    group_info = GroupInfo(group_id=1001, title="گروه لوازم یدکی")
    lead = await build_lead_context(
        client=mock_client,
        entity=MagicMock(),
        group_info=group_info,
        target_msg=mock_target,
        context_msg_count=2,
    )

    # Assertions
    assert lead.lead_id == "1001_50"
    assert lead.user.user_id == 999
    assert lead.user.display_name == "Reza Ahmadi"
    assert lead.user.is_premium is True

    # Check preceding messages: should be reversed into chronological order (48 then 49)
    assert len(lead.previous_messages) == 2
    assert lead.previous_messages[0].message_id == 48
    assert lead.previous_messages[1].message_id == 49

    # Check thread
    assert len(lead.reply_thread.parent_messages) == 1
    assert lead.reply_thread.parent_messages[0].message_id == 45
    assert len(lead.reply_thread.child_replies) == 1
    assert lead.reply_thread.child_replies[0].message_id == 52


# =====================================================================
# 6. Monitor Pipeline End-to-End Test
# =====================================================================
@pytest.mark.asyncio
async def test_lead_monitor_pipeline(tmp_path: Path):
    from telegram_crawler.monitor import LeadMonitor

    db_file = str(tmp_path / "monitor_leads.db")
    dt = datetime.now(timezone.utc)

    # Mock client and messages
    mock_client = MagicMock()
    mock_entity = MagicMock()
    mock_entity.id = 555
    mock_entity.title = "گروه تست"
    mock_entity.username = "test_grp"

    msg1 = MagicMock(id=1, sender_id=10, message="سلام روز بخیر", date=dt, reply_to=None, sender=None)
    msg2 = MagicMock(id=2, sender_id=20, message="دنبال روغن موتور الف هستم", date=dt, reply_to=None, sender=None)
    msg2.get_sender = AsyncMock(return_value=MagicMock(id=20, first_name="Ahmad", last_name=None, username="ahmad20", phone=None, bot=False, premium=False))

    msg3 = MagicMock(id=3, sender_id=30, message="چطوری؟", date=dt, reply_to=None, sender=None)

    async def mock_iter(entity, limit=200):
        for m in [msg3, msg2, msg1]:
            yield m

    mock_client.iter_messages = mock_iter
    mock_client.get_messages = AsyncMock(return_value=[])

    leads_captured: list[LeadContext] = []

    async def capture_callback(lead: LeadContext):
        leads_captured.append(lead)

    monitor = LeadMonitor(
        client=mock_client,
        group_link="https://t.me/test_grp",
        backfill_hours=24,
        backfill_limit=10,
        context_msg_count=2,
        on_lead_detected=capture_callback,
        db_path=db_file,
    )

    monitor.entity = mock_entity
    monitor.group_info = GroupInfo(group_id=555, title="گروه تست", username="test_grp")
    init_db(db_file)

    leads_count = await monitor.run_backfill()

    assert leads_count == 1
    assert len(leads_captured) == 1
    assert leads_captured[0].lead_id == "555_2"
    assert leads_captured[0].user.user_id == 20
    assert "روغن موتور" in leads_captured[0].target_message.text

    # Verify database persistence
    db_lead = get_lead("555_2", db_path=db_file)
    assert db_lead is not None
    assert db_lead.user.user_id == 20
    assert db_lead.lead_id == "555_2"


# =====================================================================
# 7. Django Webhook Dispatcher Tests
# =====================================================================
@pytest.mark.asyncio
async def test_webhook_dispatcher_offline():
    from telegram_crawler.webhook import DjangoWebhookDispatcher

    dt = datetime.now(timezone.utc)
    lead = LeadContext(
        lead_id="g1_m1",
        group=GroupInfo(group_id=1, title="Test Group", username="testgrp"),
        target_message=MessageSnippet(message_id=1, text="خریدارم", date=dt),
        user=UserProfile(user_id=10, first_name="User10"),
        previous_messages=[],
        reply_thread=ReplyThread(),
        detected_at=dt,
    )

    # Use a dummy non-existent port to test offline fallback
    dispatcher = DjangoWebhookDispatcher(url="http://127.0.0.1:59999/dummy/", timeout=0.5)
    # Should safely return False without throwing exception
    result = await dispatcher.send_lead(lead)
    assert result is False


@pytest.mark.asyncio
async def test_webhook_dispatcher_success(tmp_path: Path, monkeypatch):
    from telegram_crawler.webhook import DjangoWebhookDispatcher, build_django_payload

    db_file = str(tmp_path / "webhook_leads.db")
    init_db(db_file)

    dt = datetime.now(timezone.utc)
    lead = LeadContext(
        lead_id="g1_m2",
        group=GroupInfo(group_id=1, title="Test Group", username="testgrp"),
        target_message=MessageSnippet(message_id=2, text="روغن موتور کاسترول خریدارم", date=dt),
        user=UserProfile(user_id=20, first_name="Ali", username="ali20", phone="+989123456789"),
        previous_messages=[],
        reply_thread=ReplyThread(),
        detected_at=dt,
    )
    save_lead(lead, is_synced=False, db_path=db_file)

    payload = build_django_payload(lead)
    assert payload["channel"] == "TELEGRAM"
    assert payload["lead_handle"] == "@ali20"
    assert payload["phone"] == "+989123456789"
    assert "روغن موتور" in payload["content_snippet"]

    # Mock _http_post_sync
    import telegram_crawler.webhook as wh_mod
    monkeypatch.setattr(wh_mod, "_http_post_sync", lambda url, p, timeout: {"status": "success", "lead_id": 123})

    dispatcher = DjangoWebhookDispatcher(
        url="http://localhost:8000/discovery/api/leads/submit/",
        db_path=db_file,
    )
    success = await dispatcher.send_lead(lead)
    assert success is True



"""Archive-only crawler: backfill, resume, multiple groups, FloodWait, live messages."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import FloodWaitError

from telegram_crawler import db as local_db
from telegram_crawler.models import GroupInfo
from telegram_crawler.monitor import CrawlerMonitor
from telegram_crawler.ratelimit import FloodWaitTooLong, with_flood_retry
from telegram_crawler.telegram_client import parse_group_link


def make_msg(mid: int, text: str, sender_id: int = 10, reply_to: int | None = None, minutes_ago: int = 5, bot: bool = False):
    sender = MagicMock(id=sender_id, first_name=f"U{sender_id}", last_name=None, username=f"user{sender_id}", bot=bot)
    reply = MagicMock(reply_to_msg_id=reply_to) if reply_to else None
    return MagicMock(id=mid, sender_id=sender_id, sender=sender, message=text, reply_to=reply,
                     date=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))


class FakeClient:
    """Minimal async Telethon stand-in. ``history`` maps entity.id -> messages."""

    def __init__(self, history: dict[int, list]):
        self.history = history
        self.flood_once = False

    async def iter_messages(self, entity, limit=200, min_id=0, offset_id=0, **kwargs):
        count = 0
        for m in sorted(self.history[entity.id], key=lambda m: m.id, reverse=True):
            if m.id <= min_id or (offset_id and m.id >= offset_id):
                continue
            if self.flood_once and count == 2:
                self.flood_once = False
                raise FloodWaitError(request=None, capture=0)
            yield m
            count += 1
            if count >= limit:
                return


def entity(eid: int, title: str):
    return MagicMock(id=eid, title=title, username=None)


def monitor(client, db_path, **kw):
    return CrawlerMonitor(client, [], backfill_hours=24, backfill_limit=kw.pop("limit", 100), db_path=db_path, **kw)


async def test_backfill_archives_every_message_in_order(tmp_path):
    db_path = str(tmp_path / "crawler.db")
    history = [make_msg(1, "سلام"), make_msg(2, "کسی دوره پایتون میشناسه؟", 2), make_msg(3, "👍", 3),
               make_msg(4, "تبلیغ", 5, bot=True), make_msg(5, "مکتب‌خونه", 4, reply_to=2)]
    mon = monitor(FakeClient({100: history}), db_path)
    mon.add_resolved_group(entity(100, "Python IR"), GroupInfo(group_id=100, title="Python IR"))

    assert await mon.run_backfill() == 5                                   # no filtering: need_engine decides
    with local_db.get_connection(db_path) as conn:
        rows = conn.execute("SELECT msg_id, sender_is_bot, reply_to_msg_id FROM messages ORDER BY id").fetchall()
        gm = conn.execute("SELECT title, last_scanned_msg_id FROM group_monitors").fetchone()
    assert [r["msg_id"] for r in rows] == [1, 2, 3, 4, 5]                  # oldest first → ids follow the conversation
    assert rows[3]["sender_is_bot"] == 1 and rows[4]["reply_to_msg_id"] == 2
    assert (gm["title"], gm["last_scanned_msg_id"]) == ("Python IR", 5)


async def test_resume_skips_already_archived_messages(tmp_path):
    db_path = str(tmp_path / "crawler.db")
    history = [make_msg(i, f"پیام {i}", i) for i in range(1, 4)]
    client = FakeClient({7: history})
    first = monitor(client, db_path)
    first.add_resolved_group(entity(7, "G"), GroupInfo(group_id=7, title="G"))
    assert await first.run_backfill() == 3

    history.append(make_msg(4, "پیام جدید", 4))
    second = monitor(client, db_path)
    state = second.add_resolved_group(entity(7, "G"), GroupInfo(group_id=7, title="G"))
    assert state.resume_from == 3
    assert await second.run_backfill() == 1
    assert local_db.count_local_messages(7, db_path=db_path) == 4


async def test_multiple_groups_and_flood_wait_during_backfill(tmp_path, monkeypatch):
    import telegram_crawler.monitor as mon_mod

    monkeypatch.setattr(mon_mod.asyncio, "sleep", AsyncMock())
    db_path = str(tmp_path / "crawler.db")
    client = FakeClient({1: [make_msg(i, f"A{i}", i) for i in range(1, 6)], 2: [make_msg(i, f"B{i}", i) for i in range(1, 3)]})
    client.flood_once = True
    mon = monitor(client, db_path)
    mon.add_resolved_group(entity(1, "A"), GroupInfo(group_id=1, title="A"))
    mon.add_resolved_group(entity(2, "B"), GroupInfo(group_id=2, title="B"))
    assert await mon.run_backfill() == 7                                   # FloodWait mid-scan resumed without losses
    assert local_db.count_local_messages(1, db_path=db_path) == 5


async def test_live_messages_are_archived_once(tmp_path):
    db_path = str(tmp_path / "crawler.db")
    mon = monitor(FakeClient({9: []}), db_path)
    state = mon.add_resolved_group(entity(9, "Live"), GroupInfo(group_id=9, title="Live", username="livegrp"))
    assert await mon.handle_live_message(state, make_msg(20, "دنبال یه عینک هستم", 1)) is True
    assert await mon.handle_live_message(state, make_msg(21, "منم", 2, reply_to=20)) is True
    assert await mon.handle_live_message(state, make_msg(20, "دنبال یه عینک هستم", 1)) is False   # re-delivery
    assert local_db.get_local_message(9, 21, db_path=db_path).reply_to_msg_id == 20
    assert mon.stats["archived"] == 2


async def test_with_flood_retry_sleeps_then_succeeds_or_gives_up():
    calls, slept = {"n": 0}, []

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise FloodWaitError(request=None, capture=4)
        return "ok"

    async def fake_sleep(s):
        slept.append(s)

    assert await with_flood_retry(lambda: flaky(), sleep=fake_sleep, max_retries=2, max_wait=60) == "ok"
    assert slept == [5]

    async def too_long():
        raise FloodWaitError(request=None, capture=5000)

    with pytest.raises(FloodWaitTooLong):
        await with_flood_retry(lambda: too_long(), sleep=fake_sleep, max_wait=60)


def test_parse_group_link():
    assert parse_group_link("https://t.me/+AbCdEfGh123") == ("invite", "AbCdEfGh123")
    assert parse_group_link("https://t.me/joinchat/XyZ12345") == ("invite", "XyZ12345")
    assert parse_group_link("https://t.me/my_supergroup") == ("public", "my_supergroup")
    assert parse_group_link("@my_channel") == ("public", "my_channel")
    assert parse_group_link("-1001234567890") == ("id", "-1001234567890")

"""Milestone 1: archive-all, local context, resume, multi-group, flood-wait, live replies."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from telethon.errors import FloodWaitError

from analysis.store import AnalysisStore
from telegram_crawler import db as local_db
from telegram_crawler.detector import BasicMessageFilter
from telegram_crawler.models import GroupInfo
from telegram_crawler.monitor import CrawlerMonitor
from telegram_crawler.ratelimit import FloodWaitTooLong, with_flood_retry


def make_msg(mid: int, text: str, sender_id: int = 10, reply_to: int | None = None, minutes_ago: int = 5, bot: bool = False):
    sender = MagicMock(id=sender_id, first_name=f"U{sender_id}", last_name=None, username=f"user{sender_id}", bot=bot, premium=False)
    reply = MagicMock(reply_to_msg_id=reply_to) if reply_to else None
    return MagicMock(
        id=mid, sender_id=sender_id, sender=sender, message=text, reply_to=reply,
        date=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


class FakeClient:
    """Minimal async Telethon stand-in. ``history`` maps entity.id -> messages (any order)."""

    def __init__(self, history: dict[int, list]):
        self.history = history
        self.remote_calls = 0
        self.flood_once = False

    async def iter_messages(self, entity, limit=200, min_id=0, offset_id=0, **kwargs):
        msgs = sorted(self.history[entity.id], key=lambda m: m.id, reverse=True)
        count = 0
        for m in msgs:
            if m.id <= min_id or (offset_id and m.id >= offset_id):
                continue
            if self.flood_once and count == 2:
                self.flood_once = False
                raise FloodWaitError(request=None, capture=0)
            yield m
            count += 1
            if count >= limit:
                return

    async def get_messages(self, entity, **kwargs):
        self.remote_calls += 1
        return [] if "ids" not in kwargs else None


def entity(eid: int, title: str):
    return MagicMock(id=eid, title=title, username=None)


@pytest.fixture
def paths(tmp_path: Path):
    return str(tmp_path / "crawler.db"), AnalysisStore(str(tmp_path / "analysis.sqlite3"))


def build_monitor(client, db_path, store, **kw):
    return CrawlerMonitor(client, [], detector=BasicMessageFilter(min_chars=8), backfill_hours=24,
                          backfill_limit=kw.pop("limit", 100), context_msg_count=3, db_path=db_path,
                          analysis_store=store, **kw)


async def test_backfill_archives_all_and_enqueues_with_local_context(paths):
    db_path, store = paths
    history = [
        make_msg(1, "سلام بچه‌ها صبح بخیر به همگی", 1),
        make_msg(2, "کسی دوره خوب پایتون برای تحلیل داده میشناسه؟", 2),
        make_msg(3, "مرسی", 3),                                   # too short -> archived, not a candidate
        make_msg(4, "من مکتب‌خونه رو دیدم ولی پشتیبانی نداشت", 4, reply_to=2),
        make_msg(5, "این بات تبلیغات است لطفا عضو شوید", 5, bot=True),
    ]
    client = FakeClient({100: history})
    mon = build_monitor(client, db_path, store)
    mon.add_resolved_group(entity(100, "Python IR"), GroupInfo(group_id=100, title="Python IR", is_supergroup=True))

    found = await mon.run_backfill()

    assert local_db.count_local_messages(100, db_path=db_path) == 5      # every message archived
    assert found == 3                                                    # 1, 2, 4 (3 short, 5 bot)
    q = store.get_message("telegram:100:2")
    assert q.text.startswith("کسی دوره")
    assert [c.id for c in q.context_of("previous")] == ["1"]            # built locally
    assert [c.id for c in q.context_of("reply")] == ["4"]               # later reply in the window
    assert q.url == "https://t.me/c/100/2"
    assert store.get_message("telegram:100:4").context_of("parent")[0].id == "2"
    assert store.get_message("telegram:100:5") is None
    assert client.remote_calls <= 2                                     # context came from the archive


async def test_resume_skips_already_scanned_messages(paths):
    db_path, store = paths
    history = [make_msg(i, f"پیام شماره {i} درباره خرید لپ تاپ", i) for i in range(1, 4)]
    client = FakeClient({7: history})
    first = build_monitor(client, db_path, store)
    first.add_resolved_group(entity(7, "G"), GroupInfo(group_id=7, title="G"))
    assert await first.run_backfill() == 3

    history.append(make_msg(4, "یک پیام جدید درباره خرید مانیتور", 4))
    second = build_monitor(client, db_path, store)
    state = second.add_resolved_group(entity(7, "G"), GroupInfo(group_id=7, title="G"))
    assert state.resume_from == 3
    assert await second.run_backfill() == 1
    assert store.stats()["inbox"]["pending"] == 4


async def test_multiple_groups_and_flood_wait_during_backfill(paths, monkeypatch):
    db_path, store = paths
    import telegram_crawler.monitor as mon_mod

    monkeypatch.setattr(mon_mod.asyncio, "sleep", AsyncMock())
    client = FakeClient({
        1: [make_msg(i, f"گروه یک پیام شماره {i} طولانی", i) for i in range(1, 6)],
        2: [make_msg(i, f"گروه دو پیام شماره {i} طولانی", i) for i in range(1, 3)],
    })
    client.flood_once = True
    mon = build_monitor(client, db_path, store)
    mon.add_resolved_group(entity(1, "A"), GroupInfo(group_id=1, title="A"))
    mon.add_resolved_group(entity(2, "B"), GroupInfo(group_id=2, title="B"))
    assert await mon.run_backfill() == 7          # FloodWait mid-scan resumed without losing messages
    assert local_db.count_local_messages(1, db_path=db_path) == 5


async def test_live_reply_is_attached_to_pending_message(paths):
    db_path, store = paths
    client = FakeClient({9: []})
    mon = build_monitor(client, db_path, store)
    state = mon.add_resolved_group(entity(9, "Live"), GroupInfo(group_id=9, title="Live", username="livegrp"))

    await mon.handle_live_message(state, make_msg(20, "دنبال یه عینک برای کار با لپ‌تاپ هستم", 1))
    await mon.handle_live_message(state, make_msg(21, "منم همین مشکل رو دارم چشمام میسوزه", 2, reply_to=20))

    q = store.get_message("telegram:9:20")
    assert q.url == "https://t.me/livegrp/20"
    assert [c.id for c in q.context_of("reply")] == ["21"]
    assert mon.stats["late_replies_attached"] == 1
    # re-delivering the same message does not enqueue twice
    await mon.handle_live_message(state, make_msg(20, "دنبال یه عینک برای کار با لپ‌تاپ هستم", 1))
    assert mon.stats["enqueued"] == 2


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


async def test_basic_filter():
    f = BasicMessageFilter(min_chars=8)
    assert await f("کسی سراغ داره دوره پایتون؟") is True
    assert await f("👍👍") is False
    assert await f("مرسی") is False
    assert await f("یک پیام از بات تبلیغاتی", {"is_bot": True}) is False

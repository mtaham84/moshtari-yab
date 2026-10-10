"""Crawler → PostgreSQL archive with real Telethon objects: users, chats, parents, media, resume, FloodWait, live."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon import utils
from telethon.errors import FloodWaitError
from telethon.tl import types

from telegram_crawler.db import Archive
from telegram_crawler.monitor import CrawlerMonitor
from telegram_crawler.ratelimit import FloodWaitTooLong, with_flood_retry
from telegram_crawler.telegram_client import parse_group_link

NOW = datetime.now(timezone.utc)


def channel(cid: int, title: str, username: str | None = None, megagroup: bool = True) -> types.Channel:
    return types.Channel(id=cid, title=title, photo=types.ChatPhotoEmpty(), date=NOW, megagroup=megagroup,
                         broadcast=not megagroup, username=username, access_hash=cid * 3)


def user(uid: int, username: str | None = None, bot: bool = False, first: str | None = None) -> types.User:
    return types.User(id=uid, first_name=first or f"U{uid}", last_name="L" if uid % 2 else None, username=username,
                      bot=bot, premium=uid == 11, access_hash=uid * 7, lang_code="fa")


class FakeClient:
    """Async Telethon stand-in. ``history[chat]`` = messages visible to iter_messages, ``old[chat]`` = older ones."""

    _self_id = 999
    _mb_entity_cache = None

    def __init__(self, entities: list):
        self.entities = {utils.get_peer_id(e): e for e in entities}
        self.history: dict[int, list] = {}
        self.old: dict[int, list] = {}
        self.flood_once = False
        self.get_messages_calls: list[list[int]] = []
        self.bios = {11: "فروشگاه لوازم موتور در تهران"}

    def msg(self, chat: types.Channel, mid: int, text: str = "", uid: int | None = 10, reply_to: int | None = None,
            minutes_ago: int = 5, cls=types.Message, **kw):
        from_id = kw.pop("from_id", types.PeerUser(uid) if uid else None)
        m = cls(id=mid, peer_id=types.PeerChannel(chat.id), date=NOW - timedelta(minutes=minutes_ago),
                from_id=from_id, reply_to=types.MessageReplyHeader(reply_to_msg_id=reply_to) if reply_to else None,
                **({"message": text} if cls is types.Message else {}), **kw)
        m._finish_init(self, self.entities, None)
        return m

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

    async def get_messages(self, entity, ids):
        self.get_messages_calls.append(list(ids))
        pool = {m.id: m for m in self.history.get(entity.id, []) + self.old.get(entity.id, [])}
        return [pool.get(i) for i in ids]

    async def __call__(self, request):
        uid = getattr(request.id, "user_id", request.id)
        if uid not in self.bios:
            raise ValueError("USER_PRIVACY_RESTRICTED")
        return SimpleNamespace(full_user=SimpleNamespace(about=self.bios[uid]))


@pytest.fixture
def archive(pg_dsn, pg_schema):
    a = Archive(pg_dsn, pg_schema("crawler"))
    yield a
    a.close()


def rows(archive: Archive, sql: str, *params):
    return archive.conn.execute(sql.replace("{s}", archive.schema), params).fetchall()


def monitor(client, archive, **kw):
    return CrawlerMonitor(client, [], backfill_hours=24, backfill_limit=kw.pop("limit", 100), archive=archive,
                          fetch_profiles=kw.pop("fetch_profiles", False), **kw)


async def test_backfill_stores_users_chat_parents_and_message_details(archive):
    g = channel(100, "موتورسواران تهران", "motor_teh")
    ali, sara, bot, other = user(11, "ali"), user(12), user(13, "spam_bot", bot=True), channel(555, "کانال فروش", "shop_ch", megagroup=False)
    c = FakeClient([g, ali, sara, bot, other])
    c.old[100] = [c.msg(g, 3, "کسی دستکش زمستونی خوب سراغ داره؟", 12, minutes_ago=3000)]       # outside the 24h window
    photo = types.MessageMediaPhoto(photo=types.Photo(id=1, access_hash=2, file_reference=b"x", date=NOW, dc_id=1,
                                                      sizes=[types.PhotoSize(type="x", w=800, h=600, size=51200)]))
    c.history[100] = [
        c.msg(g, 10, "سلام", 11),
        c.msg(g, 11, "من دنبال دستکش گرم موتورم بودجه ۱ میلیون", 11, reply_to=3,
              entities=[types.MessageEntityUrl(offset=0, length=2)]),
        c.msg(g, 12, "این مدل خوبه؟", 12, reply_to=11, media=photo),
        c.msg(g, 13, "تبلیغ", 13, fwd_from=types.MessageFwdHeader(date=NOW, from_id=types.PeerChannel(555))),
        c.msg(g, 14, cls=types.MessageService, uid=12, action=types.MessageActionChatAddUser(users=[12])),
        c.msg(g, 15, "پیام ادمین ناشناس", uid=None, from_id=types.PeerChannel(100), post_author="admin"),
    ]
    mon = monitor(c, archive, parent_depth=3)
    full = types.ChannelFull(id=100, about="گروه موتورسواران", read_inbox_max_id=0, read_outbox_max_id=0, unread_count=0,
                             chat_photo=types.PhotoEmpty(id=0), notify_settings=types.PeerNotifySettings(), bot_info=[], pts=1,
                             participants_count=4200)
    state = mon.add_resolved_group(g, "https://t.me/motor_teh", full)

    assert await mon.run_backfill() == 6
    assert mon.stats["context"] == 1 and c.get_messages_calls == [[3]]           # one batched request for old parents

    chat = archive.get_chat(state.chat_id)
    assert state.chat_id == -1000000000100
    assert (chat["type"], chat["username"], chat["about"], chat["members_count"], chat["is_monitored"]) == \
        ("supergroup", "motor_teh", "گروه موتورسواران", 4200, True)
    assert chat["last_scanned_msg_id"] == 15

    users = {r["user_id"]: r for r in rows(archive, "SELECT * FROM {s}.tg_users")}
    assert set(users) == {11, 12, 13}
    assert (users[11]["username"], users[11]["first_name"], users[11]["is_premium"], users[11]["lang_code"]) == ("ali", "U11", True, "fa")
    assert users[13]["is_bot"] is True and users[12]["username"] is None and users[11]["raw"]["id"] == 11

    msgs = rows(archive, "SELECT * FROM {s}.tg_messages ORDER BY id")
    assert [m["message_id"] for m in msgs] == [3, 10, 11, 12, 13, 14, 15]           # parent first, then conversation order
    by_id = {m["message_id"]: m for m in msgs}
    assert by_id[3]["is_context"] is True and by_id[3]["sender_user_id"] == 12
    assert by_id[11]["reply_to_msg_id"] == 3 and by_id[11]["entities"][0]["type"] == "Url"
    assert by_id[12]["media_type"] == "photo" and by_id[12]["media"]["width"] == 800 and by_id[12]["text"] == "این مدل خوبه؟"
    assert by_id[13]["fwd_from_id"] == -1000000000555
    assert by_id[14]["is_service"] is True and by_id[14]["service_action"] == "ChatAddUser"
    assert by_id[15]["sender_user_id"] is None and by_id[15]["sender_chat_id"] == state.chat_id and by_id[15]["post_author"] == "admin"
    assert all(m["raw"]["id"] == m["message_id"] for m in msgs)


async def test_user_is_a_snapshot_and_placeholders_are_completed(archive):
    g = channel(7, "G")
    c = FakeClient([g, user(21, "first_name_handle")])
    mon = monitor(c, archive)
    state = mon.add_resolved_group(g)
    unknown = c.msg(g, 1, "sender entity missing", 22)                       # Telegram gave no entity for user 22
    assert await mon.handle_live_message(state, unknown)
    assert archive.get_user(22)["first_name"] is None                         # placeholder keeps the FK valid

    c.entities[22] = user(22, "now_known")
    await mon.handle_live_message(state, c.msg(g, 2, "hi", 22))
    assert archive.get_user(22)["username"] == "now_known"                    # placeholder filled once

    await mon.handle_live_message(state, c.msg(g, 3, "a", 21))
    c.entities[21] = user(21, "renamed_later", first="Changed")
    await mon.handle_live_message(state, c.msg(g, 4, "b", 21))
    u = archive.get_user(21)
    assert (u["username"], u["first_name"]) == ("first_name_handle", "U21")   # stored once, not overwritten


async def test_live_reply_fetches_missing_parent_once_and_redelivery_is_ignored(archive):
    g = channel(9, "Live", "livegrp")
    c = FakeClient([g, user(1), user(2)])
    c.old[9] = [c.msg(g, 5, "دنبال عینک آفتابی‌ام", 1, minutes_ago=600)]
    mon = monitor(c, archive)
    state = mon.add_resolved_group(g)
    assert await mon.handle_live_message(state, c.msg(g, 20, "از کجا بخرم؟", 2, reply_to=5)) is True
    assert await mon.handle_live_message(state, c.msg(g, 21, "منم", 1, reply_to=20)) is True
    assert await mon.handle_live_message(state, c.msg(g, 20, "از کجا بخرم؟", 2, reply_to=5)) is False
    assert c.get_messages_calls == [[5]]
    assert archive.get_message(state.chat_id, 5)["is_context"] is True
    assert archive.get_message(state.chat_id, 21)["reply_to_msg_id"] == 20
    assert mon.stats["archived"] == 2 and archive.count_messages(state.chat_id) == 3


async def test_resume_skips_already_archived_messages(archive):
    g = channel(7, "G")
    c = FakeClient([g] + [user(i) for i in range(1, 5)])
    c.history[7] = [c.msg(g, i, f"پیام {i}", i) for i in range(1, 4)]
    first = monitor(c, archive)
    first.add_resolved_group(g)
    assert await first.run_backfill() == 3

    c.history[7].append(c.msg(g, 4, "پیام جدید", 4))
    second = monitor(c, archive)
    state = second.add_resolved_group(g)
    assert state.resume_from == 3
    assert await second.run_backfill() == 1
    assert archive.count_messages(state.chat_id) == 4


async def test_multiple_groups_and_flood_wait_during_backfill(archive, monkeypatch):
    import telegram_crawler.monitor as mon_mod

    monkeypatch.setattr(mon_mod.asyncio, "sleep", AsyncMock())
    a, b = channel(1, "A"), channel(2, "B")
    c = FakeClient([a, b] + [user(i) for i in range(1, 6)])
    c.history = {1: [c.msg(a, i, f"A{i}", i) for i in range(1, 6)], 2: [c.msg(b, i, f"B{i}", i) for i in range(1, 3)]}
    c.flood_once = True
    mon = monitor(c, archive)
    sa, _ = mon.add_resolved_group(a), mon.add_resolved_group(b)
    assert await mon.run_backfill() == 7                                   # FloodWait mid-scan resumed without losses
    assert archive.count_messages(sa.chat_id) == 5


async def test_profile_worker_fills_bio_once(archive, monkeypatch):
    import telegram_crawler.monitor as mon_mod

    monkeypatch.setattr(mon_mod.asyncio, "sleep", AsyncMock())
    g = channel(3, "G")
    c = FakeClient([g, user(11, "ali"), user(12)])
    mon = monitor(c, archive, fetch_profiles=True)
    state = mon.add_resolved_group(g)
    await mon.handle_live_message(state, c.msg(g, 1, "x", 11))
    await mon.handle_live_message(state, c.msg(g, 2, "y", 12))
    assert await mon.fetch_profiles_once() == 2
    assert archive.get_user(11)["bio"] == "فروشگاه لوازم موتور در تهران"
    assert archive.get_user(12)["bio"] is None and archive.get_user(12)["profile_fetched_at"] is not None  # privacy → not retried
    assert await mon.fetch_profiles_once() == 0


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


# ── panel («جوامع آنلاین») → crawler ────────────────────────────────────────────
PANEL_DDL = """CREATE TABLE {t} (id serial PRIMARY KEY, platform text NOT NULL DEFAULT 'telegram', name text NOT NULL,
    handle_or_link text NOT NULL, is_active boolean NOT NULL DEFAULT TRUE, members_count integer NOT NULL DEFAULT 0,
    messages_scanned_count integer NOT NULL DEFAULT 0, leads_discovered_count integer NOT NULL DEFAULT 0,
    last_scanned_at timestamptz, telegram_chat_id bigint, sync_status varchar(10) NOT NULL DEFAULT 'PENDING',
    sync_error text NOT NULL DEFAULT '')"""


@pytest.fixture
def panel(archive):
    from telegram_crawler.panel import PanelCommunities

    table = f"{archive.schema}.discovery_monitoredcommunity"
    archive.conn.execute(PANEL_DDL.format(t=table))
    return PanelCommunities(archive.conn, table=table, crawler_schema=archive.schema)


def panel_rows(panel):
    return {r["handle_or_link"]: r for r in panel.conn.execute(f"SELECT * FROM {panel.table}").fetchall()}


async def test_panel_communities_join_pause_resume_without_restart(archive, panel, monkeypatch):
    import telegram_crawler.monitor as mon_mod
    from telegram_crawler.panel import normalize_link

    monkeypatch.setattr(mon_mod, "pace", AsyncMock())
    g, other = channel(31, "موتورسواران تهران", "motor_tehran"), channel(32, "Other", "other_grp")
    c = FakeClient([g, other] + [user(i) for i in range(1, 6)])
    c.history = {31: [c.msg(g, i, f"پیام {i}", i) for i in range(1, 4)], 32: [c.msg(other, 1, "x", 1)]}
    joins: list[str] = []

    async def fake_join(client, link):
        joins.append(link)
        key = normalize_link(link)
        if key == "motor_tehran":
            return g, None
        if key == "other_grp":
            return other, None
        raise ValueError("No user has \"%s\" as username" % key)

    monkeypatch.setattr(mon_mod, "join_and_resolve_group", fake_join)
    monkeypatch.setattr(mon_mod, "fetch_full_chat", AsyncMock(return_value=SimpleNamespace(participants_count=1234)))
    handlers = []
    c.on = lambda event: (lambda fn: handlers.append(fn) or fn)

    ins = f"INSERT INTO {panel.table} (name, handle_or_link, is_active) VALUES (%s, %s, %s)"
    for row in [("", "@motor_tehran", True), ("dup", "https://t.me/Motor_Tehran/", True),
                ("bad", "@no_such_group", True), ("off", "@other_grp", False)]:
        archive.conn.execute(ins, row)

    mon = monitor(c, archive, panel=panel, join_interval=0)
    await mon.run_backfill()                      # no CLI groups: nothing to do
    assert await mon.sync_panel() == 1            # one join for both spellings, bad link → error, inactive ignored
    assert joins == ["@motor_tehran", "@no_such_group"]
    rows = panel_rows(panel)
    a = rows["@motor_tehran"]
    assert (a["sync_status"], a["telegram_chat_id"], a["members_count"], a["name"]) == ("ACTIVE", utils.get_peer_id(g), 1234, "موتورسواران تهران")
    assert rows["https://t.me/Motor_Tehran/"]["telegram_chat_id"] == a["telegram_chat_id"]
    assert rows["https://t.me/Motor_Tehran/"]["name"] == "dup"                 # a name typed by the seller is kept
    assert (rows["@no_such_group"]["sync_status"], rows["@no_such_group"]["sync_error"]) == ("ERROR", "INVALID_LINK")
    assert rows["@other_grp"]["sync_status"] == "PENDING" and rows["@other_grp"]["telegram_chat_id"] is None
    assert a["messages_scanned_count"] == 3 and a["last_scanned_at"] is not None
    assert archive.count_messages(utils.get_peer_id(g)) == 3

    # a bad link is not retried until the seller re-activates it
    assert await mon.sync_panel() == 0 and joins.count("@no_such_group") == 1

    # live messages are archived through one catch-all handler; unknown chats are ignored
    await mon.start_live_monitoring()
    (handler,) = handlers
    live = c.msg(g, 4, "کسی تعمیرکار خوب سراغ داره؟", 2)
    await handler(SimpleNamespace(chat_id=utils.get_peer_id(g), message=live))
    await handler(SimpleNamespace(chat_id=-100999, message=c.msg(other, 2, "y", 1)))
    assert archive.count_messages(utils.get_peer_id(g)) == 4

    # «توقف پایش» on both rows → paused; messages are skipped
    archive.conn.execute(f"UPDATE {panel.table} SET is_active = FALSE WHERE telegram_chat_id IS NOT NULL")
    await mon.sync_panel()
    assert utils.get_peer_id(g) in mon.paused
    assert panel_rows(panel)["@motor_tehran"]["sync_status"] == "PAUSED"
    missed = c.msg(g, 5, "پیام وقت توقف", 3)
    c.history[31].append(missed)
    await handler(SimpleNamespace(chat_id=utils.get_peer_id(g), message=missed))
    assert archive.count_messages(utils.get_peer_id(g)) == 4

    # «فعال‌سازی» → active again and the missed message is fetched by a catch-up backfill
    archive.conn.execute(f"UPDATE {panel.table} SET is_active = TRUE WHERE handle_or_link = '@motor_tehran'")
    await mon.sync_panel()
    assert not mon.paused
    assert panel_rows(panel)["@motor_tehran"]["sync_status"] == "ACTIVE"
    assert archive.count_messages(utils.get_peer_id(g)) == 5
    assert panel_rows(panel)["@motor_tehran"]["messages_scanned_count"] == 5


async def test_panel_join_queue_flood_wait_limit_and_restart(archive, panel, monkeypatch):
    import telegram_crawler.monitor as mon_mod
    from telethon.errors import FloodWaitError

    from telegram_crawler.panel import normalize_link

    monkeypatch.setattr(mon_mod, "pace", AsyncMock())
    monkeypatch.setattr(mon_mod, "fetch_full_chat", AsyncMock(return_value=None))
    chans = {f"grp_{i}": channel(40 + i, f"G{i}", f"grp_{i}") for i in range(4)}
    c = FakeClient(list(chans.values()))
    c.history = {ch.id: [] for ch in chans.values()}
    joins: list[str] = []
    flood = {"once": True}

    async def fake_join(client, link):
        key = normalize_link(link)
        joins.append(key)
        if key == "grp_1" and flood["once"]:
            flood["once"] = False
            raise FloodWaitError(request=None, capture=120)
        return chans[key], None

    monkeypatch.setattr(mon_mod, "join_and_resolve_group", fake_join)
    clock = {"t": 1000.0}
    monkeypatch.setattr(mon_mod.time, "monotonic", lambda: clock["t"])
    for key in chans:
        archive.conn.execute(f"INSERT INTO {panel.table} (name, handle_or_link) VALUES ('', %s)", (f"@{key}",))

    mon = monitor(c, archive, panel=panel, join_interval=60, max_groups=2)
    assert await mon.sync_panel() == 1 and joins == ["grp_0"]          # one join per interval, others queued
    assert panel_rows(panel)["@grp_1"]["sync_status"] == "PENDING"
    clock["t"] += 30
    assert await mon.sync_panel() == 0                                  # interval not over yet
    clock["t"] += 31
    assert await mon.sync_panel() == 0 and joins[-1] == "grp_1"         # FloodWait → code shown, every join waits
    assert (panel_rows(panel)["@grp_1"]["sync_status"], panel_rows(panel)["@grp_1"]["sync_error"]) == ("PENDING", "FLOOD_WAIT:120")
    clock["t"] += 100
    assert await mon.sync_panel() == 0 and len(joins) == 2
    clock["t"] += 30
    assert await mon.sync_panel() == 1 and joins[-1] == "grp_1"         # retried after the wait
    clock["t"] += 61
    assert await mon.sync_panel() == 0 and len(joins) == 3              # TG_MAX_GROUPS reached
    rows = panel_rows(panel)
    assert (rows["@grp_2"]["sync_status"], rows["@grp_2"]["sync_error"]) == ("PENDING", "LIMIT_REACHED")

    # restart: groups the account already belongs to are reused without joining again
    c.iter_dialogs = lambda: _aiter([SimpleNamespace(entity=chans["grp_0"]), SimpleNamespace(entity=chans["grp_1"])])
    mon2 = monitor(c, archive, panel=panel, join_interval=60, max_groups=3)
    assert await mon2.sync_panel() == 1 and joins[3:] == ["grp_2"]
    assert {st.chat_id for st in mon2.groups} == {utils.get_peer_id(chans[k]) for k in ("grp_0", "grp_1", "grp_2")}


async def _aiter(items):
    for x in items:
        yield x


def test_join_errors_are_mapped_to_panel_codes():
    from telethon import errors as tg

    from telegram_crawler.panel import classify_join_error
    from telegram_crawler.ratelimit import FloodWaitTooLong
    from telegram_crawler.telegram_client import JoinError

    cases = [
        (ValueError('No user has "x" as username'), "INVALID_LINK"),
        (tg.UsernameNotOccupiedError(request=None), "INVALID_LINK"),
        (tg.InviteHashExpiredError(request=None), "NO_ACCESS"),
        (tg.ChannelPrivateError(request=None), "NO_ACCESS"),
        (tg.UserBannedInChannelError(request=None), "BANNED"),
        (tg.ChannelsTooMuchError(request=None), "LIMIT_REACHED"),
        (JoinError("NOT_A_GROUP"), "NOT_A_GROUP"),
        (FloodWaitTooLong("x", 900), "FLOOD_WAIT:900"),
        (RuntimeError("boom"), "UNKNOWN:boom"),
    ]
    for exc, code in cases:
        assert classify_join_error(exc)[0] == code, exc


async def test_panel_table_missing_is_not_an_error(archive):
    from telegram_crawler.panel import PanelCommunities

    p = PanelCommunities(archive.conn, table=f"{archive.schema}.not_migrated_yet", crawler_schema=archive.schema)
    assert p.communities() == []


def test_normalize_link():
    from telegram_crawler.panel import normalize_link

    assert {normalize_link(x) for x in ["@Motor", "motor", "https://t.me/motor/", "t.me/Motor", "telegram.me/motor"]} == {"motor"}
    assert normalize_link("https://t.me/+AbCd") == "+AbCd"

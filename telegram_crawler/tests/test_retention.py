import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from telegram_crawler.monitor import CrawlerMonitor, GroupState
from telegram_crawler.retention import _scrub_stale_jsonl, scrub_engine_opportunity_payload


class MemoryArchive:
    def __init__(self):
        self.messages = []
        self.progress = []

    def add_message(self, record):
        self.messages.append(record)
        return True

    def save_progress(self, chat_id, message_id):
        self.progress.append((chat_id, message_id))


async def test_expired_live_message_is_skipped_and_progress_advances(monkeypatch):
    monkeypatch.setenv("DATA_RETENTION_DAYS", "30")
    archive = MemoryArchive()
    monitor = CrawlerMonitor(object(), archive=archive, parent_depth=0)
    state = GroupState(entity=object(), chat_id=42, title="group")
    message = SimpleNamespace(id=19, date=datetime.now(timezone.utc) - timedelta(days=31))

    assert await monitor.handle_live_message(state, message) is False

    assert archive.messages == []
    assert state.max_seen == 19
    assert archive.progress == [(42, 19)]


def test_expired_reply_context_is_saved_as_anonymous_stub(monkeypatch):
    monkeypatch.setenv("DATA_RETENTION_DAYS", "30")
    archive = MemoryArchive()
    monitor = CrawlerMonitor(object(), archive=archive, parent_depth=0)
    monitor.store_raw = True
    state = GroupState(entity=object(), chat_id=42, title="group")
    message = SimpleNamespace(
        id=7,
        date=datetime.now(timezone.utc) - timedelta(days=31),
        message="old buyer message",
        reply_to=None,
        fwd_from=None,
        from_id=None,
        media=None,
        entities=None,
    )

    assert monitor._store(state, message, is_context=True) is True

    saved, = archive.messages
    assert saved["is_context"] is True
    assert saved["text"] == ""
    assert saved["sender_user_id"] is None
    assert saved["raw"] is None
    assert monitor.stats["context"] == 1


def test_retention_payload_scrub_keeps_business_fields_and_removes_source_identity():
    payload = {
        "status": "open",
        "created_at": "2020-01-01T00:00:00+00:00",
        "candidate": {"external_user_id": "123", "username": "buyer", "contacts": ["@buyer"]},
        "source": {"platform": "x", "chat_id": "group-1", "evidence": [{"text": "private"}]},
        "need": {"label": "espresso machine", "priority": 4},
        "matched_products": [{"product_id": "coffee-1", "reply_draft": "hello"}],
        "thread": {"text": "private reply"},
    }

    clean = scrub_engine_opportunity_payload(payload)

    assert clean["retention_scrubbed"] is True
    assert clean["status"] == "expired"
    assert clean["candidate"]["external_user_id"] == ""
    assert clean["candidate"]["username"] == ""
    assert clean["source"]["chat_id"] == ""
    assert clean["source"]["evidence"] == []
    assert clean["need"]["label"] == ""
    assert clean["matched_products"][0]["product_id"] == "coffee-1"
    assert "reply_draft" not in clean["matched_products"][0]
    assert clean["thread"] is None
    assert payload["candidate"]["username"] == "buyer"


def test_retention_jsonl_scrub_removes_only_expired_records(tmp_path):
    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    expired = {"id": "1", "created_at": (cutoff - timedelta(days=1)).isoformat()}
    current = {"id": "2", "created_at": (cutoff + timedelta(days=1)).isoformat()}
    path = tmp_path / "posts.jsonl"
    original = (
        json.dumps(expired) + "\n" + json.dumps(current) + "\n"
    ).encode()
    path.write_bytes(original)

    assert _scrub_stale_jsonl(path, cutoff, delete=False) == 1
    assert path.read_bytes() == original
    assert _scrub_stale_jsonl(path, cutoff, delete=True) == 1

    remaining = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert remaining == [current]
    assert path.stat().st_size == len(original)

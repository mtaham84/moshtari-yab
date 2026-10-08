"""Milestone 1: shared inbox, dedup, replay, catalog and source adapters."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from analysis.catalog import load_catalog, parse_catalog
from analysis.config import AnalysisSettings, parse_model_prices
from analysis.schemas import AgentVerdict, Author, ContextMessage, SocialMessage, StageCost
from analysis.store import AnalysisStore
from sources.file_adapter import load_file
from sources.telegram_adapter import lead_context_to_social_message, telegram_message_url


def msg(uid: str, text: str = "دنبال یه دوره پایتون خوب هستم", author: str = "a1") -> SocialMessage:
    return SocialMessage(uid=uid, source="telegram", channel_id="1", text=text, author=Author(id=author))


@pytest.fixture
def store(tmp_path: Path) -> AnalysisStore:
    return AnalysisStore(str(tmp_path / "a.sqlite3"))


def test_enqueue_dedups_by_uid_and_same_author_copy(store):
    assert store.enqueue(msg("telegram:1:1")) is True
    assert store.enqueue(msg("telegram:1:1")) is False                     # same uid
    assert store.enqueue(msg("telegram:1:2")) is False                     # same author, same text (copy-paste)
    assert store.enqueue(msg("telegram:1:3", author="a2")) is True         # different author
    assert store.stats()["inbox"] == {"pending": 2}


def test_claim_release_error_and_replay(store):
    store.enqueue(msg("telegram:1:1"))
    store.enqueue(msg("telegram:1:2", text="یک پیام دیگر"))
    claimed = store.claim_pending(10)
    assert len(claimed) == 2 and store.claim_pending(10) == []
    store.release([claimed[0].uid], "timeout", max_attempts=1)               # attempts=1 -> error
    store.mark_done([claimed[1].uid])
    store.save_verdicts([AgentVerdict(message_uid=claimed[1].uid, source="telegram", reached_stage="prefilter",
                                      skip_reason="too_short", costs=[StageCost(stage="prefilter")])])
    assert store.stats()["inbox"] == {"error": 1, "done": 1}
    assert store.reset_for_replay(source="telegram") == 2
    assert store.stats()["inbox"] == {"pending": 2}
    assert store.list_verdicts() == []


def test_add_context_only_while_pending(store):
    store.enqueue(msg("telegram:1:1"))
    reply = ContextMessage(id="9", relation="reply", text="منم دنبالشم")
    assert store.add_context("telegram:1:1", reply) is True
    assert store.add_context("telegram:1:1", reply) is False                 # no duplicates
    store.claim_pending(1)
    assert store.add_context("telegram:1:1", ContextMessage(id="10", relation="reply", text="x")) is False


def test_recover_stale(store):
    store.enqueue(msg("telegram:1:1"))
    store.claim_pending(1)
    assert store.recover_stale() == 1


def test_catalog_accepts_django_feed_shape(tmp_path):
    feed = {"products": [{"id": 3, "name": "دوره پایتون", "target_customer": "مبتدی‌ها", "price": {"raw": "1500000"}, "url": ""}]}
    products = parse_catalog(feed, "https://shop.example")
    assert products[0].url == "https://shop.example/p/3/" and products[0].price == 1500000
    path = tmp_path / "c.json"
    path.write_text(json.dumps([{"product_id": 1, "name": "عینک", "url": "https://x/p/1/"}]), encoding="utf-8")
    assert load_catalog(AnalysisSettings(catalog_source=str(path)))[0].name == "عینک"


def test_parse_model_prices():
    assert parse_model_prices("llama-3.1-8b-instant:0.05:0.08, bad, m2:1:2") == {
        "llama-3.1-8b-instant": (0.05, 0.08), "m2": (1.0, 2.0)}


def test_file_adapter_jsonl_and_csv(tmp_path):
    jl = tmp_path / "x.jsonl"
    jl.write_text(
        json.dumps({"id": "171", "text": "چشمام از کار با لپ‌تاپ خیلی خسته میشه", "author_handle": "@sara",
                    "context": [{"id": "170", "relation": "parent", "text": "کسی راه حل داره؟"}]}, ensure_ascii=False) + "\n",
        encoding="utf-8")
    m = load_file(jl, "x")[0]
    assert m.uid == "x:171" and m.source == "x" and m.context_of("parent")[0].id == "170"
    csv_path = tmp_path / "m.csv"
    csv_path.write_text("id,text,source\n5,یه پیام تستی برای خرید,telegram\n", encoding="utf-8")
    assert load_file(csv_path)[0].uid == "telegram:5"


def test_telegram_adapter_and_links():
    from telegram_crawler.models import GroupInfo, LeadContext, MessageSnippet, ReplyThread, UserProfile

    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lead = LeadContext(
        lead_id="5_10", group=GroupInfo(group_id=5, title="G", is_supergroup=True),
        target_message=MessageSnippet(message_id=10, text="کسی سراغ داره؟", date=dt, reply_to_msg_id=8),
        user=UserProfile(user_id=77, first_name="Ali", username="ali"),
        previous_messages=[MessageSnippet(message_id=8, text="parent", date=dt), MessageSnippet(message_id=9, text="prev", date=dt)],
        reply_thread=ReplyThread(parent_messages=[MessageSnippet(message_id=8, text="parent", date=dt)],
                                 child_replies=[MessageSnippet(message_id=11, text="reply", date=dt)]),
    )
    m = lead_context_to_social_message(lead)
    assert m.uid == "telegram:5:10" and m.author.handle == "@ali" and m.url == "https://t.me/c/5/10"
    assert [(c.relation, c.id) for c in m.context] == [("parent", "8"), ("previous", "9"), ("reply", "11")]
    assert telegram_message_url(GroupInfo(group_id=5, title="G", is_supergroup=False), 10) is None

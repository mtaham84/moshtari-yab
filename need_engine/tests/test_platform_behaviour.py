from datetime import datetime, timezone
from types import SimpleNamespace

from need_engine.config import EngineConfig
from need_engine.engine import NeedEngine, RunReport, _message_url
from need_engine.schemas import ChatMessage, NeedCard
from need_engine.windowing import build_windows
from need_engine.x_filters import cheap_x_filter


NOW = datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc)


def message(chat_id, message_id, *, platform="telegram", text="دنبال کفش مناسب برای پیاده‌روی هستم", **kwargs):
    return ChatMessage(chat_id=chat_id, message_id=message_id, row_id=message_id, author_id="buyer",
                       text=text, date=NOW, platform=platform, **kwargs)


class WindowStore:
    def recent(self, chat_id, before_id, limit):
        return [message(chat_id, 1, text="پیام قبلی برای زمینه")]

    def message(self, chat_id, message_id):
        return None


def test_telegram_messages_keep_conversation_context_but_x_posts_stay_independent():
    cfg = EngineConfig(window_size=1, context_messages=3)
    store = WindowStore()
    telegram = build_windows("-100123", [message("-100123", 2)], store, cfg)[0]
    x_post = message("x:public", 2, platform="x")
    x_window = build_windows("x:public", [x_post], store, cfg)[0]

    assert [item.message_id for item in telegram.context] == [1]
    assert x_window.context == []


def test_x_prefilter_keeps_buyer_request_and_drops_promotion_while_telegram_has_no_prefilter():
    cfg = EngineConfig(x_min_chars=15)
    buyer = message("x:public", 1, platform="x")
    promotion = message("x:public", 2, platform="x",
                        text="فروش ویژه خرید کنید ارسال رایگان امروز https://shop.example")
    telegram = message("-100123", 3)

    assert cheap_x_filter(buyer, cfg).keep is True
    assert cheap_x_filter(promotion, cfg).keep is False
    assert not hasattr(telegram, "prefilter")


def test_message_urls_keep_telegram_fallbacks_and_explicit_x_url():
    assert _message_url(message("-100123", 55)) == "https://t.me/c/123/55"
    assert _message_url(message("-100123", 55, chat_username="publicgroup")) == "https://t.me/publicgroup/55"
    assert _message_url(message("x:public", 99, platform="x", url="https://x.com/buyer/status/99")) == \
        "https://x.com/buyer/status/99"
    assert _message_url(message("x:public", 99, platform="x")) is None


def test_only_x_opportunities_apply_x_seller_consent_and_match_floor():
    engine = NeedEngine.__new__(NeedEngine)
    engine.catalog = SimpleNamespace(
        pid_index={"enabled": 0, "disabled": 1},
        products=[SimpleNamespace(x_outreach_enabled=True), SimpleNamespace(x_outreach_enabled=False)],
    )
    engine.store = SimpleNamespace(need_is_scrubbed=lambda _need_id: False, update_need=lambda _need: None)
    engine.cfg = SimpleNamespace(max_products_per_opportunity=10, write_replies=False,
                                 x_min_match_score=65, reply_top_n=1, x_reply_top_n=1, x_thread_replies=False)
    emitted = []
    engine._opportunity = lambda _need, matches: matches
    engine._emit = lambda matches, _report: emitted.append(matches)
    matches = [SimpleNamespace(product_id="enabled", match_score=0.8),
               SimpleNamespace(product_id="disabled", match_score=0.9),
               SimpleNamespace(product_id="enabled", match_score=0.5)]

    telegram = NeedCard(need_id="tg-1", chat_id="-100123", author_id="buyer",
                        label="explicit_need", is_opportunity=True, created_at=NOW, updated_at=NOW)
    x_post = telegram.model_copy(update={"need_id": "x-1", "chat_id": "x:public"})
    engine._finish_and_emit(telegram, matches, RunReport())
    engine._finish_and_emit(x_post, matches, RunReport())

    assert emitted[0] == [matches[1], matches[0], matches[2]]
    assert emitted[1] == [matches[0]]

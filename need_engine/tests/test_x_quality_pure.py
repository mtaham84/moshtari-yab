from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from need_engine.config import EngineConfig
from need_engine.schemas import ChatMessage
from need_engine.x_filters import cheap_x_filter
from need_engine.x_replies import build_x_intent_url, fit_public_reply, sanitize_public_reply, x_weighted_length
from need_engine.contacts import extract_public_contacts
from need_engine.x_query_scoring import query_weight, score_query


def post(text, **kwargs):
    return ChatMessage(chat_id="x:public", message_id=1, author_id="x_user", text=text,
                       date=datetime.now(timezone.utc), platform="x", **kwargs)


def test_filter_keeps_explicit_and_on_behalf_intent_and_bio_alone():
    cfg = EngineConfig(x_min_chars=15)
    assert cheap_x_filter(post("برای مادرم دنبال ویلچر مناسب می‌گردم", author_bio="فروشگاه رسمی"), cfg).keep
    assert cheap_x_filter(post("دنبال کفش مدرسه برای بچه‌ام هستم", author_bio="shop"), cfg).keep


def test_filter_detects_promotions_short_and_language():
    cfg = EngineConfig(x_min_chars=15)
    assert cheap_x_filter(post("فروش ویژه خرید کنید ارسال رایگان https://x.test"), cfg).reason in {"shop_bio", "promotional"}
    assert cheap_x_filter(post("سلام"), cfg).reason == "too_short"
    assert cheap_x_filter(post("I need a new laptop", lang="ar"), cfg).reason == "lang"


def test_reply_sanitizing_weights_fallback_and_intent_link():
    assert "https://" not in sanitize_public_reply("سلام https://x.test 09123456789 a@b.com {{LINK}}")
    assert x_weighted_length("می‌خوام https://x.test") >= 23
    assert fit_public_reply("", 240)
    url = build_x_intent_url("https://x.com/intent/post", "123", "سلام خرید")
    assert parse_qs(urlparse(url).query)["text"] == ["سلام خرید"]
    assert build_x_intent_url("https://x.com/intent/post", None, "متن") is None


def test_contacts_require_platform_context_and_never_turn_email_into_website():
    contacts = extract_public_contacts("تلگرام: @my_public_id email me name@gmail.com instagram.com/p/post t.me/joinchat/abc",
                                      types=("telegram", "instagram", "website", "email"))
    assert [(item.type, item.value) for item in contacts] == [("telegram", "my_public_id"), ("email", "name@gmail.com")]


def test_query_score_prior_and_weight_clamp():
    assert score_query(0) == 0.03
    assert score_query(50, ordered=5) > 0.5
    assert query_weight(0.01, 0.5) == 0.2
    assert query_weight(1.0, 0.1) == 3.0

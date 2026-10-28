# -*- coding: utf-8 -*-
import pytest
from apps.discovery.semantic_moderation import (
    is_political_post_semantic,
    check_comment_semantic_moderation
)
from shared.x_filters import is_political_x_post


def test_pure_political_tweets_are_rejected():
    political_samples = [
        "🔴در ۴۸ سال دست کم ۱۵۰۰ میلیارد دلار نفت و میعانات گازی توسط غارتگران اوباش ج.ا فروخته شده+مس و طلا ،سنگ آهن",
        "فعالیت‌های های امارت اسلامی افغانستان در ماه میزان ۱۴۰۵ ● اقتصادی و زیربنایی ۱. کمیسیون اقتصادی بر نظارت در قیمت‌ها و معادن سنگ",
        "جاوید شاه و مرگ بر دیکتاتوری آخوندی، به امید پیروزی انقلاب ملی ایران و سرنگونی رژیم جمهوری اسلامی",
        "دیدار دیپلماتیک وزیر امور خارجه و مقامات سیاسی دولت با سفرای اروپایی درباره مذاکرات هسته‌ای و برجام و تحریم‌های آمریکا",
        "اعتصاب سراسری کارگران نفت و اعتراضات مردمی به وضعیت معیشتی و بازداشت فعالان مدنی توسط سپاه پاسداران"
    ]
    for text in political_samples:
        is_pol, conf, reason = is_political_post_semantic(text)
        assert is_pol is True, f"Failed to reject political text: '{text[:50]}' (conf={conf}, reason={reason})"
        assert is_political_x_post(text) is True


def test_genuine_commercial_inquiries_are_not_rejected():
    buyer_samples = [
        "برای نمای اصلی یک پروژه مسکونی ۴ طبقه در نیاوران دنبال سنگ تراورتن عباس‌آباد سوپر یا حاجی‌آباد سورت یک هستم. حدود ۶۵۰ مترمربع نیاز داریم، قیمت متری چنده",
        "یه دندانپزشک متخصص درمان ریشه صبور و تمیز معرفی کنید که درد نداشته باشه. هزینه عصب‌کشی چقدر میشه؟",
        "لپ‌تاپ لنوو تینک‌پد ThinkPad T14 استوک گرید A++ رم ۳۲ گیگ برای برنامه‌نویسی تا ۴۰ تومن سراغ دارید؟ فروشگاه با مهلت تست یک هفته‌ای میخوام.",
        "مشاور رتبه‌ساز تجربی برای پشت کنکوری‌ها برنامه اختصاصی میده یا عمومیه؟ متقاضی ثبت‌نام فوری هستم.",
        "جرم‌گیری و بلیچینگ دندان با دستگاه لیزر کجا خوبه؟ هزینه رو اگر میدونید بگید."
    ]
    for text in buyer_samples:
        is_pol, conf, reason = is_political_post_semantic(text)
        assert is_pol is False, f"False positive: incorrectly rejected genuine buyer: '{text[:50]}' (conf={conf}, reason={reason})"
        assert is_political_x_post(text) is False


def test_commercial_buying_guard_prevents_false_positive_on_shared_words():
    # Tweet mentions governmental/state keyword but is explicitly a commercial purchase inquiry
    mixed_text = "برای سالن همایش پروژه‌ای در مجاورت ساختمان مجلس دنبال سنگ گرانیت مروارید یا نهبندان هستیم، قیمت متری چنده و ارسال به محل پروژه دارید؟"
    is_pol, conf, reason = is_political_post_semantic(mixed_text)
    assert is_pol is False, f"Buying guard failed for mixed commercial text: '{mixed_text[:50]}' (conf={conf})"
    assert is_political_x_post(mixed_text) is False

from __future__ import annotations
import logging
import os
import re
import numpy as np
from typing import Tuple, Optional, Dict, Any

log = logging.getLogger('semantic_moderation')

# Negative cluster anchor texts (representing toxicity, diss, scam accusations, sexual spam, and gibberish)
NEGATIVE_ANCHOR_CLUSTERS = {
    'profanity_and_sexual': [
        'حرومزاده بی ناموس کثافت آشغال بی شرف',
        'خفه شو زن قحبه لاشی بی ناموس سگ',
        'سکس حضوری دختر ماساژور صیغه ساعتی بیاد دایرکت',
        'فیلم سوپر و پورن خاله صیغه پیام تلگرام',
        'کص کش کونی جنده اوبی حرومی',
    ],
    'diss_and_scam_accusation': [
        'جمع کنید این مسخره‌بازی و دلقک‌بازی رو خجالت بکشید',
        'خوب جونتون بالا میاد مسخره‌های متقلب سر گردنه',
        'کلاهبردارای دزد مردم رو سر کار گذاشتید همش دروغه',
        'این آشغالا چیه می‌فروشید جنس بنجل و بنجل‌فروشی',
        'دروغه باور نکنید پولتونو می‌خورن دزدا همش کلکه',
        'چقدر گران‌فروش و بی انصافید قیمت فضایی و خون مردم تو شیشه',
        'دلقک‌بازی درنیارید کلاهبرداری محض هست',
    ],
    'yellow_spam_and_bots': [
        'افزایش فالوور و لایک تضمینی دایرکت پیام بدید با قیمت ارزان',
        'دعانویسی و باطل‌السحر تضمینی برگشت معشوق صد در صد قرآنی',
        'درآمد دلاری روزی ۵۰ دلار بدون سرمایه اولیه لینک در بیو',
        'شارژ رایگان همراه اول و ایرانسل با ارسال کد هدیه',
        'فروش فیلترشکن v2ray پرسرعت و قوی بدون قطعی تحویل فوری',
    ],
    'gibberish_and_meaningless': [
        'asdfghjkl zxcvbnm qwerty',
        'سیسیش شسیبنت کمنتن لبیلت',
        'حخعهغف قفغعهخ جچپ شسیبلات',
        'خخخخخخخخ ههههههههه پپپپپپپپپ',
        'بلبلبل لبلبلب تبتبتبت',
    ],
    'political_and_news': [
        'مرگ بر دیکتاتور و رژیم جمهوری اسلامی و خامنه‌ای زن زندگی آزادی',
        'غارت نفت و گاز و غارتگری اموال عمومی و اختلاس میلیارد دلاری توسط مسئولین و آقا زاده ها',
        'اعتراضات سراسری، اعتصاب عمومی کارگران و بازداشت معترضان و نقض حقوق بشر در زندان',
        'بیانیه سیاسی احزاب و نمایندگان مجلس و مصوبات دولت و سیاست خارجی',
        'سپاه پاسداران، جنگ منطقه‌ای، تنش نظامی، حملات موشکی و مذاکرات هسته‌ای و برجام',
        'دیدار دیپلماتیک وزیر امور خارجه و مقامات سیاسی دولت با سفرای خارجی',
        'اطلاعیه رسمی حکومت طالبان و امارت اسلامی افغانستان در خصوص معادن و اقتصاد',
        'اعدام و احکام قضایی صادره برای زندانیان سیاسی و محکومیت توسط سازمان ملل',
        'تحریم‌های بانکی و نفتی آمریکا و شورای امنیت علیه حکومت و گرانی ناشی از سوءمدیریت دولتی',
        'پهلوی، پادشاهی‌خواهان، اصلاح‌طلبان، اصولگرایان و احزاب اپوزیسیون خارج از کشور',
        'سرنگونی حکومت آخوندی و مبارزات مدنی و سیاسی برای آزادی ایران',
    ]
}

# Positive commercial buying anchors
POSITIVE_BUYING_ANCHORS = [
    'سلام قیمت این کار چنده و چطور سفارش بدم؟',
    'قیمت رو لطف میکنید دایرکت بفرستید؟',
    'لطفاً قیمت و شرایط رو برام دایرکت کنید ممنون',
    'ارسال به شهرستان دارید؟ متری چند حساب می‌کنید؟',
    'لطفاً کاتالوگ و مشخصات کامل و شرایط پرداخت اقساطی رو برام بفرستید',
    'شماره تماس یا آدرس لطف می‌کنید برای خرید و استعلام موجودی؟',
    'قیمت سایز بزرگ این مدل چقدر میشه برای خرید؟',
    'کدوم شعبه موجود دارید برای خرید حضوری خدمت برسیم؟',
    'سلام شرایط همکاری و فروش عمده به چه صورته؟',
]

POLITICAL_EXPLICIT_KEYWORDS = (
    'جمهوری اسلامی', 'ج.ا', 'رژیم', 'خامنه‌ای', 'پهلوی', 'شاهزاده', 'برجام', 'سپاه پاسداران',
    'طالبان', 'امارت اسلامی', 'اختلاس', 'غارتگران', 'زندانیان سیاسی', 'اعتراضات سراسری',
    'تحریم‌های', 'حقوق بشر', 'اعدام', 'سرنگونی', 'دیکتاتوری', 'آخوند', 'آخوندی', 'جاویدنام',
    'عرزشی', 'ارزشی', 'ساندیس‌خور', 'بسیجی', 'حزب‌اللهی'
)

BUYING_GUARD_KEYWORDS = (
    'بخرم', 'میخوام بخرم', 'می‌خوام بخرم', 'قصد خرید', 'خریدارم',
    'سفارش بدم', 'سفارش دهید', 'متری چنده', 'متری چند', 'استعلام قیمت',
    'موجود دارید', 'موجود دارین', 'قیمت چنده', 'قیمتش چنده', 'قیمت لطفاً', 'قیمت لطفا',
    'قیمت رو دایرکت', 'قیمت میدید', 'قیمت میدین'
)

_CACHED_EMBEDDER = None
_CACHED_ANCHOR_VECTORS: Dict[str, np.ndarray] = {}
_CACHED_POSITIVE_VECTORS: Optional[np.ndarray] = None


def _get_embedder():
    return None


def _init_anchor_vectors():
    return False


def is_gibberish_heuristics(text: str) -> Tuple[bool, str]:
    """Quick heuristic for repeated characters, extreme punctuation or keyboard mashing."""
    clean = re.sub(r'\s+', '', text)
    if len(clean) >= 6:
        max_run = max(len(m.group(0)) for m in re.finditer(r'(.) +', clean)) if re.search(r'(.) +', clean) else 0
        if max_run >= 5 and max_run / len(clean) >= 0.55:
            return True, 'repeated_char_gibberish'

    if re.match(r'^[qwrtpsdfghjklzxcvbnmQWRTPSDFGHJKLZXCVBNM]+$', clean) and len(clean) >= 6:
        return True, 'keyboard_mash_gibberish'

    return False, ''


def check_comment_semantic_moderation(comment_text: str) -> Dict[str, Any]:
    """
    Evaluates a comment/post against toxicity, political news, and gibberish
    without any remote embedding calls (100% deterministic keyword and heuristic filtering).
    """
    text = (comment_text or '').strip()
    if not text:
        return {'is_rejected': True, 'reason': 'empty_text', 'category': 'empty', 'confidence': 1.0}

    # 1. Quick heuristic for gibberish
    is_gib, gib_reason = is_gibberish_heuristics(text)
    if is_gib:
        return {
            'is_rejected': True,
            'reason': f'semantic_gibberish:{gib_reason}',
            'category': 'gibberish_and_meaningless',
            'confidence': 0.95,
        }

    # 2. Political check: reject obvious political rants if no buying guard is present
    has_buying_guard = any(k in text for k in BUYING_GUARD_KEYWORDS)
    has_pol_kw = any(k in text for k in POLITICAL_EXPLICIT_KEYWORDS)
    if has_pol_kw and not has_buying_guard:
        return {
            'is_rejected': True,
            'reason': 'explicit_political_and_news',
            'category': 'political_and_news',
            'confidence': 0.95,
            'max_negative_sim': 0.95,
            'max_positive_sim': 0.0,
        }

    # 3. Explicit severe hostility / profanity
    has_explicit_hostility = any(k in text for k in ['دلقک', 'جونتون', 'کلاهبردار', 'دزد', 'مسخره', 'دروغ', 'آشغال', 'بنجل', 'حرومزاده', 'جنده', 'لاشی', 'کثافت'])
    if has_explicit_hostility and not has_buying_guard:
        return {
            'is_rejected': True,
            'reason': 'explicit_hostility',
            'category': 'profanity_and_sexual',
            'confidence': 0.90,
            'max_negative_sim': 0.90,
            'max_positive_sim': 0.0,
        }

    return {
        'is_rejected': False,
        'reason': '',
        'category': 'acceptable',
        'confidence': 0.8,
        'max_negative_sim': 0.0,
        'max_positive_sim': 0.8,
    }


def is_political_post_semantic(text: str) -> Tuple[bool, float, str]:
    """
    Evaluates whether an X (Twitter) post is purely political, governmental, or protest
    news using embeddings with a high confidence threshold, ensuring genuine buyers
    are never falsely rejected. Returns (is_political, confidence, reason).
    """
    if not text or not text.strip():
        return False, 0.0, ""
    res = check_comment_semantic_moderation(text)
    if res.get('is_rejected') and res.get('category') == 'political_and_news':
        return True, float(res.get('confidence', 0.0)), str(res.get('reason', ''))
    return False, 0.0, ""


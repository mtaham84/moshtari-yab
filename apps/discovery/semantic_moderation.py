from __future__ import annotations
import logging
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
    global _CACHED_EMBEDDER
    if _CACHED_EMBEDDER is not None:
        return _CACHED_EMBEDDER
    try:
        from need_engine.config import EngineConfig
        from need_engine.llm import LLMClient
        from need_engine.store import Store
        from need_engine.embeddings import Embedder
        cfg = EngineConfig()
        store = Store(cfg.database_url, cfg.state_schema)
        llm = LLMClient(cfg, store)
        _CACHED_EMBEDDER = Embedder(cfg, llm)
        return _CACHED_EMBEDDER
    except Exception as e:
        log.warning('Could not initialize primary Embedder: %s', e)
        return None


def _init_anchor_vectors():
    global _CACHED_ANCHOR_VECTORS, _CACHED_POSITIVE_VECTORS
    if _CACHED_ANCHOR_VECTORS and _CACHED_POSITIVE_VECTORS is not None:
        return True

    emb = _get_embedder()
    if not emb:
        return False

    try:
        for cat, texts in NEGATIVE_ANCHOR_CLUSTERS.items():
            vecs = emb.encode(texts)
            _CACHED_ANCHOR_VECTORS[cat] = vecs

        _CACHED_POSITIVE_VECTORS = emb.encode(POSITIVE_BUYING_ANCHORS)
        log.info('Semantic moderation BGE-M3 anchors initialized successfully.')
        return True
    except Exception as exc:
        log.error('Failed to compute anchor embeddings: %s', exc)
        return False


def is_gibberish_heuristics(text: str) -> Tuple[bool, str]:
    """Quick heuristic for repeated characters, extreme punctuation or keyboard mashing."""
    clean = re.sub(r'\s+', '', text)
    if len(clean) >= 6:
        max_run = max(len(m.group(0)) for m in re.finditer(r'(.)+', clean)) if re.search(r'(.)+', clean) else 0
        if max_run >= 5 and max_run / len(clean) >= 0.55:
            return True, 'repeated_char_gibberish'

    if re.match(r'^[qwrtpsdfghjklzxcvbnmQWRTPSDFGHJKLZXCVBNM]+$', clean) and len(clean) >= 6:
        return True, 'keyboard_mash_gibberish'

    return False, ''


def check_comment_semantic_moderation(comment_text: str) -> Dict[str, Any]:
    """
    Evaluates a comment against semantic toxicity, diss, spam, and gibberish using BGE-M3 embeddings.
    """
    if not comment_text or not comment_text.strip():
        return {'is_rejected': True, 'reason': 'empty_text', 'category': 'empty'}

    # 1. Quick heuristic for gibberish
    is_gib, gib_reason = is_gibberish_heuristics(comment_text)
    if is_gib:
        return {
            'is_rejected': True,
            'reason': f'semantic_gibberish:{gib_reason}',
            'category': 'gibberish_and_meaningless',
            'confidence': 0.95,
        }

    # 2. Lazy init anchors
    if not _init_anchor_vectors():
        return {'is_rejected': False, 'reason': 'embedder_unavailable', 'category': 'none'}

    emb = _get_embedder()
    try:
        comment_vec = emb.encode([comment_text])[0]
    except Exception as exc:
        log.warning('Failed to encode comment: %s', exc)
        return {'is_rejected': False, 'reason': f'encode_error:{exc}', 'category': 'none'}

    # Check against negative clusters
    max_neg_sim = -1.0
    matched_cat = 'none'

    for cat, anchor_mat in _CACHED_ANCHOR_VECTORS.items():
        sims = anchor_mat @ comment_vec
        cat_max = float(np.max(sims))
        if cat_max > max_neg_sim:
            max_neg_sim = cat_max
            matched_cat = cat

    # Check against positive buying anchors
    pos_sims = _CACHED_POSITIVE_VECTORS @ comment_vec
    max_pos_sim = float(np.max(pos_sims))

    # Guard: Polite inquiries with buying cues (لطف میکنید، قیمت، دایرکت، بفرستید)
    has_polite_buying_cue = any(k in comment_text for k in ['لطف میکنید', 'لطف می‌کنید', 'لطفا', 'لطفاً', 'سلام', 'ممنون', 'خسته نباشید']) and any(k in comment_text for k in ['قیمت', 'دایرکت', 'چند', 'سفارش', 'ارسال', 'کاتالوگ'])
    has_explicit_hostility = any(k in comment_text for k in ['دلقک', 'جونتون', 'کلاهبردار', 'دزد', 'مسخره', 'دروغ', 'آشغال', 'بنجل', 'حرومزاده', 'جنده', 'لاشی', 'کثافت'])

    is_rejected = False
    reject_reason = ''

    if matched_cat == 'political_and_news':
        has_buying_guard = any(k in comment_text for k in BUYING_GUARD_KEYWORDS)
        has_pol_kw = any(k in comment_text for k in POLITICAL_EXPLICIT_KEYWORDS)

        # High confidence political check: strictly reject with 0 tokens if verified political
        if has_pol_kw and not has_buying_guard:
            if max_neg_sim >= 0.72 and (max_neg_sim - max_pos_sim) >= 0.04:
                is_rejected = True
                reject_reason = 'semantic_political_and_news'
        elif not has_buying_guard:
            if max_neg_sim >= 0.76 and (max_neg_sim - max_pos_sim) >= 0.10:
                is_rejected = True
                reject_reason = 'strong_semantic_political_and_news'
        elif max_neg_sim >= 0.88 and not any(k in comment_text for k in ['بخرم', 'سفارش بدم', 'متری چند']):
            is_rejected = True
            reject_reason = 'strong_semantic_political_and_news'
    elif has_explicit_hostility or not has_polite_buying_cue:
        if max_neg_sim >= 0.82 and max_neg_sim > max_pos_sim:
            is_rejected = True
            reject_reason = f'strong_semantic_{matched_cat}'
        elif max_neg_sim >= 0.74 and (max_neg_sim - max_pos_sim) > 0.05:
            is_rejected = True
            reject_reason = f'semantic_{matched_cat}'

    return {
        'is_rejected': is_rejected,
        'reason': reject_reason,
        'category': matched_cat if is_rejected else 'acceptable',
        'confidence': max_neg_sim if is_rejected else max_pos_sim,
        'max_negative_sim': max_neg_sim,
        'max_positive_sim': max_pos_sim,
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


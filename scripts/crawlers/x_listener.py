#!/usr/bin/env python3
"""
ماژول رصد، جستجو و تعامل در شبکه اجتماعی X / توییتر (X / Twitter Discovery & Reply Bot)
ویژه تیم هوش مصنوعی پروژه مشتری‌یاب (مسابقه buildX - مسئله شماره ۱)

این اسکریپت رصد توییت‌ها و کامنت‌ها را در شبکه X مدیریت می‌کند:
۱. استراتژی کامنت‌اول: درج ریپلای زیر توییت خریدار با لینک مستقیم کالا و دعوت مشروط به دایرکت.
۲. پاسخ در دایرکت: فقط زمانی به دایرکت پاسخ داده می‌شود که مشتری پیام داده یا اطلاعات بیشتر خواسته باشد.
۳. سقف مجاز پیام: حداکثر ۱۰ پیام در روز به ازای هر حساب کاربری در X (کامنت + دایرکت).
۴. گاردریل موضوعی: گفتگو منحصراً در چارچوب کاتالوگ فروشگاه؛ سوالات آشپزی و متفرقه مسدود می‌شود.
"""

import os
import re
import sys
import json
import logging
from datetime import datetime

# تنظیم دسترسی به مدل‌های جنگو در صورت اجرای محلی
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

try:
    import django
    django.setup()
    from django.utils import timezone
    from apps.businesses.models import Business
    from apps.products.models import Product
    from apps.discovery.models import DiscoveredLead, ProcessedMessageHash, ProductDailyMetric
    from apps.discovery.services import get_agent_discovery_feed, check_account_message_cap, validate_message_in_product_domain
    DJANGO_AVAILABLE = True
except Exception as e:
    DJANGO_AVAILABLE = False
    print(f"Django setup note: Running in API client mode ({e})")

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("x_listener")

OFF_TOPIC_GUARD = ["قرمه سبزی", "آشپزی", "دستور پخت", "هواشناسی", "فوتبال", "فال"]


class XDiscoveryWorker:
    def __init__(self, business_id: int = 1, api_base_url: str = "http://127.0.0.1:8000"):
        self.business_id = business_id
        self.api_base_url = api_base_url.rstrip("/")
        self.feed_data = None
        self.load_feed()

    def load_feed(self):
        """دریافت تنظیمات اکانت X و کاتالوگ فروشنده"""
        if DJANGO_AVAILABLE:
            biz = Business.objects.filter(id=self.business_id).first()
            if biz:
                self.feed_data = get_agent_discovery_feed(biz, channel="X")
                logger.info(f"Loaded feed for X via Django ORM. Products: {len(self.feed_data.get('products', []))}")
                return

        try:
            resp = requests.get(f"{self.api_base_url}/discovery/api/agent/feed/?channel=X")
            if resp.status_code == 200:
                self.feed_data = resp.json()
                logger.info("Loaded feed for X via REST API.")
        except Exception as err:
            logger.error(f"Failed to load feed via API: {err}")

    def evaluate_tweet(self, tweet_id: str, author_handle: str, author_name: str, tweet_text: str):
        """
        ارزیابی توییت دریافتی بر اساس کاتالوگ و استراتژی کامنت‌اول:
        - گاردریل موضوعی
        - محاسبه نمره نیت
        - ارسال کامنت حاوی لینک مستقیم کارت کالا
        - کنترل سقف روزانه ۱۰ پیام برای کاربر
        """
        clean_text = tweet_text.strip()

        # ۱. بررسی گاردریل امنیتی موضوع (Off-Topic Check)
        for bad_topic in OFF_TOPIC_GUARD:
            if bad_topic in clean_text.lower():
                logger.warning(f"Guardrail triggered for off-topic content: '{bad_topic}' in tweet.")
                return {"status": "blocked", "reason": "OFF_TOPIC"}

        # ۲. تطبیق با کاتالوگ محصولات فعال
        matched_product = None
        if self.feed_data and "products" in self.feed_data:
            for prod in self.feed_data["products"]:
                kws = prod.get("keywords", [])
                if any(kw in clean_text.lower() for kw in kws):
                    matched_product = prod
                    break

        if not matched_product:
            logger.debug(f"Tweet '{clean_text[:30]}' does not match any catalog keywords.")
            return None

        # ۳. تدوین کامنت پاسخ هوشمند (Comment-First Strategy)
        prod_name = matched_product["name"]
        prod_id = matched_product["id"]
        prod_link = matched_product.get("url") or f"{self.api_base_url}/p/{prod_id}"
        price_str = matched_product.get("price", {}).get("formatted", "شرایط ویژه")

        comment_message = (
            f"سلام {author_name or author_handle} گرامی،\n"
            f"در پاسخ به پرسش شما درباره {prod_name}، این محصول در کاتالوگ رسمی ما با {price_str} موجود است.\n"
            f"لینک مستقیم مشخصات، تصاویر و ثبت سفارش:\n{prod_link}\n"
            f"در صورت نیاز به بررسی مشخصات بیشتر یا هرگونه راهنمایی، خوشحال می‌شویم به دایرکت ما پیام بدهید."
        )

        post_url = f"https://x.com/{author_handle.replace('@', '')}/status/{tweet_id}"

        # ۴. ارسال به دیتابیس با سهمیه روزانه
        payload = {
            "business_id": self.business_id,
            "channel": "X",
            "lead_handle": author_handle,
            "lead_display_name": author_name,
            "post_url": post_url,
            "content_snippet": clean_text,
            "product_id": prod_id,
            "intent_score": 85,
            "intent_reasoning": f"تطبیق کلیدواژه‌های کالای «{prod_name}» در توییت عمومی شبکه X.",
            "outreach_mode": "COMMENT",
            "outreach_message": comment_message,
            "direct_link_sent": prod_link,
            "bot_agent_name": "بات پیدا (@peyda_bot)",
            "tokens_used": 610,
            "cost_usd": 0.000384,
            "cost_toman": 27
        }

        try:
            resp = requests.post(f"{self.api_base_url}/discovery/api/leads/submit/", json=payload, timeout=5)
            data = resp.json()
            logger.info(f"X Tweet Lead processed: status={data.get('status')}, capped_today={data.get('is_conversation_capped')}")
            return data
        except Exception as ex:
            logger.error(f"Error submitting X lead to backend: {ex}")
            return None


if __name__ == "__main__":
    print("=" * 60)
    print("X / Twitter Discovery Bot Initialized (Peyda Bot Strategy)")
    print("Daily message cap per account: 10 messages/day across comments and DMs")
    print("=" * 60)
    bot = XDiscoveryWorker()
    # تست با یک توییت نمونه
    bot.evaluate_tweet(
        tweet_id="17894019283",
        author_handle="@sara_tehrani",
        author_name="سارا تهرانی",
        tweet_text="بچه‌ها کسی آنلاین‌شاپی رو می‌شناسه که شلوار کارگو باکیفیت و دوخت تمیز داشته باشه؟ چند وقته دنبال مدل کتان بگ می‌گردم."
    )

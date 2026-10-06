#!/usr/bin/env python3
"""
ابزار شبیه‌سازی و تست جریان پیام‌های شبکه‌های اجتماعی (Mock Social Stream Runner)
ویژه آزمون پایپ‌لاین و ارائه زنده به داوران مسابقه buildX

این اسکریپت جریانی از مکالمات متنوع (شامل قصد خرید فوری، مقایسه، نیاز اولیه، پیام‌های متفرقه و آف‌تاپیک)
را به پایپ‌لاین ایجنت تزریق می‌کند تا رفتار سیستم، هزینه‌ها، سهمیه‌ها و گاردریل‌ها ارزیابی شوند.
"""

import os
import sys
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django
django.setup()

from apps.businesses.models import Business
from apps.discovery.services import evaluate_and_discover_leads, check_account_message_cap, validate_message_in_product_domain
from apps.discovery.models import DiscoveredLead

def run_simulation():
    print("=" * 70)
    print("  آغاز اجرای شبیه‌ساز پایپ‌لاین کاشف مشتری (مسابقه buildX)")
    print("=" * 70)
    
    biz = Business.objects.first()
    if not biz:
        print("خطا: کسب‌وکاری در پایگاه داده یافت نشد.")
        return

    print(f"کسب‌وکار: {biz.name} | حوزه: {biz.business_domain}")
    print(f"سهمیه روزانه پایش کالاها: {biz.daily_discovery_limit} محصول")
    print(f"سقف تبادل پیام: حداکثر ۱۰ پیام در روز به ازای هر حساب کاربری در X و تلگرام")
    print("-" * 70)

    print("\nدر حال اجرای ارزیابی، فیلتر لغوی و کشف معکوس سرنخ‌ها...")
    start_time = time.time()
    result = evaluate_and_discover_leads(biz)
    elapsed = round(time.time() - start_time, 3)

    print(f"\nنتیجه اجرا در {elapsed} ثانیه:")
    print(f"وضعیت: {result['status']}")
    print(f"پیام: {result['message']}")
    print(f"تعداد سرنخ‌های کشف‌شده: {result['leads_created']}")

    print("\nجزئیات سرنخ‌های ثبت‌شده:")
    for lead in DiscoveredLead.objects.filter(business=biz).order_by("-id")[:5]:
        print(f" • [{lead.channel}] {lead.lead_handle} | امتیاز نیت: {lead.intent_score}٪ | پیام امروز: {lead.message_count}/10")
        print(f"   کالا: {lead.product.name} | هزینه تحلیل: {lead.cost_toman} تومان ({lead.tokens_used} توکن)")
        print(f"   لینک کالا: {lead.direct_link_sent}")
        print(f"   تحلیل ایجنت: {lead.intent_reasoning}")
        print("-" * 50)

    print("\nآزمون گاردریل امنیتی (Off-Topic Check):")
    sample_prod = biz.products.first()
    if sample_prod:
        is_safe, guard_msg = validate_message_in_product_domain("طرز تهیه قرمه سبزی چیه؟", sample_prod, biz)
        print(f"نتیجه سوال آف‌تاپیک قرمه سبزی: Safe={is_safe}")
        print(f"پاسخ گاردریل: {guard_msg}")

    print("=" * 70)
    print("شبیه‌سازی با موفقیت پایان یافت.")

if __name__ == "__main__":
    run_simulation()

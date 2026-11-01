import os, sys, json
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.businesses.models import Business
from apps.products.models import Product
from apps.billing.models import Wallet, WalletTransaction
from apps.discovery.models import Opportunity
import psycopg
from x_ingest.__main__ import _dsn

target_ids = [2, 4, 6]

print("=== 1. CURRENT WALLET BALANCES ===")
for bid in target_ids:
    b = Business.objects.get(id=bid)
    w = b.wallet
    print(f"Tenant ID={b.id} ({b.name}): موجودی={w.balance_toman:,.0f} تومان | تعداد کالاها={b.products.count()} (همگی x_search_enabled=True)")

print("\n=== 2. DISCOVERED TWEETS & OPPORTUNITIES PER TENANT ===")
for bid in target_ids:
    b = Business.objects.get(id=bid)
    b_opps = list(Opportunity.objects.filter(business=b, source_platform="x").order_by("-created_at"))
    print(f"\n=======================================================")
    print(f"TENANT ID={b.id} | {b.name} ({len(b_opps)} فرصت کشف‌شده در توییتر)")
    print(f"=======================================================")
    if not b_opps:
        print("هنوز فرصتی در جدول Opportunity ثبت نشده است.")
    for idx, o in enumerate(b_opps, 1):
        prod_matches = list(o.product_matches.select_related("product").all())
        prod_names = ", ".join([pm.product.name for pm in prod_matches]) or "نامشخص"
        txt = (o.normalized_message or o.source_raw_message or "").strip().replace(chr(10), " ")
        print(f"{idx}. [وضعیت: {o.status} | قصد: {o.intent_type} | اطمینان: {o.category_confidence}]")
        print(f"   تاریخ کشف: {o.created_at.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   کوئری جستجو: {o.source_query}")
        print(f"   کالای انطباق‌یافته: {prod_names}")
        print(f"   متن توییت مشتری: {txt}")
        print(f"   لینک/شناسه توییت: https://x.com/i/web/status/{o.source_message_id}")

print("\n=== 3. RECENT POSTS MATCHED IN CRAWLER POSTS ===")
with psycopg.connect(_dsn()) as conn:
    with conn.cursor() as cur:
        for bid in target_ids:
            b = Business.objects.get(id=bid)
            pids = [str(p.id) for p in b.products.all()]
            cur.execute("""
                SELECT p.post_id, p.author_handle, p.created_at, p.text, pp.product_id
                FROM crawler.x_posts p
                JOIN crawler.x_post_products pp ON p.post_id = pp.post_id
                WHERE pp.product_id = ANY(%s)
                ORDER BY p.created_at DESC
                LIMIT 5;
            """, (pids,))
            posts = cur.fetchall()
            print(f"\n--- پست‌های ذخیره‌شده کراولر برای شناسه {b.id} ({b.name}): {len(posts)} پست ---")
            for post in posts:
                print(f"  * [پست {post[0]} | @{post[1]} | {post[2]}] (کالا {post[4]}): {post[3][:120].replace(chr(10), ' ')}...")

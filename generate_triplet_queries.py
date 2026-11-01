import os, sys, json
os.environ['HTTPS_PROXY'] = 'socks5h://xray:1082'
os.environ['https_proxy'] = 'socks5h://xray:1082'
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from apps.businesses.models import Business
from apps.products.models import Product
from apps.billing.models import Wallet
from need_engine.config import EngineConfig
from need_engine.sources import SQLProductSource
from need_engine.store import Store
from workers.x_collector.query_source import load_intent_queries, _load_intent_queries, targeted_queries_from_families
from workers.x_collector.families import build_families, load_notes, family_cards, family_topic_contexts
from workers.x_collector.llm_terms import llm_terms, KV_PREFIX
from need_engine.llm import LLMClient

target_ids = [2, 4, 6]
target_str_ids = ["2", "4", "6"]
print("=== 1. TENANT STATUS & WALLET BALANCES ===")
for bid in target_ids:
    b = Business.objects.get(id=bid)
    w = b.wallet
    print(f"Tenant {b.id}: {b.name} | اعتبار: {w.balance_toman:,.0f} تومان | کالاها: {b.products.count()}")

print("\n=== 2. GENERATING ENTITY-BLIND NEED TRIPLET QUERIES ===")
config = EngineConfig()
source = SQLProductSource(config)
all_prods = source.all()
products = [p for p in all_prods if str(p.business_id) in target_str_ids and p.x_search_enabled]
store = Store(config.database_url, config.state_schema)

notes = load_notes(source.db)
families = build_families(products, notes)
print(f"Total matching products: {len(products)}")
print(f"Total families for target tenants: {len(families)}")

cards = store.product_cards()
cards = family_cards(families, cards)
llm_inst = LLMClient(config, store)

print("Clearing x_qterms from KV table to guarantee 100% fresh Luna generation...")
store._exec("DELETE FROM {s}.kv WHERE k LIKE %s", (KV_PREFIX + "%",))

print(f"Triggering LLM terms generation with model: {config.extract_model}...")
terms = llm_terms(families, cards, store, llm_inst, config.extract_model, batch_size=4)

print("\n=== 3. GENERATED QUERIES AND TRIPLETS PER TENANT ===")
for bid in target_ids:
    b = Business.objects.get(id=bid)
    fam_list = [f for f in families if str(f.business_id) == str(bid)]
    print(f"\n=======================================================")
    print(f"TENANT ID: {b.id} | {b.name}")
    print(f"تعداد زیرگروه‌ها/خانواده‌های محصولی: {len(fam_list)}")
    print(f"=======================================================")
    
    contexts = family_topic_contexts(fam_list, cards)
    for fam in fam_list:
        fid = str(fam.product_id)
        fam_terms = terms.get(fid, {})
        triplets = fam_terms.get("need_triplets") or fam_terms.get("triplets") or []
        print(f"\n  [زیرگروه: {fam.category_path or fam.title}] (شناسه خانواده: {fid})")
        print(f"  تعداد کالاهای زیرمجموعه: {len(fam.member_product_ids)} -> {', '.join(fam.titles[:4])}")
        
        if triplets:
            print("  سه‌گانه‌های نیاز کور نسبت به شیء (Entity-Blind Need Triplets):")
            for idx, tr in enumerate(triplets, 1):
                print(f"    {idx}. زمینه/پرسونا: «{tr.get('context')}» | عامل محرک: «{tr.get('trigger')}» | درد/نشانه: «{tr.get('pain')}»")
        else:
            print("  سه‌گانه‌ای ثبت نشده یا مدل خروجی پیش‌فرض داده است.")
            
        contexts[fid]["family"] = fam
        from workers.x_collector.intent_queries import product_queries
        queries = product_queries(fam, terms=fam_terms, card=cards.get(fid), topic_context=contexts.get(fid))
        print(f"  کوئری‌های نهایی تولید شده برای این زیرگروه (تعداد: {len(queries)}):")
        for q_idx, q in enumerate(queries, 1):
            print(f"    {q_idx}. {q['query']} (نوع: {q.get('kind')})")

source.db.conn.close()
store.close()

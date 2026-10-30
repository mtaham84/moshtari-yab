"""Multi-Sender Simulation script for Sarv Dental Clinic Instagram Outreach.
Demonstrates:
1. Strict Role Isolation: Only SENDER accounts are used; SCRAPERS are NEVER used for DMs.
2. Distinct Devices:
   - @raha.rostamim -> Xiaomi 14 Pro
   - @Pania.mehrim  -> Google Pixel 8 Pro
3. Personalized Account-Specific Pitch:
   - When @raha.rostamim messages -> "بنده رها هستم..."
   - When @Pania.mehrim messages  -> "بنده پنیا هستم..."
4. Round-Robin message distribution across senders.
5. Lead prioritization by highest intent score first.
6. Anti-spam safety delays between consecutive DMs.
"""
import json
import time
from pathlib import Path

# Load SENDER accounts from data/accounts_pool.json
pool_path = Path("data/accounts_pool.json")
pool_data = json.loads(pool_path.read_text(encoding="utf-8"))

senders = [
    a for a in pool_data["instagram"]
    if a.get("role") == "SENDER" and a.get("tenant_id") == "sarv"
]

config_path = Path("data/sarv_outreach_config.json")
config = json.loads(config_path.read_text(encoding="utf-8"))

HYPOTHETICAL_LEADS = [
    {
        "id": 101,
        "username": "sara_beauty",
        "full_name": "سارا",
        "comment_text": "سلام، هزینه کامپوزیت و لمینت دندون جلو چقدر میشه؟",
        "intent_score": 0.96,
        "category": "کامپوزیت و لمینت زیبایی"
    },
    {
        "id": 102,
        "username": "nima_tehrani",
        "full_name": "نیما",
        "comment_text": "برای ایمپلنت دندان مشاوره می‌خواستم. شرایط اقساطی دارید؟",
        "intent_score": 0.89,
        "category": "ایمپلنت تخصصی"
    },
    {
        "id": 103,
        "username": "maryam_ahmadi",
        "full_name": "مریم احمدی",
        "comment_text": "دکتر متخصص خوب برای طرح لبخند و جرم‌گیری سراغ دارید؟",
        "intent_score": 0.78,
        "category": "طراحی لبخند و جرم‌گیری"
    },
    {
        "id": 104,
        "username": "ali_rezaei",
        "full_name": "علیرضا",
        "comment_text": "قیمت ارتودنسی نامرئی برای بزرگسالان چنده؟",
        "intent_score": 0.75,
        "category": "ارتودنسی"
    }
]

def run_multi_sender_demo():
    print("=" * 75)
    print("🚀 [استخر فرستنده‌های اختصاصی مجموعه دندان‌پزشکی سرو - تفکیک هویت]")
    print(f"👥 تعداد اکانت‌های فرستنده فعال (SENDER): {len(senders)} اکانت")
    for idx, s in enumerate(senders, 1):
        u = s['username']
        dev = s['device_settings']['device_name']
        pitch_info = config["sender_pitches"].get(u, {})
        consultant = pitch_info.get("consultant_name", u)
        print(f"   {idx}. @{u} | مشاور: {consultant} | دیوایس: {dev}")
    print("=" * 75 + "\n")

    valid_leads = [l for l in HYPOTHETICAL_LEADS if l["intent_score"] >= config["safety_policy"]["min_intent_score"]]
    sorted_leads = sorted(valid_leads, key=lambda x: x["intent_score"], reverse=True)

    print("📊 صف اولویت ارسال پیام (مرتب‌شده بر اساس بالاترین احتمال خرید):")
    for idx, l in enumerate(sorted_leads, 1):
        print(f"   {idx}. @{l['username']} ({l['full_name']}) | احتمال خرید: {int(l['intent_score']*100)}% | نیاز: {l['category']}")
    print("-" * 75 + "\n")

    # Round-Robin dispatching
    for idx, lead in enumerate(sorted_leads):
        sender = senders[idx % len(senders)]
        sender_user = sender['username']
        sender_dev = sender['device_settings']['device_name']
        sender_pitch = config["sender_pitches"].get(sender_user, {})
        pitch_text = sender_pitch.get("template", "")
        consultant_name = sender_pitch.get("consultant_name", sender_user)

        print(f"👉 [مشتری {idx+1}/{len(sorted_leads)}]: @{lead['username']} — احتمال خرید: {int(lead['intent_score']*100)}%")
        print(f"   📌 اکانت فرستنده: @{sender_user} (مشاور: {consultant_name} | دیوایس: {sender_dev})")
        print(f"   [۱/۴] 👤 شبیه‌سازی باز کردن پروفایل @{lead['username']} با {sender_dev}...")
        time.sleep(1)

        print(f"   [۲/۴] ⌨️ شبیه‌سازی تایپ طبیعی توسط {consultant_name}...")
        time.sleep(1)

        print(f"   [۳/۴] ✉️ متن ارسالی از سمت @{sender_user}:")
        print("   " + "-" * 55)
        for line in pitch_text.splitlines():
            print(f"      {line}")
        print("   " + "-" * 55)

        print(f"   [۴/۴] ✅ پیام دایرکت ارسال شد (فرستنده: @{sender_user}).")

        if idx + 1 < len(sorted_leads):
            print(f"\n   ⏳ [وقفه ایمنی ۵ دقیقه‌ای ضد بلاک]...")
            print("   " + "." * 50 + "\n")

    print("\n" + "=" * 75)
    print("🏁 [پایان چرخه]: هر اکانت با هویت و نام مشاور اختصاصی خود پیام‌ها را ارسال کرد.")
    print("=" * 75)

if __name__ == "__main__":
    run_multi_sender_demo()

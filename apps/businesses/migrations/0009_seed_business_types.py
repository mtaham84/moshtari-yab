from django.db import migrations

TYPES = [
    ("apparel", "پوشاک و مد", "لباس، کفش، کیف و اکسسوری", "shirt", "PHYSICAL"),
    ("food", "خوراکی و نوشیدنی", "مواد غذایی، شیرینی، میوه و پروتئین", "food", "PHYSICAL"),
    ("digital", "لوازم دیجیتال و الکترونیک", "موبایل، لپ‌تاپ، لوازم جانبی و قطعات", "cpu", "PHYSICAL"),
    ("home", "لوازم خانگی و دکوراسیون", "لوازم برقی، مبلمان، فرش و دکور", "home", "PHYSICAL"),
    ("beauty", "آرایشی، بهداشتی و مراقبت", "لوازم آرایش، پوست و مو، عطر", "heart", "PHYSICAL"),
    ("building", "مصالح ساختمانی و تأسیسات", "سنگ، کاشی، رنگ، لوله و ابزار", "building", "PHYSICAL"),
    ("auto", "خودرو و موتورسیکلت", "قطعات، لوازم جانبی، خرید و فروش", "car", "PHYSICAL"),
    ("health", "پزشکی، دندان‌پزشکی و سلامت", "کلینیک، درمان، تجهیزات پزشکی", "shield", "SERVICE"),
    ("education", "آموزش و آموزشگاه", "کنکور، زبان، برنامه‌نویسی، هنر و مهارت", "book", "SERVICE"),
    ("software", "خدمات نرم‌افزاری و دیجیتال مارکتینگ", "طراحی سایت، اپلیکیشن، سئو، تبلیغات", "monitor", "SERVICE"),
    ("construction", "خدمات ساختمانی و تعمیرات", "بازسازی، نقاشی ساختمان، تأسیسات، تعمیرکار", "tool", "SERVICE"),
    ("beauty_services", "آرایشگاه و خدمات زیبایی", "سالن زیبایی، ناخن، مو، پوست", "star", "SERVICE"),
    ("sports", "ورزش و تناسب اندام", "باشگاه، مربی، تجهیزات ورزشی", "target", "PHYSICAL"),
    ("kids", "کودک و نوزاد", "لباس، اسباب‌بازی، لوازم نوزاد", "users", "PHYSICAL"),
    ("stationery", "لوازم تحریر، کتاب و فرهنگی", "کتاب، نوشت‌افزار، هنری", "doc", "PHYSICAL"),
    ("jewelry", "طلا، جواهر و زیورآلات", "طلا، نقره، بدلیجات، ساعت", "star", "PHYSICAL"),
    ("handicraft", "صنایع دستی و هنری", "سفال، قالی، منبت، تابلو", "box", "PHYSICAL"),
    ("travel", "مسافرت، گردشگری و اقامت", "تور، هتل، بلیط، کرایه", "globe", "SERVICE"),
    ("legal_finance", "خدمات حقوقی، مالی و مشاوره", "وکیل، حسابدار، مشاور", "doc", "SERVICE"),
    ("agriculture", "کشاورزی، دام و باغبانی", "نهال، کود، ماشین‌آلات، دام", "box", "PHYSICAL"),
    ("industrial", "صنعتی، بسته‌بندی و عمده‌فروشی", "ماشین‌آلات، مواد اولیه، فروش عمده", "building", "PHYSICAL"),
    ("pets", "حیوانات خانگی", "غذا، لوازم، دامپزشکی", "heart", "PHYSICAL"),
    ("events", "مراسم و خدمات مجالس", "عروسی، عکاسی، پذیرایی، تالار", "calendar", "SERVICE"),
    ("transport", "حمل‌ونقل و باربری", "باربری، پیک، حمل بار", "send", "SERVICE"),
    ("other", "سایر", "هر کسب‌وکار دیگر", "box", "PHYSICAL"),
]


def seed(apps, schema_editor):
    BusinessType = apps.get_model("businesses", "BusinessType")
    Business = apps.get_model("businesses", "Business")
    for i, (key, name, desc, icon, kind) in enumerate(TYPES, start=1):
        BusinessType.objects.update_or_create(key=key, defaults={"name": name, "description": desc, "icon": icon, "kind": kind, "order": i * 10})
    Business.objects.all().update(onboarding_done=True)   # tenants that existed before the guided setup keep working


class Migration(migrations.Migration):
    dependencies = [("businesses", "0008_business_cover_business_logo_and_more")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

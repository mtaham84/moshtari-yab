import os
import sys

# Setup Django standalone
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.production')
if 'DJANGO_SETTINGS_MODULE' not in os.environ:
    os.environ['DJANGO_SETTINGS_MODULE'] = 'customerweb.settings'

try:
    import django
    django.setup()
except Exception:
    # Try finding settings module
    for s in ['config.settings', 'customerweb.settings', 'config.settings.local', 'settings']:
        try:
            os.environ['DJANGO_SETTINGS_MODULE'] = s
            import django
            django.setup()
            break
        except Exception:
            pass

from decimal import Decimal
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.businesses.models import Business, MessageStyle
from apps.products.models import Category, Product
from apps.billing.models import Wallet, WalletTransaction
from apps.billing.services import ensure_wallet, apply_transaction

User = get_user_model()

print("--- 1. Deactivating Discovery for Businesses 3 and 4 ---")
p3_count = Product.objects.filter(business_id=3, is_discovery_active=True).update(is_discovery_active=False)
p4_count = Product.objects.filter(business_id=4, is_discovery_active=True).update(is_discovery_active=False)
print(f"Deactivated discovery for Business 3: {p3_count} products, Business 4: {p4_count} products.")

print("\n--- 2. Creating User & Business for Building Stone ---")
user, created = User.objects.get_or_create(
    username="sang@peyda.ir",
    defaults={
        "email": "sang@peyda.ir",
        "first_name": "مهدی",
        "last_name": "سنگ‌شناس (صنایع سنگ نقش‌جهان)",
        "is_active": True,
    }
)
user.set_password("Sang_2026_Demo!")
user.save()
print(f"User sang@peyda.ir ready (id={user.id}).")

biz, created = Business.objects.update_or_create(
    user=user,
    defaults={
        "name": "صنایع سنگ و سنگ‌بری نگین نقش‌جهان (تولید و تأمین سنگ ساختمانی و نما)",
        "business_type": "PHYSICAL",
        "business_domain": "تولید، برش، ساب و فروش تخصصی انواع سنگ نما، سنگ تراورتن، سنگ مرمریت، سنگ اسلب، سنگ گرانیت و سنگ پله ساختمانی",
        "description": "تولیدکننده و تامین‌کننده دست‌اول انواع سنگ‌های ساختمانی و نمای رومی و مدرن، تراورتن عباس‌آباد و حاجی‌آباد، مرمریت دهبید و لاشتر، اسلب‌های بوک‌مچ لوکس و سنگ‌های پله و پارکینگ مستقیم از کارخانه سنگ‌بری با ضمانت ضخامت و کیفیت ساب.",
        "location": "تهران و اصفهان (شهرک صنعتی محمودآباد)",
        "target_locations": "سراسر کشور",
        "target_customer_description": "سازندگان ساختمان، انبوه‌سازان، معماران، پیمانکاران نما، سنگ‌کاران و خریداران عمده و پروژه‌ای سنگ ساختمانی",
        "telegram_account_handle": "@NeginStone_Sales",
        "daily_discovery_limit": 100,
        "preferred_outreach_mode": "DIRECT",
    }
)
print(f"Business ready (id={biz.id}, name={biz.name}).")

# Style
style, _ = MessageStyle.objects.update_or_create(
    business=biz,
    defaults={
        "tone": "FORMAL",
        "max_sentences": 3,
        "use_emoji": True,
        "signature": "ارادتمند - بازرگانی و کارخانه سنگ نقش‌جهان",
        "include_link": True,
        "extra_instructions": "مشاوره تخصصی متراژ، معرفی سنگ مناسب اقلیم پروژه و ارسال تصاویر و فیلم بدون افکت سنگ‌ها",
    }
)

print("\n--- 3. Creating Category Tree ---")
# Root Categories
c_facade, _ = Category.objects.get_or_create(
    business=biz, name="سنگ نما و انواع تراورتن", parent=None,
    defaults={"depth": 1, "product_type": "PHYSICAL"}
)
c_interior, _ = Category.objects.get_or_create(
    business=biz, name="سنگ کف، پله و مشاعات ساختمانی", parent=None,
    defaults={"depth": 1, "product_type": "PHYSICAL"}
)
c_slab, _ = Category.objects.get_or_create(
    business=biz, name="سنگ‌های لوکس اسلب و بوک‌مچ", parent=None,
    defaults={"depth": 1, "product_type": "PHYSICAL"}
)
c_granite_deco, _ = Category.objects.get_or_create(
    business=biz, name="سنگ گرانیت، سنگ لاشه و دکوراتیو", parent=None,
    defaults={"depth": 1, "product_type": "PHYSICAL"}
)

# Subcategories
c_trav_super, _ = Category.objects.get_or_create(
    business=biz, name="تراورتن سوپر و ممتاز نما (عباس‌آباد و حاجی‌آباد)", parent=c_facade,
    defaults={"depth": 2, "product_type": "PHYSICAL"}
)
c_trav_project, _ = Category.objects.get_or_create(
    business=biz, name="تراورتن اقتصادی پروژه‌ای (دره‌بخاری، آبگرم و رامشه)", parent=c_facade,
    defaults={"depth": 2, "product_type": "PHYSICAL"}
)
c_marmarit, _ = Category.objects.get_or_create(
    business=biz, name="سنگ مرمریت کف و لابی (دهبید، لاشتر، پرشین)", parent=c_interior,
    defaults={"depth": 2, "product_type": "PHYSICAL"}
)
c_stairs, _ = Category.objects.get_or_create(
    business=biz, name="سنگ پله، زیرپله و کف پارکینگ", parent=c_interior,
    defaults={"depth": 2, "product_type": "PHYSICAL"}
)
c_slab_crystal, _ = Category.objects.get_or_create(
    business=biz, name="اسلب چینی و کریستال بوک‌مچ (ازنا و الیگودرز)", parent=c_slab,
    defaults={"depth": 2, "product_type": "PHYSICAL"}
)
c_granite, _ = Category.objects.get_or_create(
    business=biz, name="سنگ گرانیت ضدسایش (مروارید و نهبندان)", parent=c_granite_deco,
    defaults={"depth": 2, "product_type": "PHYSICAL"}
)
c_deco, _ = Category.objects.get_or_create(
    business=biz, name="سنگ آنتیک، گیوتین و مالون باغی", parent=c_granite_deco,
    defaults={"depth": 2, "product_type": "PHYSICAL"}
)

print("\n--- 4. Creating Rich Realistic Products ---")
products_data = [
    # 1. سنگ نما و تراورتن سوپر
    ("سنگ تراورتن عباس‌آباد سفید سوپر (بی‌موج ساب براق)", c_trav_super, 1850000,
     "سنگ نمای تراورتن عباس‌آباد با تم رنگی سفید یکدست، قدوپای بلند، تخلخل پرشده با رزین اپوکسی درجه یک و ساب آیینه‌ای. بسیار مقاوم در برابر یخ‌زدگی و باران‌های اسیدی.",
     "سازندگان نماهای لوکس رومی و مدرن، پروژه‌های ویلایی و ساختمانی تهران و کلان‌شهرها",
     {"ابعاد": "عرض ۴۰ طولی", "ضخامت": "۱.۸ سانت دقیق", "فرآوری": "ساب صیقلی اپوکسی", "معدن": "محلات عباس‌آباد"}),

    ("سنگ تراورتن حاجی‌آباد کرم روشن ممتاز", c_trav_super, 1280000,
     "سنگ تراورتن حاجی‌آباد بدون موج با بافت متراکم و استخوانی و چسبندگی فوق‌العاده به ملات سیمان. تم کرم روشن و گرم مناسب نمای ترکیبی، حیاط و نماهای کلاسیک.",
     "معماران و سازندگانی که به دنبال سنگ بادوام، بدون پرتی و خوش‌رنگ هستند",
     {"ابعاد": "عرض ۴۰ طولی", "ضخامت": "۱.۷۵ سانت", "فرآوری": "ساب براق یووی", "معدن": "محلات حاجی‌آباد"}),

    ("سنگ تراورتن آتشکوه سفید صادراتی", c_trav_super, 2600000,
     "لوکس‌ترین و درخشان‌ترین سنگ نمای تراورتن ایران، با تخلخل بسیار ریز، استقامت فشاری بالا و رنگ سفید مرواریدی بی‌نظیر.",
     "کارفرمایان برج‌های مسکونی لوکس و پنت‌هاوس‌ها",
     {"ابعاد": "عرض ۴۰ طولی", "ضخامت": "۲ سانت کامل", "فرآوری": "سوپر ساب ایتالیایی", "معدن": "نیم‌ور آتشکوه"}),

    # 2. سنگ نما و تراورتن اقتصادی پروژه‌ای
    ("سنگ تراورتن دره‌بخاری شکلاتی موج‌پیچ", c_trav_project, 890000,
     "سنگ تراورتن دره‌بخاری با موج‌های جذاب شکلاتی و کرم، ساب عالی و قیمت مناسب برای حجم‌های بالای متراژ و نماهای مدرن و ترکیبی با آجر نسوز.",
     "انبوه سازان، پیمانکاران مجتمع‌های مسکونی و طراحان نمای مدرن ترکیبی",
     {"ابعاد": "عرض ۴۰ طولی", "ضخامت": "۱.۷ سانت", "فرآوری": "رزین ماستیک و پولیش", "معدن": "دره‌بخاری محلات"}),

    ("سنگ تراورتن آبگرم و تکاب کرم بژ پروژه‌ای", c_trav_project, 650000,
     "سنگ تراورتن باصرفه اقتصادی بسیار بالا برای پروژه‌های انبوه‌سازی و دیوارهای حیاط و نورگیر. ماندگاری عالی و چسبندگی قوی.",
     "انبوه‌سازان مسکن ملی، پروژه‌های تعاونی و متراژهای بالا",
     {"ابعاد": "عرض ۴۰ طولی", "ضخامت": "۱.۶ سانت استاندارد", "فرآوری": "ماستیک ساب", "معدن": "آبگرم محلات"}),

    ("سنگ تراورتن شکلاتی کاشان و یزد (موج‌دار پررنگ)", c_trav_project, 580000,
     "سنگ تراورتن تیره با ماندگاری رنگ در برابر آفتاب شدید و نور مستقیم. مناسب نمای رومی به عنوان ستون و قاب پنجره و ازاره ساختمان.",
     "سازندگان ویلاهای شمال، قاب پنجره و سنگ ازاره دور ساختمان",
     {"ابعاد": "عرض ۴۰ طولی", "ضخامت": "۱.۷ سانت", "فرآوری": "ساب صیقلی", "معدن": "کاشان"}),

    # 3. سنگ مرمریت کف و لابی
    ("سنگ مرمریت دهبید شایان کرم شیشه‌ای (کف ۶۰×۶۰ و ۸۰×۸۰)", c_marmarit, 1450000,
     "سنگ مرمریت لوکس صادراتی دهبید با ساب آیینه‌ای فوق‌العاده شفاف، بدون رگه ناخالصی و با تراکم بسیار بالا. ایده‌آل برای سالن پذیرایی، لابی هتل و تالار.",
     "طراحان داخلی، سازندگان پروژه‌های مسکونی مدرن و لابی‌های مجلل",
     {"ابعاد": "تایل ۸۰×۸۰ و ۶۰×۶۰", "ضخامت": "۱.۸ سانت", "ساب": "آیینه‌ای الماسه", "معدن": "دهبید فارس"}),

    ("سنگ مرمریت لاشتر طوسی بوش‌همر و ساب‌خورده", c_marmarit, 980000,
     "سنگ مرمریت محبوب لاشتر (معادل پیترا گری ایتالیا) با رنگ زمینه طوسی خنثی و خطوط سفید نازک. فرآوری بوش‌همر و چرمی بدون لغزندگی مناسب لابی، حیاط و استخر.",
     "معماران سبک مینیمال، طراحان فضاهای تجاری، رستوران‌ها و ویلاهای مدرن",
     {"ابعاد": "تایل ۶۰×۱۲۰ و ۴۰ طولی", "ضخامت": "۲ سانت", "فرآوری": "بوش‌همر و چرمی", "معدن": "اصفهان لاشتر"}),

    ("سنگ مرمریت پرشین سیلک (طوسی ابری ممتاز)", c_marmarit, 1150000,
     "مرمریت طوسی پرشین سیلک با هاله‌های ابری نقره‌ای و بافت استخوانی فشرده. انتخابی بی‌نظیر برای کف و بدنه راهروها، سرویس‌های مستر و فضای اداری لوکس.",
     "کارفرمایان پروژه‌های اداری و تجاری برند و ساختمان‌های نئوکلاسیک",
     {"ابعاد": "۶۰×۱۲۰ و ۸۰×۸۰", "ضخامت": "۱.۸ سانت", "فرآوری": "ساب براق صادراتی", "معدن": "فارس"}),

    ("سنگ مرمریت مشکی نجف‌آباد رگه‌دار (بلک‌پارس)", c_marmarit, 850000,
     "سنگ مرمریت مشکی عمیق با رگه‌های ظریف سفید برفی. کنتراست فوق‌العاده برای قاب آسانسور، جزیره آشپزخانه، کف لابی و سرویس‌های بهداشتی.",
     "طراحان دکوراسیون داخلی برای تضاد رنگی و قاب‌بندی‌های مدرن",
     {"ابعاد": "تایل ۶۰×۶۰ و عرض ۴۰ طولی", "ضخامت": "۱.۸ سانت", "فرآوری": "سوپر پولیش", "معدن": "نجف‌آباد"}),

    # 4. سنگ پله و پارکینگ
    ("سنگ پله مرمریت صلصالی کرم خردلی (ضخامت ۳ سانت ساب براق)", c_stairs, 420000,
     "سنگ پله متراکم مرمریت صلصالی با رگه‌های سینوسی و جذب آب بسیار پایین. لب‌شور و ساب خورده دقیق با ضخامت ۳ سانتی‌متر مقاوم در برابر تردد بالا.",
     "سازندگان مسکونی و تجاری برای راه پله اصلی، پاگرد و زیرپله",
     {"ابعاد": "عرض ۳۵ در طول ۱۱۰ تا ۱۳۰", "ضخامت": "۳ سانت کامل", "فرآوری": "ابزار نیم‌گرد و ساب", "معدن": "خوروبیابانک"}),

    ("سنگ پله مرمریت هرسین سفید کرم (ضخامت ۳ سانت)", c_stairs, 560000,
     "سنگ پله مرمریت هرسین کرمانشاه با بافت کریستالی بسیار متراکم و بدون پوسیدگی. مقاوم در برابر شوینده‌ها و تردد سنگین اداری.",
     "پیمانکاران ساختمان‌های اداری و برج‌های پرتردد",
     {"ابعاد": "عرض ۳۵ طول استاندارد", "ضخامت": "۳ سانت", "فرآوری": "ساب مات و براق", "معدن": "هرسین کرمانشاه"}),

    ("سنگ گرانیت مروارید مشهد (پله و کف پارکینگ ۴۰×۴۰)", c_granite, 340000,
     "اقتصادی‌ترین و سخت‌ترین سنگ برای کف پارکینگ، رمپ ورودی و راه پله‌های عمومی. ضد یخ‌زدگی، ضد خط و خش لاستیک ماشین و بدون شکستگی.",
     "پیمانکاران کف پارکینگ، رمپ، محوطه‌سازی شهری و ساختمان‌های پرتردد",
     {"ابعاد": "تایل ۴۰×۴۰ و پله ۳۵ سانت", "ضخامت": "۲ و ۳ سانت", "فرآوری": "ساب و فلیم", "معدن": "مشهد"}),

    ("سنگ گرانیت نهبندان طوسی گل‌پنبه‌ای", c_granite, 680000,
     "گرانیت باکیفیت و دانه درشت نهبندان با رنگ روشن و استحکام بی‌رقیب در برابر اسید، روغن و ضربه چرخ‌های خودروهای سنگین.",
     "سازندگان مجتمع‌های تجاری بزرگ، ایستگاه‌ها و پارکینگ‌های طبقاتی",
     {"ابعاد": "تایل ۶۰×۶۰ و ۴۰×۴۰", "ضخامت": "۲ سانت کامل", "فرآوری": "ساب آیینه‌ای", "معدن": "خراسان نهبندان"}),

    # 5. سنگ‌های اسلب لوکس بوک‌مچ
    ("سنگ اسلب چینی ازنا سوپر سفید (بوک‌مچ و فورمچ)", c_slab_crystal, 2200000,
     "سنگ اسلب شاهکار چینی کریستال ازنا با خطوط نامنظم خاکستری و مشکی روی زمینه سفید صدفی با قابلیت چینش قرینه (بوک‌مچ). جذب آب نزدیک به صفر.",
     "طراحان سالن‌های همایش، تی‌وی‌وال، کف لابی و سرویس‌های لوکس مستر",
     {"ابعاد": "اسلب ابعاد بزرگ ۲۸۰×۱۸۰", "ضخامت": "۲ سانت", "فرآوری": "توری و اپوکسی و ساب ایتالیایی", "معدن": "لرستان ازنا"}),

    ("سنگ اسلب چینی الیگودرز کریستال طرح مایا", c_slab_crystal, 1950000,
     "اسلب چینی الیگودرز با زمینه سفید یخی و رگه‌های مورب دودی و طلایی. قابلیت عبور نور در ضخامت‌های استاندارد و ایجاد تقارن بی‌نظیر.",
     "معماران لابی‌های مجلل، کانتر جزیره آشپزخانه و دیوار شاخص سالن",
     {"ابعاد": "اسلب ۲۹۰×۱۹۰", "ضخامت": "۲ سانت", "فرآوری": "اپوکسی دو رو و ساب براق", "معدن": "الیگودرز"}),

    ("سنگ اسلب کریستال قروه مشکی و سفید (سایمان)", c_slab_crystal, 2400000,
     "سنگ اسلب کریستال فوق‌العاده مستحکم سایمان قروه با طرح‌های هنری شبیه نقاشی آبرنگ. ساب‌پذیری بسیار بالا و جذب آب صفر.",
     "ویلاهای مدرن لواسان و شمال، لابی هتل‌ها و کانترهای اداری خاص",
     {"ابعاد": "اسلب ۲۷۰×۱۷۰", "ضخامت": "۲ سانت", "فرآوری": "پولیش براق نانو", "معدن": "کردستان قروه"}),

    # 6. سنگ دکوراتیو و محوطه
    ("سنگ گیوتین و آنتیک تراورتن قیچی (پشت ال‌سی‌دی و ستون)", c_deco, 290000,
     "سنگ‌های گیوتین و بادبر تراورتن با بافت طبیعی و برجسته ۳ بعدی. رنگ‌بندی گردویی، کرم و شکلاتی برای دکوراسیون داخلی، شومینه و دیوار پشت تلویزیون.",
     "دکوراتورها، نصابان کناف و مالکان بازسازی خانه",
     {"ابعاد": "تکه سنگ‌های ۵×۲۰ سانت توری شده", "فرآوری": "قیچی و گیوتین", "کاربری": "نمای داخلی و ستون"}),

    ("سنگ لاشه و مالون ورقه‌ای قهوه‌ای دماوند (محوطه و باغ)", c_deco, 180000,
     "سنگ لاشه طبیعی کوهی برای کف‌سازی محوطه ویلا، پله‌های باغی، ساخت استخر، آبنما و دیوارکشی بدون فرسایش در برابر یخبندان زمستان.",
     "محوطه‌سازان ویلا، باغ‌داران و پیمانکاران فضای سبز شهری",
     {"ضخامت": "۴ تا ۶ سانت ورقه‌ای", "فرآوری": "طبیعی کوهی چکشی", "واحد": "مترمربع / تن", "معدن": "دماوند"}),

    ("صراحی سنگی گرد و چهارگوش تراورتن (نرده بالکن و راه پله)", c_trav_super, 125000,
     "نرده‌های سنگی صراحی تراورتن تراشیده شده با دستگاه تمام اتوماتیک سی‌ان‌سی. مقاوم در برابر آفتاب و باد بدون ریزش یا زرد شدگی.",
     "سازندگان بالکن، روف گاردن و دور استخر",
     {"ابعاد": "ارتفاع ۷۰ سانت، مقطع ۱۲×۱۲", "فرآوری": "تراش CNC و ساب", "معدن": "تراورتن عباس‌آباد"}),
]

created_prods = []
for name, cat, price, desc, target, attrs in products_data:
    p, _ = Product.objects.update_or_create(
        business=biz, name=name,
        defaults={
            "category": cat,
            "price": price,
            "description": desc,
            "target_customer": target,
            "attributes": attrs,
            "product_type": "PHYSICAL",
            "status": "ACTIVE",
            "is_discovery_active": True,
            "discovery_priority": 5,
        }
    )
    created_prods.append(p)

print(f"Created/Updated {len(created_prods)} rich products for Building Stone Business.")

print("\n--- 5. Funding Wallet ---")
wallet = ensure_wallet(biz)
target_topup = Decimal("3000000")  # 3 million Toman credit
tx = apply_transaction(
    biz,
    target_topup,
    kind=WalletTransaction.GIFT,
    description="اعتبار هدیه راه‌اندازی و پایش تخصصی سنگ ساختمانی و سنگ نما",
    calls=0,
)
wallet.refresh_from_db()
print(f"Wallet updated: Balance = {wallet.balance_toman:,.2f} Toman, is_blocked = {wallet.is_blocked if hasattr(wallet, 'is_blocked') else 'N/A'}")
print("ALL DONE SUCCESS!")

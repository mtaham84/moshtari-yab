import os
import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.businesses.models import Business
from apps.products.models import Category, Product, ProductImage
from apps.discovery.models import Customer, Opportunity, AIAnalysis, OpportunityProductMatch

User = get_user_model()


class Command(BaseCommand):
    help = "Seed 2 realistic Iranian demo businesses with 25+ products each, full taxonomies, and Telegram leads."

    def handle(self, *args, **options):
        self.stdout.write(self.style.NOTICE("Starting demo businesses seeding..."))

        # -------------------------------------------------------------
        # Sample image paths available in media
        # -------------------------------------------------------------
        sample_images = [
            "products/2026/10/img1.jpg",
            "products/2026/10/img2.jpg",
            "products/2026/10/img_0.jpg",
        ]

        # =============================================================
        # BUSINESS 1: BaristaPro Equipment (تجهیزات و قهوه تخصصی باریستا)
        # =============================================================
        user_coffee, _ = User.objects.get_or_create(
            username="barista@peyda.ir",
            defaults={
                "email": "barista@peyda.ir",
                "first_name": "احسان",
                "last_name": "باریستا",
                "is_active": True,
            }
        )
        user_coffee.set_password("Barista_2026_Demo!")
        user_coffee.save()

        biz_coffee, _ = Business.objects.update_or_create(
            user=user_coffee,
            defaults={
                "name": "فروشگاه تخصصی تجهیزات قهوه و باریستا آرت",
                "business_type": "PHYSICAL",
                "business_domain": "تجهیزات کافی‌شاپ، رست و اکسسوری قهوه",
                "telegram_account_handle": "@BaristaPro_Iran",
                "daily_discovery_limit": 50,
            }
        )
        self.stdout.write(self.style.SUCCESS(f"Business 1 ready: {biz_coffee.name}"))

        # Category Tree for Coffee
        c_root_equip, _ = Category.objects.get_or_create(
            business=biz_coffee, name="تجهیزات کافی‌شاپ", parent=None,
            defaults={"depth": 1, "product_type": "PHYSICAL"}
        )
        c_espresso, _ = Category.objects.get_or_create(
            business=biz_coffee, name="دستگاه اسپرسوساز", parent=c_root_equip,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_home_espresso, _ = Category.objects.get_or_create(
            business=biz_coffee, name="خانگی و نیمه‌صنعتی", parent=c_espresso,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )
        c_commercial_espresso, _ = Category.objects.get_or_create(
            business=biz_coffee, name="صنعتی و تجاری کافه", parent=c_espresso,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )

        c_grinder, _ = Category.objects.get_or_create(
            business=biz_coffee, name="آسیاب قهوه (گریندر)", parent=c_root_equip,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_manual_grinder, _ = Category.objects.get_or_create(
            business=biz_coffee, name="آسیاب دستی مخروطی", parent=c_grinder,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )
        c_electric_grinder, _ = Category.objects.get_or_create(
            business=biz_coffee, name="آسیاب برقی و آندیمند", parent=c_grinder,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )

        c_tools, _ = Category.objects.get_or_create(
            business=biz_coffee, name="اکسسوری و ابزار باریستا", parent=c_root_equip,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_tamper, _ = Category.objects.get_or_create(
            business=biz_coffee, name="تمپر، لولر و مت", parent=c_tools,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )
        c_pitcher, _ = Category.objects.get_or_create(
            business=biz_coffee, name="پیچر شیر و لاته آرت", parent=c_tools,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )

        c_root_beans, _ = Category.objects.get_or_create(
            business=biz_coffee, name="دان قهوه و ملزومات", parent=None,
            defaults={"depth": 1, "product_type": "PHYSICAL"}
        )
        c_beans, _ = Category.objects.get_or_create(
            business=biz_coffee, name="دان قهوه تخصصی و ترکیبی", parent=c_root_beans,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_syrup, _ = Category.objects.get_or_create(
            business=biz_coffee, name="سیروپ و طعم‌دهنده باریستا", parent=c_root_beans,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )

        # 25 Products for Coffee
        coffee_products_data = [
            # Home Espresso
            ("اسپرسوساز نووا مدل ۱۴۹ (Nova 149)", c_home_espresso, 6400000,
             "اسپرسوساز نیمه‌صنعتی نووا ۱۴۹ با توان ۱۰۵۰ وات و پمپ فشار ۱۵ بار ایتالیایی اولکا. مناسب مصارف خانگی، دفتر کار و کافه‌های کوچک بیرون‌بر با خروجی کرما غلیظ و ماندگار.",
             "علاقه‌مندان به قهوه خانگی، دفترهای اداری، کافه‌های کوچک", {"پمپ": "۱۵ بار اولکا", "توان": "۱۰۵۰ وات", "بسکت": "۵۱ میلیمتر", "جنس بدنه": "استیل ضدزنگ"}),
            ("اسپرسوساز مباشی مدل ۲۰۲۰ (Mebashi 2020)", c_home_espresso, 7200000,
             "دستگاه اسپرسوساز مباشی مدل ۲۰۲۰ دارای پرتافیلتر سایز ۵۱، فشار ۲۰ بار و پیچ تنظیم بخار قدرتمند برای فوم‌گیری سریع لاته و کاپوچینو.",
             "کافی‌لاورهای خانگی و هوم باریستاها", {"فشار": "۲۰ بار", "توان": "۱۳۵۰ وات", "بویلر": "آلومینیوم دایکست"}),
            ("اسپرسوساز دلونگی ددیکا مدل ۶۸۵ (EC685)", c_home_espresso, 9800000,
             "اسپرسوساز فوق باریک و محبوب دلونگی EC685 با بدنه استیل مات، سیستم ترموبلاک پرسرعت و امکان تنظیم دمای خروجی عصاره‌گیری.",
             "علاقه‌مندان به لوازم خانگی شیک و عصاره‌گیری استاندارد", {"عرض دستگاه": "۱۵ سانتیمتر", "سیستم حرارتی": "ترموبلاک", "کشور برند": "ایتالیا"}),
            ("اسپرسوساز جیمیلای مدل ۳۶۰۵ (Gemilai 3605)", c_home_espresso, 12500000,
             "دستگاه نیمه‌صنعتی جیمیلای ۳۶۰۵ با پرتافیلتر صنعتی سایز ۵۸، گیج نمایشگر فشار و قابلیت پری‌اینفیوژن (پیش‌عصاره‌گیری). گزینه‌ای بی‌رقیب برای کافه‌های اقتصادی.",
             "کافه‌های بیرون‌بر، هوم‌باریستاهای حرفه‌ای", {"سایز هدگروپ": "۵۸ میلیمتر استاندارد", "توان": "۱۴۵۰ وات", "گیج فشار": "دارد"}),
            ("اسپرسوساز جیمیلای مدل ۳۰۰۵ (Gemilai 3005E)", c_home_espresso, 15800000,
             "اسپرسوساز حرفه‌ای مجهز به بویلر استیل، کنترل دیجیتال دما PID و بدنه تمام استیل با توان تولید مداوم روزی تا ۶۰ شات قهوه.",
             "کافی‌شاپ‌های کوچک و غرفه‌های فروش آبمیوه بستنی", {"کنترل دما": "PID دیجیتال", "پرتافیلتر": "۵۸ استیل سنگین"}),

            # Commercial Espresso
            ("اسپرسوساز صنعتی سن‌رمو زوئی ۲ گروپ (Sanremo Zoe)", c_commercial_espresso, 245000000,
             "دستگاه اسپرسوساز ۲ گروپ تال‌کاپ ایتالیایی سن‌رمو با ظرفیت بویلر ۱۰ لیتری، نازل بخار کول‌تاچ و سیستم کنترل حجمی الکترونیکی. ساخته شده برای ترافیک کاری شلوغ.",
             "کافی‌شاپ‌های پر رفت‌وآمد و رستوران‌های بزرگ", {"تعداد گروپ": "۲ گروپ", "حجم بویلر": "۱۰ لیتر", "کشور سازنده": "ایتالیا", "تال‌کاپ": "بله"}),
            ("اسپرسوساز چیمبالی ام۲۷ ۲ گروپ (La Cimbali M27)", c_commercial_espresso, 220000000,
             "شاهکار دوام ایتالیا لاسیبمالی M27 RE با سیستم تبادل حرارتی دوال بویلر، سیستم پیش‌تزریق و پایدارترین دمای عصاره‌گیری در شلوغ‌ترین ساعات کاری.",
             "کافه‌داران حرفه‌ای و صاحبان فرانچایز", {"کشور": "ایتالیا", "بویلر": "۱۱ لیتر", "توان": "۴۵۰۰ وات"}),
            ("اسپرسوساز لاسپازیاله اس۲ ۲ گروپ (La Spaziale S2)", c_commercial_espresso, 195000000,
             "دستگاه حرفه‌ای با سیستم گرمایشی بخار انحصاری و گروپ‌های ۵۳ میلیمتری با عصاره‌گیری فشرده و بدنه استیل با نورپردازی LED.",
             "کافه‌های موج سوم و قهوه‌فروشی‌ها", {"گروپ": "۲ گروپ ۵۳ میلیمتر", "بویلر": "۱۰ لیتری", "ساخت": "بولونیا ایتالیا"}),

            # Manual Grinder
            ("آسیاب قهوه دستی تایم‌مور مدل C3 (Timemore C3)", c_manual_grinder, 3800000,
             "آسیاب دستی بسیار دقیق تایم‌مور C3 با تیغه ۳۸ میلیمتری اسپایک‌توکات استیل S2C660 و بدنه آلومینیومی آجدار. فوق‌العاده برای ایروپرس، وی۶۰ و فرنچ‌پرس.",
             "علاقه‌مندان به قهوه دمی، کمپرها و باریستاهای خانگی", {"جنس تیغه": "استیل ضدزنگ S2C", "ظرفیت هاپر": "۲۵ گرم", "درجه تنظیم": "کلیکی دقیق"}),
            ("آسیاب دستی ۱Zpresso مدل J-Max", c_manual_grinder, 9200000,
             "دقیق‌ترین آسیاب دستی جهان برای اسپرسو با دقت ۸.۴ میکرون در هر کلیک، تیغه تیتانیوم کونکال و محفظه مگنتی جمع‌آوری قهوه.",
             "عاشقان اسپرسوی دستی تخصصی و قهوه سینگل اوریجین", {"دقت تنظیم": "۸.۴ میکرون", "پوشش تیغه": "تیتانیوم", "تنظیم": "حلقه بیرونی"}),
            ("آسیاب دستی قهوه کینگ‌گریندر مدل K6 (Kingrinder K6)", c_manual_grinder, 6900000,
             "آسیاب تایوانی K6 با تیغه ۴۸ میلیمتری هفت‌پرخه هفت‌ضلعی؛ کارایی همزمان برای آسیاب ریز اسپرسو تا درشت کلدبرو.",
             "باریستاهای چندمنظوره و عاشقان قهوه موج سوم", {"قطر تیغه": "۴۸ میلیمتر", "جنس تیغه": "استیل حرارت‌دیده"}),

            # Electric Grinder
            ("آسیاب قهوه برقی آندیمند اوبل مدل میتوس (Obel Mito)", c_electric_grinder, 32000000,
             "آسیاب آندیمند ایتالیایی با تیغه‌های فلت ۶۴ میلیمتری، تنظیم تایمر دیجیتال تک‌شات و دو شات و لود سریع هاپر ۱.۲ کیلوگرمی.",
             "کافه‌های بیرون‌بر و شیفت‌های شلوغ", {"تیغه": "۶۴ میلیمتر فلت", "سرعت": "۱۴۰۰ دور در دقیقه", "صفحه نمایش": "لمسی دیجیتال"}),
            ("آسیاب قهوه باراتزا مدل سِته ۳۰ (Baratza Sette 30)", c_electric_grinder, 22500000,
             "آسیاب تخصصی خانگی و نیمه‌صنعتی با طراحی انقلابی عبور مستقیم پودر قهوه و صفر درصد ماندگاری (Zero Retention).",
             "هوم‌باریستاهای حرفه‌ای", {"تیغه": "کونکال ۴۰ میلیمتر AP", "مکانیزم": "موتور مستقیم بدون گیربکس"}),
            ("آسیاب قهوه کامپک مدل K3 تاچ (Compak K3 Touch)", c_electric_grinder, 28000000,
             "آسیاب اسپانیایی با تیغه ۵۸ میلیمتری فلت، سیستم سرمایش محفظه و تنظیم میکرومتریک پیوسته درجه پودر قهوه.",
             "کافه‌های اسپشالتی و باریستاهای مسابقاتی", {"تیغه": "۵۸ میلیمتر فلت", "تنظیم درجه": "میکرومتری استپ‌لس"}),
            ("آسیاب قهوه فیمار مدل فیورنزاتو اف۶۴ (Fiorenzato F64E)", c_electric_grinder, 54000000,
             "یکی از پرفروش‌ترین آسیاب‌های صنعتی دنیا با دیسپلی لمسی بزرگ، شمارشگر شات‌ها و سیستم خنک‌کننده اتوماتیک موتور.",
             "کافه‌های بزرگ و رستوران‌ها", {"تیغه": "۶۴ میلیمتر", "کنترل": "صفحه لمسی رنگی", "ساخت": "ایتالیا"}),

            # Tamper & Tools
            ("تمپر کالیبره فنری سایز ۵۸ میلیمتر نورمکور (Normcore)", c_tamper, 2400000,
             "تمپر تحت فشار فنری با وزن استاندارد ۳۰ پوند، هدایتگر تراز افقی و سازگار با انواع بسکت‌های صنعتی IMS و VST.",
             "باریستاها جهت تمپ یکنواخت و جلوگیری از چنلینگ", {"سایز": "۵۸.۵ میلیمتر", "فشار فنر": "۳۰ پوند", "پایه": "تخت استیل ۳۰۴"}),
            ("لولر و دیستریبیوتر قهوه سایز ۵۱ طرح گل (Distributor)", c_tamper, 950000,
             "ابزار توزیع یکدست پودر قهوه برای دستگاه‌های خانگی نوا و مباشی با قابلیت تنظیم عمق نفوذ پره‌ها.",
             "دارندگان دستگاه‌های اسپرسوساز خانگی", {"قطر": "۵۱ میلیمتر", "طرح": "۳ پره قابل تنظیم"}),
            ("نیدل و ویز دیستریبیوشن تول WDT مدل پایه فلزی", c_tamper, 680000,
             "سوزن بازکننده کلوخه‌های پودر قهوه با سوزن‌های باریک ۰.۳۵ میلیمتری استیل برای عصاره‌گیری بدون کانالینگ و حداکثر بادی قهوه.",
             "باریستاهای تخصصی و مسابقه‌ای", {"تعداد سوزن": "۸ عدد", "ضخامت": "۰.۳۵ میلیمتر"}),

            # Pitchers
            ("پیچر لاته آرت موتا مدل اروپا ۵۰۰ میلی‌لیتر (Motta Europa)", c_pitcher, 1850000,
             "پیچر اصل ایتالیایی استیل ۱۸/۱۰ سنگین با دماغه کشیده و ارگونومیک برای اجرای پترن‌های پیچیده رزتا و سوان لاته آرت.",
             "باریستاهای لاته آرت کار و کافی‌شاپ‌ها", {"حجم": "۵۰۰ میلی‌لیتر", "جنس": "استیل ضدزنگ ۱۸/۱۰", "ساخت": "ایتالیا"}),
            ("پیچر تفلون شیر ۳۵۰ میلی‌لیتر باریستا اسپیس (Barista Space)", c_pitcher, 1250000,
             "پیچر با پوشش تفلون نچسب مشکی مات، دهانه نوک‌تیز لیزری و دستگیره راحت برای فوم‌گیری سریع سینگل کاپوچینو.",
             "کافه‌ها و استفاده خانگی", {"حجم": "۳۵۰ میلی‌لیتر", "روکش": "تفلون صنعتی نچسب"}),

            # Coffee Beans
            ("دان قهوه تخصصی اتیوپی یرگاچف ۲۵۰ گرمی (موج سوم)", c_beans, 420000,
             "دان ۱۰۰٪ عربیکا سینگل اوریجین اتیوپی با فرآوری شسته، اسیدیته زنده مرکباتی، نوت‌های یاسمن و ترنج با رست مدیوم لایت تازه.",
             "علاقه‌مندان به طعم‌های میوه‌ای و قهوه دمی تخصصی", {"خاستگاه": "یرگاچف اتیوپی", "ارتفاع": "۲۰۰۰ متر", "نوت طعمی": "یاسمن، لیمو، ترنج"}),
            ("دان قهوه ترکیبی ۷۰٪ روبوستا ۳۰٪ عربیکا بارستا کرما (۱ کیلو)", c_beans, 750000,
             "بلند پرکافئین و پرکرما مناسب اسپرسوسازهای خانگی و کافه‌های تجاری با بادی سنگین، طعم‌یاد کاکائو تلخ و کرمای فندقی ضخیم.",
             "مصرف‌کنندگان روزانه اسپرسو با کافئین بالا", {"ترکیب": "۷۰٪ روبوستا اوگاندا - ۳۰٪ عربیکا برزیل", "بادی": "بسیار سنگین", "کافئین": "بالا"}),
            ("دان قهوه کلمبیا سوپریمو ۱۰۰٪ عربیکا (۵۰۰ گرمی)", c_beans, 620000,
             "قهوه نام‌آشنای کلمبیا با اسیدیته ملایم، بادی متوسط، طعم‌یادهای کارامل و فندق برشته با رست مدیوم استاندارد.",
             "مصرف روزمره اسپرسو و آمریکانو", {"گونه": "۱۰۰٪ عربیکا کلمبیا", "فرآوری": "شسته", "نوت": "کارامل، شکلات شیری"}),

            # Syrups
            ("سیروپ وانیل ماداگاسکار توسانی ۱۰۰۰ میلی‌لیتر (Teisseire)", c_syrup, 480000,
             "سیروپ غلیظ پایه طبیعی مناسب سیروپ لاته، موکا و انواع ماکیاتو با غلظت عالی و بدون شیرین‌کننده مصنوعی.",
             "کافی‌شاپ‌ها و بار نوشیدنی‌های گرم و سرد", {"حجم": "۱۰۰۰ میلی‌لیتر", "طعم": "وانیل خالص ماداگاسکار"}),
            ("سیروپ کارامل نمکی مانین ۷۰۰ میلی‌لیتر (Monin)", c_syrup, 590000,
             "محبوب‌ترین سیروپ جهان از برند فرانسوی مونین برای تهیه سالتد کارامل لاته و فراپوچینو با بافت ابریشمی.",
             "کافی‌شاپ‌ها و میکسولوژیست‌ها", {"برند": "Monin فرانسه", "حجم": "۷۰۰ میلی‌لیتر", "طعم": "کارامل شور"}),
        ]

        saved_coffee_prods = []
        for name, cat, price, desc, target, attrs in coffee_products_data:
            p, _ = Product.objects.update_or_create(
                business=biz_coffee, name=name,
                defaults={
                    "category": cat,
                    "price": price,
                    "description": desc,
                    "target_customer": target,
                    "attributes": attrs,
                    "product_type": "PHYSICAL",
                    "status": "ACTIVE",
                    "is_discovery_active": True,
                    "discovery_priority": random.randint(3, 5),
                }
            )
            # Add image if not exists
            if not p.images.exists():
                img_rel = random.choice(sample_images)
                ProductImage.objects.create(product=p, image=img_rel, is_main=True)
            saved_coffee_prods.append(p)

        self.stdout.write(self.style.SUCCESS(f"Created {len(saved_coffee_prods)} products for Coffee Business."))

        # Zero fake leads: real crawler & need_engine only
        Opportunity.objects.filter(business=biz_coffee).delete()
        Customer.objects.filter(business=biz_coffee).delete()
        self.stdout.write(self.style.SUCCESS("Coffee seller catalog ready (0 fake leads, waiting for live crawler opportunities)."))


        # =============================================================
        # BUSINESS 2: Paytakht Stock Laptops (دیجیتال استوک پایتخت)
        # =============================================================
        user_digi, _ = User.objects.get_or_create(
            username="digital@peyda.ir",
            defaults={
                "email": "digital@peyda.ir",
                "first_name": "محمدرضا",
                "last_name": "پایتخت",
                "is_active": True,
            }
        )
        user_digi.set_password("Digital_2026_Demo!")
        user_digi.save()

        biz_digi, _ = Business.objects.update_or_create(
            user=user_digi,
            defaults={
                "name": "مرکز دیجیتال و لپ‌تاپ استوک پایتخت",
                "business_type": "PHYSICAL",
                "business_domain": "لپ‌تاپ‌های استوک اروپایی گرید A++، مانیتور و تجهیزات IT",
                "telegram_account_handle": "@Paytakht_Stock_Laptops",
                "daily_discovery_limit": 50,
            }
        )
        self.stdout.write(self.style.SUCCESS(f"Business 2 ready: {biz_digi.name}"))

        # Category Tree for Digital
        c_root_lap, _ = Category.objects.get_or_create(
            business=biz_digi, name="لپ‌تاپ و اولترابوک استوک", parent=None,
            defaults={"depth": 1, "product_type": "PHYSICAL"}
        )
        c_eng_lap, _ = Category.objects.get_or_create(
            business=biz_digi, name="مهندسی، برنامه‌نویسی و اداری", parent=c_root_lap,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_game_lap, _ = Category.objects.get_or_create(
            business=biz_digi, name="گیمینگ و رندرینگ سه‌بعدی", parent=c_root_lap,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_ultra_lap, _ = Category.objects.get_or_create(
            business=biz_digi, name="اولترابوک سبک و مدیریتی", parent=c_root_lap,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )

        c_root_parts, _ = Category.objects.get_or_create(
            business=biz_digi, name="مانیتور و لوازم جانبی", parent=None,
            defaults={"depth": 1, "product_type": "PHYSICAL"}
        )
        c_monitors, _ = Category.objects.get_or_create(
            business=biz_digi, name="مانیتور استوک بدون فریم", parent=c_root_parts,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_accessories, _ = Category.objects.get_or_create(
            business=biz_digi, name="داک‌استیشن و شارژر اورجینال", parent=c_root_parts,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )

        # 25 Products for Digital
        digital_products_data = [
            # Engineering & Programming
            ("لپ‌تاپ لنوو تینک‌پد T480 (ThinkPad T480)", c_eng_lap, 23500000,
             "لپ‌تاپ محبوب برنامه‌نویسان با پردازنده Core i7 8650U، رم ۱۶ گیگ DDR4، اس‌اس‌دی ۵۱۲ گیگ NVMe، باتری دوبل، کیبورد افسانه‌ای ضدآب و پورت Type-C تاندربولت.",
             "برنامه‌نویسان پایتون، مهندسان نرم‌افزار و کارشناسان شبکه",
             {"پردازنده": "Core i7-8650U", "رم": "16GB DDR4", "حافظه": "512GB SSD NVMe", "صفحه نمایش": "14 اینچ IPS FHD", "گرید": "A++ استوک اروپا"}),
            ("لپ‌تاپ لنوو تینک‌پد T14 نسل ۲ (ThinkPad T14 Gen 2)", c_eng_lap, 34500000,
             "شاهکار مهندسی با پردازنده Ryzen 7 PRO 5850U هشت هسته‌ای، رم ۳۲ گیگابایت، گرافیک رادئون و حسگر اثرانگشت. ایده‌آل برای محیط‌های مجازی و داکر.",
             "توسعه‌دهندگان بک‌اند، تحلیل‌گران داده و دانشجویان مهندسی",
             {"پردازنده": "AMD Ryzen 7 PRO 5850U", "رم": "32GB DDR4", "حافظه": "1TB SSD", "صفحه نمایش": "14 FHD مات"}),
            ("لپ‌تاپ دل لتیتود ۵۴۲۰ (Dell Latitude 5420)", c_eng_lap, 28000000,
             "لپ‌تاپ با پردازنده Core i5 نسل ۱۱، رم ۱۶ گیگابایت قابل ارتقا، وبکم با شاتر فیزیکی، وزن سبک ۱.۴ کیلوگرم و شارژدهی تا ۸ ساعت.",
             "کارشناسان دیجیتال مارکتینگ و شرکت‌های استارتاپی",
             {"پردازنده": "Core i5-1145G7", "رم": "16GB", "حافظه": "512GB SSD", "وزن": "۱.۴ کیلوگرم"}),
            ("لپ‌تاپ اچ‌پی الیت‌بوک ۸۴۰ جی۷ (HP EliteBook 840 G7)", c_eng_lap, 31000000,
             "بدنه تمام آلومینیومی براق نقره‌ای، کیبورد با بکلایت سفید، سیستم صوتی بنگ اند اولوفسن و وبکم مادون قرمز ویندوز هلو.",
             "مدیران، وکلا و فریلنسرها",
             {"پردازنده": "Core i7-10610U", "رم": "16GB", "حافظه": "512GB NVMe", "اسپیکر": "Bang & Olufsen"}),
            ("لپ‌تاپ دل پرسیژن ۵۵۳۰ (Dell Precision 5530 Workstation)", c_eng_lap, 42000000,
             "ایستگاه کاری قدرتمند و فوق باریک با نمایشگر ۴K لمسی، رم ۳۲ گیگ، کارت گرافیک Nvidia Quadro P2000 با حافظه ۴ گیگ مجزا برای سالیدورکس و اتوکد.",
             "مهندسان مکانیک، معماران و طراحان CAD",
             {"پردازنده": "Core i7-8850H (6 Cores)", "گرافیک": "Quadro P2000 4GB", "نمایشگر": "15.6 4K Touch"}),

            # Gaming & Rendering
            ("لپ‌تاپ لنوو لژیون ۵ (Lenovo Legion 5)", c_game_lap, 5600000,
             "لپ‌تاپ گیمینگ قدرتمند مجهز به پردازنده Ryzen 7 5800H، رم ۳۲ گیگ، کارت گرافیک RTX 3060 با توان ۱۳۰ وات و نمایشگر ۱۶۵ هرتز sRGB 100%.",
             "گیمرها، رندرهای معماری ۳D Max و ادیتورهای پریمیر",
             {"پردازنده": "Ryzen 7 5800H", "گرافیک": "Nvidia RTX 3060 6GB 130W", "رفرش‌ریت": "165Hz", "رم": "32GB"}),
            ("لپ‌تاپ ایسوس راگ زفیروس جی۱۴ (ASUS ROG Zephyrus G14)", c_game_lap, 62000000,
             "اولترابوک گیمینگ ۱۴ اینچی بی‌رقیب با وزن ۱.۶ کیلوگرم، پردازنده Ryzen 9، گرافیک RTX 3060 و باتری پرظرفیت ۷۶ وات‌ساعت.",
             "گیمرهای پر رفت‌وآمد و تدوین‌گران پروژه‌های یوتیوب",
             {"پردازنده": "AMD Ryzen 9 5900HS", "گرافیک": "RTX 3060", "وزن": "۱.۶ کیلوگرم", "باتری": "76Wh"}),
            ("لپ‌تاپ اچ‌پی اومن ۱۶ (HP Omen 16)", c_game_lap, 52000000,
             "طراحی شیک و مینیمال بدون زرق و برق اغراق‌آمیز، خنک‌کننده تمپست کولینگ، گرافیک RTX 3060 و کیبورد مکانیکی ۴ زون RGB.",
             "برنامه‌نویسان هوش مصنوعی و گیمرهای رقابتی",
             {"پردازنده": "Core i7-11800H", "گرافیک": "RTX 3060 6GB", "صفحه نمایش": "16.1 144Hz"}),
            ("لپ‌تاپ اچ‌پی زدبوک فیوری ۱۵ جی۷ (HP ZBook Fury 15 G7)", c_game_lap, 68000000,
             "ورک‌استیشن غول‌پیکر استوک آمریکا با کارت گرافیک Nvidia Quadro RTX 3000 با ۶ گیگ GDDR6، قابلیت ارتقای رم تا ۱۲۸ گیگابایت.",
             "انیماتورها، شبیه‌سازهای هوش مصنوعی و استودیوهای رندرینگ",
             {"پردازنده": "Core i7-10850H", "گرافیک": "Quadro RTX 3000 6GB", "تعداد اسلات رم": "۴ اسلات تا 128GB"}),

            # Ultrabook & Slim
            ("مک‌بوک ایر اپل M1 استوک گرید A++ (MacBook Air M1)", c_ultra_lap, 46500000,
             "مک‌بوک ایر با چیپ افسانه‌ای اپل سیلیکون M1، باتری با شارژدهی ۱۸ ساعته بدون فن، رنگ اسپیس‌گری تمیز بدون خط و خش با سایکل زیر ۵۰.",
             "طراحان محصول، توسعه‌دهندگان iOS و مدیران ارشد",
             {"پردازنده": "Apple M1 Chip 8-Core", "رم": "8GB Unified", "حافظه": "256GB SSD", "وزن": "۱.۲۹ کیلوگرم"}),
            ("مایکروسافت سرفیس لپ‌تاپ ۴ (Surface Laptop 4)", c_ultra_lap, 38500000,
             "لپ‌تاپ لوکس با روکش پارچه آلکانترا، نسبت تصویر ۳:۲ برای مطالعه و کدنویسی، تاچ‌اسکرین ۱۰ نقطه و عمر باتری فوق‌العاده.",
             "مدیران عامل، اساتید دانشگاه و مشاوران کسب‌وکار",
             {"پردازنده": "Core i7-1185G7", "رم": "16GB LPDDR4x", "نمایشگر": "PixelSense 13.5 Touch"}),
            ("لپ‌تاپ دل ایکس‌پی‌اس ۱۳ مدل ۹۳۱۰ (Dell XPS 13 9310)", c_ultra_lap, 49000000,
             "باریک‌ترین حاشیه نمایشگر اینفینیتی‌اج در جهان، بدنه آلومینیوم ماشین‌کاری‌شده CNC با جای دست فیبرکربن و پردازنده نسل ۱۱ Evo.",
             "حرفه‌ای‌های در سفر و مدیران محصول",
             {"پردازنده": "Core i7-1165G7", "رم": "16GB", "حافظه": "1TB NVMe", "وزن": "۱.۲ کیلوگرم"}),

            # Monitors
            ("مانیتور ۲۷ اینچ دل اولتراشارپ 4K مدل U2720Q", c_monitors, 22500000,
             "مانیتور مرجع طراحان گرافیک و UI با رزولوشن 4K UHD، پوشش رنگی ۹۵٪ DCI-P3، پورت Type-C با توان شارژ لپ‌تاپ تا ۹۰ وات و پایه آسانسوری عمودی.",
             "طراحان UI/UX، تدوین‌گران و برنامه‌نویسان حرفه‌ای",
             {"سایز": "۲۷ اینچ", "پنل": "IPS 4K UHD", "پوشش رنگ": "95% DCI-P3", "پورت": "USB-C 90W PD, HDMI, DP"}),
            ("مانیتور ۲۴ اینچ دل مدل P2419H بدون فریم استوک", c_monitors, 6900000,
             "مانیتور محبوب کارمندی و برنامه‌نویسی با پایه آسانسوری و چرخش ۹۰ درجه پیوت، بدون لرزش تصویر (Flicker-Free) و نور آبی کم.",
             "برنامه‌نویسان برای مانیتور عمودی دوم، تریدرها و دورکارها",
             {"سایز": "۲۴ اینچ", "پنل": "IPS Full HD", "پایه": "آسانسوری با چرخش ۹۰ درجه", "ورودی": "HDMI, DisplayPort, VGA"}),
            ("مانیتور ۳۴ اینچ اولتراواید ال‌جی مدل 34WN700", c_monitors, 26000000,
             "نمایشگر عریض ۲۱:۹ با رزولوشن WQHD (3440x1440)، مناسب مالتی‌تسکینگ همزمان، تایم‌لاین پریمیر و مشاهده همزمان چند سورس‌کد.",
             "تریدرهای بازار مالی، برنامه‌نویسان و تدوین‌گران",
             {"سایز": "۳۴ اینچ ۲۱:۹", "رزولوشن": "WQHD 3440x1440", "پنل": "IPS HDR10"}),
            ("مانیتور ۲۷ اینچ اچ‌پی مدل E273q رزولوشن 2K", c_monitors, 11500000,
             "مانیتور استوک تمیز اروپایی با وضوح QHD (2560x1440)، فریم فوق‌باریک میکرو‌اج ۳ طرفه و پورت‌های متعدد USB Hub.",
             "طراحان وب و کاربران چندرسانه‌ای",
             {"سایز": "۲۷ اینچ 2K", "رزولوشن": "2560x1440", "پنل": "IPS مات ضدتابش"}),

            # Accessories & Docks
            ("داک‌استیشن دل مدل WD19 تاندربولت تایپ‌سی ۱۳۰ وات", c_accessories, 5800000,
             "داک اورجینال دل با پشتیبانی از اتصال همزمان ۳ مانیتور اکسترنال، پورت شبکه گیگابیت، جک صدا و شارژ سریع لپ‌تاپ با کابل تایپ‌سی.",
             "کسانی که لپ‌تاپ را به چند مانیتور و تجهیزات رومیزی متصل می‌کنند",
             {"توان خروجی": "۱۳۰ وات", "اتصال": "USB-C Thunderbolt", "خروجی تصویر": "2x DP, 1x HDMI, 1x Type-C"}),
            ("شارژر اورجینال لنوو ۶۵ وات تایپ‌سی (Type-C 65W)", c_accessories, 1450000,
             "آداپتور فابریک تینک‌پد با کابل تایپ‌سی تقویت‌شده، محافظت در برابر نوسان برق و سازگار با انواع لپ‌تاپ‌ها و گوشی‌های سامسونگ و شیائومی.",
             "دارندگان لپ‌تاپ‌های جدید لنوو، ایسوس و اپل",
             {"توان": "۶۵ وات", "سوکت": "USB Type-C", "ولتاژ": "20V - 3.25A"}),
            ("کیبورد مکانیکی سیمی ردراگون مدل K552 سوئیچ آبی", c_accessories, 2300000,
             "کیبورد مکانیکی جمع‌وجور بدون نام‌پد (TKL) با سوئیچ‌های مکانیکی پرکلیک، بدنه فلزی سنگین و نورپردازی قرمز ثابت.",
             "تایپیست‌ها، برنامه‌نویسان و گیمرها",
             {"سوئیچ": "آبی مکانیکی اوتمو", "طرح": "TKL 87 کلید", "کابل": "روکش کنفی ضخیم"}),
            ("کیبورد و ماوس بی‌سیم لاجیتک مدل MK270 اورجینال", c_accessories, 1850000,
             "ست کم‌مصرف لاجیتک با برد بی‌سیم ۱۰ متر، طول عمر باتری تا ۲۴ ماه و کلیدهای میانبر مالتی‌مدیا.",
             "محیط‌های اداری و خانگی",
             {"اتصال": "دانگل USB 2.4GHz", "برد": "۱۰ متر", "برند": "Logitech"}),
            ("شارژر مگ‌سیف ۲ اپل ۸۵ وات فابریک استوک", c_accessories, 1950000,
             "آداپتور اورجینال مگ‌سیف ۲ مخصوص مک‌بوک پرو رتینا ۱۵ اینچ ۲۰۱۲ تا ۲۰۱۵ با سوکت آهنربایی تی‌شکل و چراغ وضعیت شارژ.",
             "دارندگان مک‌بوک پروهای کلاسیک",
             {"توان": "۸۵ وات", "سوکت": "MagSafe 2 (T-Tip)", "برند": "Apple اورجینال"}),
            ("تبدیل چندکاره تایپ‌سی یوگرین ۸ در ۱ (UGREEN 8-in-1 Hub)", c_accessories, 2900000,
             "هاب آلومینیومی با خروجی HDMI 4K، سه پورت USB 3.0، رم‌ریدر SD/TF، پورت شبکه LAN و شارژر عبوری ۱۰۰ وات PD.",
             "دارندگان مک‌بوک، سرفیس و لپ‌تاپ‌های فاقد پورت‌های قدیمی",
             {"پورت‌ها": "8 پورت کامل", "جنس": "آلیاژ آلومینیوم خاکستری فضایی"}),
            ("پایه خنک‌کننده لپ‌تاپ دیپ‌کول مدل N8 آلومینیومی", c_accessories, 1650000,
             "کول‌پد تمام فلزی با دو فن ۱۴۰ میلیمتری کم‌صدا و بدنه مشبک خنک‌کننده برای لپ‌تاپ‌های تا ۱۷ اینچ.",
             "کاربران رندرینگ و گیمینگ طولانی‌مدت",
             {"تعداد فن": "۲ فن ۱۴ سانتی", "جنس پنل": "آلومینیوم اکسترود شده"}),
            ("کیف ضربه‌گیر دار لپ‌تاپ ۱۵.۶ اینچ کت (CAT Shockproof)", c_accessories, 950000,
             "کیف دوشی و دستی با فوم ضدضربه سلول‌بسته در تمام جهات، پارچه برزنتی ضدآب و زیپ‌های فلزی روان.",
             "دانشجویان و مهندسان در حال تردد",
             {"سایز مجاز": "تا ۱۵.۶ اینچ", "محافظ": "کپسول هوا و فوم حباب‌دار"}),
            ("ماوس بی‌سیم سایلنت لاجیتک مدل M220", c_accessories, 890000,
             "ماوس ارگونومیک بی‌صدا با ۹۰ درصد کاهش صدای کلیک، مناسب محیط‌های کتابخانه، اتاق جلسات و شیفت‌های شبانه.",
             "کارمندان اداری و دورکارها",
             {"نوع کلیک": "سایلنت بی‌صدا", "دقت": "1000 DPI", "باتری": "یک عدد قلمی تا ۱۸ ماه"}),
        ]

        saved_digi_prods = []
        for name, cat, price, desc, target, attrs in digital_products_data:
            p, _ = Product.objects.update_or_create(
                business=biz_digi, name=name,
                defaults={
                    "category": cat,
                    "price": price,
                    "description": desc,
                    "target_customer": target,
                    "attributes": attrs,
                    "product_type": "PHYSICAL",
                    "status": "ACTIVE",
                    "is_discovery_active": True,
                    "discovery_priority": random.randint(3, 5),
                }
            )
            if not p.images.exists():
                img_rel = random.choice(sample_images)
                ProductImage.objects.create(product=p, image=img_rel, is_main=True)
            saved_digi_prods.append(p)

        self.stdout.write(self.style.SUCCESS(f"Created {len(saved_digi_prods)} products for Digital Business."))

        # Zero fake leads: real crawler & need_engine only
        Opportunity.objects.filter(business=biz_digi).delete()
        Customer.objects.filter(business=biz_digi).delete()
        self.stdout.write(self.style.SUCCESS("Digital seller catalog ready (0 fake leads, waiting for live crawler opportunities)."))

        self.stdout.write(self.style.SUCCESS(
            "\n" + "="*70 + "\n"
            "SUCCESS: Both demo seller accounts seeded with rich real products!\n"
            "1. Coffee Equipment: barista@peyda.ir / Barista_2026_Demo! (25 products)\n"
            "2. Digital Laptops:   digital@peyda.ir / Digital_2026_Demo! (25 products)\n"
            "Zero synthetic leads generated — ready for real crawler/need_engine discovery.\n"
            + "="*70
        ))


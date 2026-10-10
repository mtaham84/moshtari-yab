import os
import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.businesses.models import Business
from apps.products.models import Category, Product, ProductImage
from apps.discovery.models import Customer, Opportunity, AIAnalysis, OpportunityProductMatch, MonitoredCommunity
from apps.discovery.sources import PRIVATE, normalize_link

User = get_user_model()


class Command(BaseCommand):
    help = "Seed 2 realistic Iranian demo businesses (Konkur Consulting Institute & Paytakht Stock Laptops) with 25+ products each, taxonomies, and monitored communities."

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

        # Cleanup legacy coffee barista user if exists
        legacy_barista = User.objects.filter(username="barista@peyda.ir").first()
        if legacy_barista:
            Business.objects.filter(user=legacy_barista).delete()
            legacy_barista.delete()
            self.stdout.write(self.style.WARNING("Removed legacy coffee barista user."))

        # =============================================================
        # BUSINESS 1: Konkur Educational & Consulting Institute (ماز / قلم‌چی)
        # =============================================================
        user_konkur, _ = User.objects.get_or_create(
            username="konkur@peyda.ir",
            defaults={
                "email": "konkur@peyda.ir",
                "first_name": "علیرضا",
                "last_name": "مشاور کنکور",
                "is_active": True,
            }
        )
        user_konkur.set_password("Konkur_2026_Demo!")
        user_konkur.save()

        biz_konkur, _ = Business.objects.update_or_create(
            user=user_konkur,
            defaults={
                "name": "موسسه خدمات آموزشی و مشاوره کنکور گام برتر (ماز کنکور)",
                "business_type": "SERVICE",
                "business_domain": "مشاوره تخصصی کنکور، آزمون‌های آزمایشی، پکیج‌های نکته و تست، همایش‌های جمع‌بندی و انتخاب رشته",
                "telegram_account_handle": "@GambeBartar_Konkur",
                "daily_discovery_limit": 100,
            }
        )
        self.stdout.write(self.style.SUCCESS(f"Business 1 ready: {biz_konkur.name}"))

        # Category Tree for Konkur Institute
        # Root 1: مشاوره و برنامه‌ریزی
        c_root_mentoring, _ = Category.objects.get_or_create(
            business=biz_konkur, name="خدمات مشاوره و برنامه‌ریزی تخصصی کنکور", parent=None,
            defaults={"depth": 1, "product_type": "SERVICE"}
        )
        c_vip_mentoring, _ = Category.objects.get_or_create(
            business=biz_konkur, name="مشاوره رتبه‌سازی VIP و مربیگری تحصیلی", parent=c_root_mentoring,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_planning, _ = Category.objects.get_or_create(
            business=biz_konkur, name="برنامه‌ریزی هفتگی و مانیتورینگ روزانه", parent=c_root_mentoring,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_workshop, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کارگاه‌های مدیریت زمان و استرس آزمون", parent=c_root_mentoring,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )

        # Root 2: آزمون‌های آزمایشی
        c_root_exams, _ = Category.objects.get_or_create(
            business=biz_konkur, name="آزمون‌های آزمایشی و ارزیابی جامع", parent=None,
            defaults={"depth": 1, "product_type": "SERVICE"}
        )
        c_mock_exams, _ = Category.objects.get_or_create(
            business=biz_konkur, name="پکیج آزمون‌های شبیه‌ساز کنکور سراسری", parent=c_root_exams,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_exam_analysis, _ = Category.objects.get_or_create(
            business=biz_konkur, name="تحلیل آزمون و کارنامه ۵ بعدی", parent=c_root_exams,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )

        # Root 3: کلاس‌های آنلاین و نکته و تست
        c_root_classes, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کلاس‌های آنلاین، نکته و تست و جمع‌بندی", parent=None,
            defaults={"depth": 1, "product_type": "SERVICE"}
        )
        c_tajrobi_classes, _ = Category.objects.get_or_create(
            business=biz_konkur, name="پکیج کلاس‌های جامع کنکور تجربی", parent=c_root_classes,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_math_classes, _ = Category.objects.get_or_create(
            business=biz_konkur, name="پکیج کلاس‌های جامع کنکور ریاضی", parent=c_root_classes,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_ensani_classes, _ = Category.objects.get_or_create(
            business=biz_konkur, name="پکیج کلاس‌های جامع کنکور انسانی", parent=c_root_classes,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_golden_seminar, _ = Category.objects.get_or_create(
            business=biz_konkur, name="همایش‌های جمع‌بندی طلایی کنکور", parent=c_root_classes,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )

        # Root 4: انتخاب رشته
        c_root_selection, _ = Category.objects.get_or_create(
            business=biz_konkur, name="خدمات تخصصی انتخاب رشته کنکور", parent=None,
            defaults={"depth": 1, "product_type": "SERVICE"}
        )
        c_smart_selection, _ = Category.objects.get_or_create(
            business=biz_konkur, name="انتخاب رشته هوشمند سراسری و آزاد", parent=c_root_selection,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )

        # Root 5: جزوات و بانک تست
        c_root_books, _ = Category.objects.get_or_create(
            business=biz_konkur, name="جزوات رتبه‌ساز و بانک تست‌های استاندارد", parent=None,
            defaults={"depth": 1, "product_type": "PHYSICAL"}
        )
        c_tree_notes, _ = Category.objects.get_or_create(
            business=biz_konkur, name="جزوات نموداری و خلاصه نکات مباحث پرتکرار", parent=c_root_books,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_tricky_tests, _ = Category.objects.get_or_create(
            business=biz_konkur, name="بانک تست‌های خط‌به‌خط و دام‌دار کنکور", parent=c_root_books,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )

        # 27 Products & Services for Konkur Institute
        konkur_products_data = [
            # VIP Mentoring
            ("پکیج مشاوره و برنامه‌ریزی VIP کنکور تجربی (طرح رتبه‌ساز با رتبه‌های تک‌رقمی)", c_vip_mentoring, 24000000,
             "برنامه شخصی‌سازی شده هفتگی بر اساس نقاط قوت و ضعف داوطلب، تماس تلفنی هفتگی با رتبه تک‌رقمی کنکور سراسری، چک شبانه گزارش‌کار، آزمونک‌های تستی اختصاصی و هدایت انگیزشی تا روز آزمون.",
             "داوطلبان کنکور تجربی متقاضی قبولی در رشته‌های پزشکی، دندان‌پزشکی و داروسازی دانشگاه‌های برتر",
             {"مشاور": "رتبه زیر ۵۰ کنکور", "پشتیبانی": "روزانه و شبانه", "تماس": "هفتگی ۴۵ دقیقه", "تحلیل آزمون": "اختصاصی"}),

            ("پکیج مشاوره و برنامه‌ریزی VIP کنکور ریاضی (طرح نخبگان دانشگاه شریف)", c_vip_mentoring, 21000000,
             "برنامه‌ریزی مدون تست‌زنی سرعتی، تحلیل آزمون‌های دو هفته یک‌بار، آموزش استراتژی حل تست‌های مفهومی حسابان و فیزیک با رتبه‌های برتر دانشگاه صنعتی شریف و تهران.",
             "داوطلبان کنکور ریاضی متقاضی مهندسی کامپیوتر، برق و مکانیک دانشگاه‌های سراسری تهران",
             {"مشاور": "دانشجویان برتر شریف", "آزمونک": "هفتگی آنلاین", "تست‌زنی": "سرعتی و تحلیلی"}),

            ("پکیج مشاوره و برنامه‌ریزی VIP کنکور انسانی (طرح حقوق و فرهنگیان)", c_vip_mentoring, 19000000,
             "برنامه‌ریزی تخصصی دروس تحلیلی (فلسفه، منطق، اقتصاد و ریاضی انسانی)، روش‌های مرور مکرر فنون ادبی و عربی، تکنیک‌های تندخوانی و ارتقای تراز قلمچی و ماز.",
             "داوطلبان کنکور انسانی متقاضی قبولی در دانشگاه فرهنگیان، حقوق و روانشناسی",
             {"مشاور": "رتبه دو رقمی انسانی", "برنامه": "اختصاصی دوازدهم و پایه", "آزمون": "تحلیل کارنامه"}),

            ("طرح مشاوره و برنامه‌ریزی ماهانه کنکور (شروع سریع و تعیین سطح)", c_planning, 2800000,
             "دوره ۳۰ روزه برنامه‌ریزی فشرده، ۴ جلسه تلفنی با مشاور ارشد، رفع اشکال روش مطالعه، تعیین سطح علمی اولیه و اصلاح عادات مخرب درسی.",
             "داوطلبانی که می‌خواهند کیفیت مشاوره را قبل از ثبت‌نام سالانه ارزیابی کنند",
             {"مدت": "۱ ماه (۳۰ روز)", "جلسات": "۴ جلسه تلفنی", "برنامه": "شخصی‌سازی هفتگی"}),

            ("برنامه‌ریزی مطالعاتی شخصی‌سازی شده هفتگی + چک شبانه گزارش‌کار", c_planning, 1900000,
             "تنظیم جدول مطالعه روزانه بر اساس بودجه‌بندی آزمون‌های آزمایشی، کنترل دقیق ساعت مطالعه و تعداد تست در پایان هر شب توسط پشتیبان آموزشی.",
             "دانش‌آموزان نیازمند انضباط فردی و پیگیری مستمر در اجرای برنامه",
             {"پیگیری": "هر شب در تلگرام", "بودجه‌بندی": "منطبق با آزمون آزمایشی", "گزارش‌کار": "بررسی روزانه"}),

            ("کارگاه آنلاین مدیریت استرس، تمرکز و تکنیک‌های تست‌زنی در جلسه آزمون", c_workshop, 650000,
             "کارگاه ۴ ساعته با حضور روانشناس تحصیلی با سرفصل‌های تکنیک تنفس در جلسه، کنترل تپش قلب، دور اول تست‌زنی بدون توقف و تکنیک زمان‌های نقصانی.",
             "دانش‌آموزان کنکوری با افت تراز ناشی از استرس و بی‌دقتی در جلسه آزمون",
             {"مدت": "۴ ساعت آنلاین", "مدرس": "روانشناس تخصصی کنکور", "فایل ضبط‌شده": "دسترسی دائم"}),

            ("کارگاه استراتژی زمان‌های نقصانی و مدیریت دفترچه کنکور", c_workshop, 550000,
             "آموزش پیاده‌سازی متدهای استراتژی بازگشت، تکنیک ضربدر و منها و اولویت‌بندی سوالات در دفترچه‌های شماره ۱ و ۲ کنکور سراسری.",
             "تمامی داوطلبان کنکور برای بهینه‌سازی زمان در جلسه آزمون",
             {"نوع": "وبینار کاربردی", "تمرین": "شبیه‌سازی با آزمون نمونه"}),

            # Mock Exams
            ("پکیج آزمون‌های شبیه‌ساز کنکور سراسری ماز/قلم‌چی (۲۰ مرحله جامع)", c_mock_exams, 5900000,
             "۲۰ مرحله آزمون آزمایشی مطابق با آخرین تغییرات سازمان سنجش، سوالات تالیفی اساتید برند، تطابق حداکثری با کنکورهای اخیر، کارنامه تحلیلی ۵ بعدی و صدور رتبه کشوری.",
             "دانش‌آموزان سال دوازدهم و فارغ‌التحصیلان کنکوری تمامی رشته‌ها",
             {"تعداد مراحل": "۲۰ مرحله", "نوع": "آنلاین با امنیت بالا", "پاسخنامه": "تشریحی کامل و ویدئویی"}),

            ("پکیج آزمون‌های طلایی شبیه‌ساز کنکور اردیبهشت و تیر (۸ مرحله شبیه‌ساز فشرده)", c_mock_exams, 2900000,
             "۸ دوره شبیه‌ساز کامل دفترچه‌های کنکور دقیقا مشابه با سطح دشواری و بودجه‌بندی کنکور سراسری اخیر با تحلیل و رتبه‌بندی لحظه‌ای.",
             "متقاضیان شبیه‌سازی شرایط واقعی کنکور در ماه‌های پایانی منتهی به آزمون",
             {"تعداد": "۸ مرحله شبیه‌ساز", "سطح": "منطبق با آخرین کنکور", "پاسخنامه": "ویدئویی"}),

            ("آزمون‌های مبحثی و سنجش پیشرفت تحصیلی (ویژه مباحث پایه دهم و یازدهم)", c_mock_exams, 1800000,
             "مجموعه آزمون‌های تستی سرفصل به سرفصل برای تثبیت دروس پایه و شناسایی نقاط ضعف قبل از ورود به جمع‌بندی نهایی.",
             "دانش‌آموزان پایه‌های دهم و یازدهم و داوطلبان دارای ضعف در مباحث پایه",
             {"پوشش": "دهم و یازدهم کامل", "سبک سوالات": "تالیفی و کنکوری"}),

            ("پکیج تحلیل ویدئویی و تشریحی سوالات آزمون‌های آزمایشی", c_exam_analysis, 1400000,
             "تدریس ویدئویی خط به خط پاسخ تشریحی تمامی سوالات آزمون‌ها با بررسی دام‌های آموزشی و تکنیک‌های رد گزینه توسط اساتید رتبه برتر.",
             "دانش‌آموزان خواهان یادگیری نکات تستی از دل سوالات آزمون",
             {"فرمت": "ویدیو با کیفیت بالا", "نکات": "بررسی روش‌های حل سریع"}),

            ("کارنامه تحلیلی ۵ بعدی آزمون و تخمین دقیق رتبه کشوری کنکور", c_exam_analysis, 850000,
             "سیستم هوشمند تحلیل کارنامه با نمودار پیشرفت درس به درس، مقایسه تراز با میانگین پذیرفته‌شدگان سال‌های قبل و تخمین رتبه کشوری.",
             "دانش‌آموزان نیازمند شناخت نقاط قوت و ضعف آماری در هر مبحث",
             {"گزارش": "PDF تفصیلی + مشاوره", "تخمین رتبه": "بر اساس سهمیه مناطق"}),

            # Online Classes & Seminars
            ("پکیج جامع کلاس آنلاین زیست‌شناسی کنکور (دکترای زیست‌شناسی، خط‌به‌خط کتاب)", c_tajrobi_classes, 9800000,
             "آموزش ترکیبی و خط‌به‌خط کتاب‌های دهم، یازدهم و دوازدهم زیست، بررسی اشکال و تصاویر پنهان کتاب، حل بیش از ۲۰۰۰ تست تالیفی و سراسری با اساتید برند کشوری.",
             "داوطلبان تجربی متقاضی درصدهای بالای ۷۰ در زیست‌شناسی کنکور",
             {"ساعت تدریس": "۱۲۰ ساعت وبینار", "جزوه": "رنگی و نموداری", "آزمونک": "هفتگی"}),

            ("پکیج جامع کلاس آنلاین شیمی کنکور (مفاهیم، حفظیات و استوکیومتری سرعتی)", c_tajrobi_classes, 8900000,
             "آموزش فرمول‌های سرعتی مسائل شیمی، تکنیک حل استوکیومتری بدون مخرج مشترک، جدول تناوبی و حفظیات شیمی با متد تصویرسازی ذهنی.",
             "داوطلبان تجربی و ریاضی خواهان تسلط بر مسائل و مفاهیم شیمی",
             {"پوشش": "شیمی دهم، یازدهم، دوازدهم", "مسائل": "استوکیومتری، ترمودینامیک، اسیدباز"}),

            ("پکیج جامع کلاس آنلاین فیزیک کنکور (آموزش مفهومی و تست‌های چندمجهولی)", c_tajrobi_classes, 8500000,
             "تدریس مباحث مکانیک، حرکت‌شناسی، الکتریسیته ساکن و جاری، نوسان و امواج با انیمیشن‌های فیزیکی و متدهای محاسبات سریع عددی.",
             "داوطلبان رشته‌های تجربی و ریاضی متقاضی درصدهای بالا در فیزیک",
             {"مباحث": "فیزیک پایه و دوازدهم", "حل تست": "بیش از ۱۲۰۰ تست کنکور و تالیفی"}),

            ("پکیج جامع کلاس آنلاین ریاضی تجربی کنکور (صفر تا صد درصد با متد تست‌زنی)", c_tajrobi_classes, 7900000,
             "آموزش جامع ریاضی دهم تا دوازدهم ویژه تجربی‌ها، رفع چالش توابع، حد و پیوستگی، مثلثات و کاربرد مشتق با حل بانک تست‌های طلایی.",
             "دانش‌آموزان تجربی با چالش درصد پایین در درس ریاضی",
             {"سطح": "از پایه تا تست‌های دشوار", "رویکرد": "مفهومی و تکنیک‌های رد گزینه"}),

            ("پکیج کلاس آنلاین حسابان و ریاضیات پایه کنکور ریاضی (حسابان ۱ و ۲)", c_math_classes, 8200000,
             "آموزش عمیق معادلات، مثلثات، مشتق و انتگرال همراه با شبیه‌سازی تست‌های دشوار کنکورهای سال‌های اخیر برای داوطلبان رشته ریاضی.",
             "داوطلبان کنکور ریاضی متقاضی درصدهای رقابتی در حسابان",
             {"تعداد جلسات": "۳۵ جلسه آنلاین", "جزوه": "کدگذاری شده اختصاصی"}),

            ("پکیج کلاس آنلاین هندسه تحلیلی و گسسته کنکور ریاضی", c_math_classes, 6500000,
             "اثبات‌های سرعتی، هندسه پایه، ماتریس، مقاطع مخروطی، نظریه اعداد، گراف و ترکیبیات با روش‌های خلاقانه و قابل فهم تستی.",
             "دانش‌آموزان رشته ریاضی نیازمند درصد بالا در دروس مهارتی",
             {"پوشش": "هندسه ۱، ۲، ۳ و گسسته", "رویکرد": "حل تست‌های مفهومی"}),

            ("پکیج کلاس آنلاین فنون ادبی، عروض سماعی و آرایه‌های کنکور انسانی", c_ensani_classes, 5800000,
             "تسلط کامل بر اختیارات شاعری، وزن‌های عروضی سماعی و تاریخ ادبیات بدون فراموشی با متدهای کدگذاری تصویری و شعرخوانی روان.",
             "داوطلبان کنکور انسانی خواهان کسب بالاترین درصد در درس سرنوشت‌ساز فنون",
             {"روش تدریس": "عروض سماعی بدون تقطیع", "آرایه‌ها": "تکنیک رد گزینه سریع"}),

            ("پکیج کلاس آنلاین فلسفه و منطق کنکور انسانی (رمزگشایی تست‌های مفهومی)", c_ensani_classes, 5600000,
             "رمزگشایی از تست‌های چندمجهولی و مفهومی منطق دهم و فلسفه یازدهم و دوازدهم با بررسی ریزترین نکات پنهان متن کتاب درسی.",
             "داوطلبان کنکور انسانی برای تسلط بر ترازسازترین درس اختصاصی",
             {"پوشش": "منطق دهم + فلسفه ۱۱ و ۱۲", "تست": "بررسی بیش از ۱۰۰۰ تست دام‌دار"}),

            ("دوره طلایی «نکته و تست» کنکور (مرور سریع و پیش‌بینی ۴۰۰ تست کنکور)", c_golden_seminar, 6200000,
             "دوره فشرده ماه‌های اردیبهشت و خرداد، مرور سریع تمامی درس‌ها در قالب تست‌های تیپ‌بندی شده و بررسی ۴۰۰ تست احتمالی به همراه جزوه شب امتحان.",
             "تمامی داوطلبان کنکور برای جمع‌بندی نهایی و جهش درصدها در ماه آخر",
             {"زمان برگزاری": "اردیبهشت و خرداد", "پیش‌بینی سوالات": "تطابق بالای ۸۰٪ با کنکور"}),

            ("همایش آنلاین جمع‌بندی زیست‌شناسی کنکور (طرح فشرده ۲۴ ساعته)", c_golden_seminar, 1900000,
             "مرور یکپارچه زیست گیاهی، جانوری، ژنتیک و فیزیولوژی بدن انسان در ۴ جلسه ۶ ساعته فشرده ویژه روزهای پایانی کنکور.",
             "داوطلبان تجربی برای مرور سریع کل کتاب‌های درسی در کمترین زمان",
             {"مدت": "۲۴ ساعت ویدیوی فشرده", "جزوه": "خلاصه درختی کل زیست"}),

            # Major Selection
            ("پکیج انتخاب رشته هوشمند کنکور سراسری با نرم‌افزار تحلیلی رتبه برتر", c_smart_selection, 1200000,
             "نرم‌افزار انتخاب رشته بر اساس آمار قبولی ۱۰ سال اخیر کانون و سازمان سنجش، اعمال سهمیه‌های مناطق و ایثارگری، تحلیل بومی‌گزینی و اولویت‌بندی ۱۵۰ کد رشته.",
             "داوطلبان مجاز به انتخاب رشته در کنکور سراسری",
             {"پوشش": "تمام دوره‌های روزانه، شبانه، پردیس و فرهنگیان", "چیدمان": "۱۵۰ کد رشته بر اساس علاقه و شانس"}),

            ("جلسه مشاوره فردی و اختصاصی انتخاب رشته حضوری/آنلاین با مشاور ارشد", c_smart_selection, 3500000,
             "جلسه ۲ ساعته فردی با مشاور ارشد برای اولویت‌بندی دقیق رشته‌ها، تحلیل بازار کار، شرایط مهاجرت، دانشگاه‌های خاص، پزشکی و فرهنگیان.",
             "داوطلبان خواهان اطمینان صددرصدی از چیدمان بهینه و آینده‌نگرانه رشته‌ها",
             {"مشاور": "مشاور ارشد با ۱۰ سال سابقه", "مدت": "۲ ساعت اختصاصی"}),

            ("مشاوره و انتخاب رشته اختصاصی دانشگاه آزاد اسلامی و رشته‌های پزشکی آزاد", c_smart_selection, 1800000,
             "تحلیل رشته‌های باآزمون و با سوابق تحصیلی دانشگاه آزاد، ترازهای قبولی پزشکی، دندانپزشکی و پیراپزشکی آزاد و چیدمان بهینه فرم انتخاب رشته.",
             "متقاضیان تحصیل در واحدهای دانشگاه آزاد سراسر کشور",
             {"پوشش": "پزشکی، داروسازی، پیراپزشکی و مهندسی آزاد", "تحلیل": "تراز قبولی سال‌های قبل"}),

            # Books & Notes
            ("جزوه طلایی نمودارهای درختی و نقشه‌های ذهنی مباحث پرتکرار کنکور", c_tree_notes, 780000,
             "جزوه ۲۵۰ صفحه‌ای چاپ رنگی سیمی شامل نقشه‌های ذهنی، جداول مقایسه‌ای زیست، خلاصه‌های فرمول شیمی و روابط طلایی فیزیک.",
             "دانش‌آموزان کنکوری برای مرورهای سریع و تورق سریع شب آزمون",
             {"تعداد صفحات": "۲۵۰ صفحه رنگی", "فرمت": "فیزیکی سیمی + PDF"}),

            ("کتابچه بانک تست‌های دام‌دار و پرتکرار ۱۰ سال اخیر کنکور سراسری", c_tricky_tests, 850000,
             "مجموعه‌ای از ۱۲۰۰ تست دست‌چین شده که بیشترین درصد پاسخ اشتباه را در آزمون‌های سراسری داشته‌اند همراه با واکاوی دقیق دام‌های تستی طراحان کنکور.",
             "داوطلبانی که درصد منفی زیادی در آزمون‌های آزمایشی دارند",
             {"تعداد تست": "۱۲۰۰ تست", "پاسخنامه": "کاملا تشریحی و نکته‌محور"})
        ]

        saved_konkur_prods = []
        for name, cat, price, desc, target, attrs in konkur_products_data:
            p, _ = Product.objects.update_or_create(
                business=biz_konkur, name=name,
                defaults={
                    "category": cat,
                    "price": price,
                    "description": desc,
                    "target_customer": target,
                    "attributes": attrs,
                    "product_type": "SERVICE" if cat.product_type == "SERVICE" else "PHYSICAL",
                    "status": "ACTIVE",
                    "is_discovery_active": True,
                    "discovery_priority": random.randint(3, 5),
                }
            )
            if not p.images.exists():
                img_rel = random.choice(sample_images)
                ProductImage.objects.create(product=p, image=img_rel, is_main=True)
            saved_konkur_prods.append(p)

        self.stdout.write(self.style.SUCCESS(f"Created {len(saved_konkur_prods)} products for Konkur Institute Business."))

        # Clear synthetic leads for clean live discovery
        Opportunity.objects.filter(business=biz_konkur).delete()
        Customer.objects.filter(business=biz_konkur).delete()

        # Seed Monitored Telegram Communities for Konkur Discovery
        konkur_communities = [
            ("گروه گفتگوی کنکوری‌ها (تبادل نظر، منابع و مشاوره)", "https://t.me/konkur_gap", "گروه چت عمومی و تبادل نظر دانش‌آموزان کنکوری برای انتخاب منابع، آزمون و مشاوره"),
            ("چت‌روم داوطلبان کنکور تجربی و پزشکی", "https://t.me/konkur_tajrobi_chat", "بحث و تبادل نظر پیرامون رتبه‌سازی، منابع زیست و شیمی و پکیج‌های آزمون تجربی"),
            ("گروه رفع اشکال و دورهمی کنکور ریاضی و فیزیک", "https://t.me/konkur_math_chat", "رفع اشکال حسابان، فیزیک، هندسه و تبادل نظر درباره اساتید و کلاس‌های آنلاین"),
            ("گروه داوطلبان کنکور انسانی و فرهنگیان", "https://t.me/konkur_ensani_chat", "تبادل جزوات، خلاصه درس‌ها و پرسش و پاسخ درباره قبولی فرهنگیان و حقوق"),
            ("گروه مشاوره تحصیلی و برنامه‌ریزی کنکور", "https://t.me/konkur_moshverah", "پرسش و پاسخ درباره روش‌های مطالعه، ساعت مطالعه، تراز آزمون‌ها و انتخاب مشاور"),
            ("تحلیل و مقایسه آزمون‌های آزمایشی (ماز، قلمچی، سنجش)", "https://t.me/azmoon_konkur_chat", "بررسی سوالات، کارنامه‌ها و تراز آزمون‌های آزمایشی قلمچی و ماز"),
            ("گروه معرفی منابع و کلاس‌های نکته و تست کنکور", "https://t.me/konkur_manabe_chat", "پرسش درباره بهترین دوره‌های آنلاین، همایش‌های جمع‌بندی و پکیج‌های تستی"),
            ("گروه داوطلبان و فارغ‌التحصیلان کنکور ۱۴۰۴", "https://t.me/konkur1404_gap", "دورهمی و پرسش و پاسخ دانش‌آموزان سال دوازدهم و پشت‌کنکوری‌ها"),
            ("گروه انتخاب رشته کنکور سراسری و آزاد", "https://t.me/konkur_reshteh_chat", "بحث و تبادل نظر داوطلبان پیرامون انتخاب رشته دانشگاه‌های سراسری و آزاد"),
            ("گروه دورهمی رتبه‌های برتر و نخبگان کنکور", "https://t.me/konkuriha_chat", "پرسش و پاسخ درباره تکنیک‌های مدیریت زمان، تست‌زنی و جمع‌بندی")
        ]

        for name, link, desc in konkur_communities:
            key = normalize_link(link)
            MonitoredCommunity.objects.update_or_create(
                normalized_link=key,
                defaults={
                    "business": biz_konkur,
                    "scope": PRIVATE,
                    "name": name,
                    "handle_or_link": link,
                    "community_type": "GROUP",
                    "description": desc,
                    "is_active": True,
                }
            )
        self.stdout.write(self.style.SUCCESS(f"Seeded {len(konkur_communities)} Telegram Konkur communities for live discovery."))


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
                "telegram_account_handle": "@Paytakht_Stock_IT",
                "daily_discovery_limit": 50,
            }
        )
        self.stdout.write(self.style.SUCCESS(f"Business 2 ready: {biz_digi.name}"))

        # Category Tree for Digital
        c_root_digital, _ = Category.objects.get_or_create(
            business=biz_digi, name="کالای دیجیتال و IT", parent=None,
            defaults={"depth": 1, "product_type": "PHYSICAL"}
        )
        c_laptops, _ = Category.objects.get_or_create(
            business=biz_digi, name="لپ‌تاپ‌های استوک و اپن‌باکس", parent=c_root_digital,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_engineering_laptops, _ = Category.objects.get_or_create(
            business=biz_digi, name="لپ‌تاپ مهندسی و برنامه‌نویسی", parent=c_laptops,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )
        c_gaming_laptops, _ = Category.objects.get_or_create(
            business=biz_digi, name="لپ‌تاپ گیمینگ و رندرینگ", parent=c_laptops,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )
        c_ultrabooks, _ = Category.objects.get_or_create(
            business=biz_digi, name="اولترابوک سبک اداری و بیزینس", parent=c_laptops,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )

        c_monitors, _ = Category.objects.get_or_create(
            business=biz_digi, name="مانیتور و نمایشگر حرفه‌ای", parent=c_root_digital,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_4k_monitors, _ = Category.objects.get_or_create(
            business=biz_digi, name="مانیتور 4K و طراحی گرافیک", parent=c_monitors,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )
        c_gaming_monitors, _ = Category.objects.get_or_create(
            business=biz_digi, name="مانیتور گیمینگ رفرش‌ریت بالا", parent=c_monitors,
            defaults={"depth": 3, "product_type": "PHYSICAL"}
        )

        c_accessories, _ = Category.objects.get_or_create(
            business=biz_digi, name="لوازم جانبی و تجهیزات شبکه", parent=c_root_digital,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )

        # 25 Products for Digital
        digital_products_data = [
            # Engineering Laptops
            ("لپ‌تاپ دل پرسیشن ۷۵۳۰ (Dell Precision 7530)", c_engineering_laptops, 38500000,
             "ورک‌استیشن غول‌پیکر صنعتی با پردازنده Core i7 نسل ۸، ۶۴ گیگابایت رم، گرافیک ۴ گیگابایت انویدیا Quadro P2000 و بدنه تیتانیومی مقاوم در برابر ضربه و حرارت.",
             "مهندسان عمران، مکانیک، سالیدورکس‌کارها و معماران",
             {"پردازنده": "Core i7-8850H", "رم": "64GB DDR4", "گرافیک": "Quadro P2000 4GB", "صفحه": "15.6 FHD IPS مات"}),
            ("لپ‌تاپ لنوو تینک‌پد P52 (Lenovo ThinkPad P52)", c_engineering_laptops, 36000000,
             "افسانه دوام لنوو با کیبورد ارگونومیک ضدآب، پردازنده ۶ هسته‌ای Xeon، رم ۳۲ گیگابایت ECC و گرافیک Quadro P3200 با خروجی فوق‌العاده برای شبیه‌سازی‌های متلب و انسیس.",
             "برنامه‌نویسان بک‌اند، محققان هوش مصنوعی و مهندسان نرم‌افزار",
             {"پردازنده": "Intel Xeon E-2176M", "رم": "32GB ECC", "حافظه": "512GB NVMe SSD", "وزن": "۲.۴ کیلوگرم"}),
            ("لپ‌تاپ اچ‌پی زدبوک ۱۵ جی۶ (HP ZBook 15 G6)", c_engineering_laptops, 44000000,
             "ورک‌استیشن لوکس نسل ۹ اچ‌پی مجهز به پردازنده Core i7-9850H، گرافیک Quadro T2000، دو اسلات تاندربولت ۳ و صفحه نمایش کالیبره دریم‌کالر با ۱۰۰٪ پوشش sRGB.",
             "تدوین‌گران حرفه‌ای پریمیر، افتر افکت و مهندسان صنایع",
             {"پردازنده": "Core i7-9850H", "رم": "32GB DDR4", "گرافیک": "Quadro T2000 4GB", "صفحه نمایش": "DreamColor 4K"}),
            ("لپ‌تاپ دل لتیتیود ۵۵۹۱ (Dell Latitude 5591)", c_engineering_laptops, 26500000,
             "لپ‌تاپ پرسرعت و بهینه سری تجاری دل با پردازنده Core i7 سری H، رم ۱۶ گیگابایت، گرافیک جیفورس MX130 و وزن مناسب برای حمل مداوم به دانشگاه و محل کار.",
             "دانشجویان فنی مهندسی و مدیران پروژه‌های عمرانی",
             {"پردازنده": "Core i7-8850H", "رم": "16GB", "حافظه": "512GB SSD", "باتری": "۶۸ وات‌ساعت سلامت بالای ۸۵٪"}),
            ("لپ‌تاپ لنوو تینک‌پد T480 (Lenovo T480)", c_engineering_laptops, 19800000,
             "محبوب‌ترین لپ‌تاپ برنامه‌نویسی دنیا به لطف دوام بی‌پایان باتری دوگانه بریج، کیبورد بی‌نظیر ThinkPad، پردازنده Core i5 نسل ۸ و قابلیت ارتقای رم تا ۶۴ گیگابایت.",
             "برنامه‌نویسان وب و موبایل، ادمین‌های لینوکس و دوآپس",
             {"پردازنده": "Core i5-8350U", "رم": "16GB DDR4", "وزن": "۱.۵۸ کیلوگرم", "پورت‌ها": "تاندربولت + تایپ سی"}),

            # Gaming Laptops
            ("لپ‌تاپ ایسوس زفیروس جی۱۴ (Asus ROG Zephyrus G14)", c_gaming_laptops, 62000000,
             "شاهکار گیمینگ جمع‌وجور با پردازنده ۸ هسته‌ای Ryzen 9 5900HS، کارت گرافیک RTX 3060 6GB، صفحه نمایش ۱۲۰ هرتز 2K و وزن خیره‌کننده ۱.۶ کیلوگرم با بدنه منیزیمی سفید.",
             "گیمرها، استریمرها، برنامه‌نویسان گیم و ادیتورهای ویدیویی",
             {"پردازنده": "AMD Ryzen 9 5900HS", "کارت گرافیک": "NVIDIA RTX 3060 6GB", "رم": "16GB 3200MHz", "صفحه": "14 QHD 120Hz"}),
            ("لپ‌تاپ لنوو لژیون ۵ پرو (Lenovo Legion 5 Pro)", c_gaming_laptops, 58000000,
             "قدرتمندترین کولینگ لپ‌تاپ‌های گیمینگ با پردازنده Ryzen 7 5800H، کارت گرافیک ۱۳۰ وات توان کامل RTX 3060، صفحه نمایش ۱۶ اینچ ۱۶۵ هرتز با نسبت ۱۶:۱۰ و کیبورد RGB.",
             "گیمرهای حرفه‌ای مسابقاتی، رندرکنندگان تری‌دی مکس و بلندر",
             {"صفحه نمایش": "16 WQXGA 165Hz G-Sync", "گرافیک": "RTX 3060 130W Full", "خنک‌کننده": "ColdFront 3.0"}),
            ("لپ‌تاپ اچ‌پی اومن ۱۶ (HP Omen 16)", c_gaming_laptops, 52000000,
             "لپ‌تاپ گیمینگ شیک و مینیمال با بدنه آلومینیومی مشکی، پردازنده Core i7-11800H، گرافیک RTX 3060، سیستم صوتی بنگ اند اولوفسن و وبکم مجهز به هوش مصنوعی حذف نویز.",
             "طراحان گرافیک متحرک و علاقه‌مندان به گیم‌های سنگین AAA",
             {"پردازنده": "Core i7-11800H", "رم": "16GB", "صدا": "Bang & Olufsen", "نمایشگر": "16.1 144Hz IPS"}),
            ("لپ‌تاپ دل جی۱۵ گیمینگ (Dell G15 5515)", c_gaming_laptops, 46000000,
             "لپ‌تاپ گیمینگ اقتصادی با شاسی خنک‌کننده مشتق از آلین‌ویر (Alienware)، پردازنده Ryzen 7 5800H و کارت گرافیک RTX 3050Ti با قابلیت فعال‌سازی کلید توربو فن G-Mode.",
             "دانشجویان معماری و گیمرهای نیمه‌حرفه‌ای",
             {"پردازنده": "Ryzen 7 5800H", "کارت گرافیک": "RTX 3050Ti 4GB", "قابلیت خاص": "Alienware Command Center"}),

            # Ultrabooks
            ("لپ‌تاپ مایکروسافت سرفیس لپ‌تاپ ۳ (Surface Laptop 3)", c_ultrabooks, 28500000,
             "اولترابوک تمام آلومینیومی فوق‌باریک با صفحه لمسی سنس ۲K با نسبت ۳:۲ ایده‌آل مطالعه و اسناد، پردازنده Core i5 نسل ۱۰، وزن ۱.۲ کیلوگرم و شارژدهی تا ۱۰ ساعت.",
             "مدیران، مدرسین، نویسندگان و دانشجویان رشته‌های علوم انسانی",
             {"صفحه نمایش": "13.5 PixelSense Touch 2K", "وزن": "۱.۲۶ کیلوگرم", "جنس بدنه": "آلومینیوم آنودایز پلاتینیوم"}),
            ("لپ‌تاپ دل ایکس‌پی‌اس ۱۳ ۹۳۸۰ (Dell XPS 13 9380)", c_ultrabooks, 34000000,
             "زیباترین اولترابوک جهان با حاشیه‌های نمایشگر نانولبه InfinityEdge، فیبر کربن دست‌بافت مشکی، وزن ۱.۲ کیلوگرم، وبکم بهبودیافته و عملکرد پایدار Core i7 نسل ۸.",
             "برنامه‌نویسان دورکار، فریلنسرها و مدیران اجرایی",
             {"صفحه نمایش": "13.3 4K Ultra HD Touch", "رم": "16GB LPDDR3", "بدنه": "آلومینیوم تراش‌خورده CNC و فیبرکربن"}),
            ("لپ‌تاپ لنوو تینک‌پد X1 کربن نسل ۶ (ThinkPad X1 Carbon)", c_ultrabooks, 27000000,
             "سبک‌ترین ورک‌استیشن همراه ساخته‌شده از الیاف کربن پیشرفته ماهواره‌ای با وزن باورنکردنی ۱.۱۳ کیلوگرم، استاندارد نظامی ضدضربه Mil-Spec و کیبورد افسانه‌ای.",
             "کارآفرینان در سفرهای تجاری مداوم و مدیران استارتاپ‌ها",
             {"وزن": "۱.۱۳ کیلوگرم فوق‌سبک", "مقاومت": "۱۲ آزمون نظامی MIL-STD", "پردازنده": "Core i7-8650U"}),
            ("لپ‌تاپ اچ‌پی الیت‌بوک ۸۴۰ جی۶ (HP EliteBook 840 G6)", c_ultrabooks, 22000000,
             "اولترابوک آلومینیومی شرکتی با امنیت فوق پیشرفته بایوس Sure Start، حسگر اثرانگشت، فیلتر ضدجاسوسی Sure View صفحه نمایش و صدای پرقدرت با میکروفون محیطی.",
             "حسابداران، مدیران مالی و سازمان‌های اداری",
             {"پردازنده": "Core i5-8365U", "رم": "16GB", "امنیت": "SureStart Gen5 + حسگر اثرانگشت"}),
            ("لپ‌تاپ سرفیس پرو ۷ با کیبورد و قلم (Surface Pro 7)", c_ultrabooks, 29000000,
             "تبلت-لپ‌تاپ ۲ در ۱ فوق سبک با پایه چرخان استند، قلم هوشمند سرفیس پن با ۴۰۹۶ سطح فشار، پردازنده Core i5 نسل ۱۰ و وزن ۷۷۰ گرم بدون کیبورد.",
             "طراحان دیجیتال، اساتید دانشگاه و نوت‌برداران جلسات",
             {"وزن خالص تبلت": "۷۷۵ گرم", "درگاه": "تایپ سی و USB-A", "لوازم": "کیبورد تایپ‌کاور آلکانتارا"}),

            # Monitors
            ("مانیتور دل ۲۷ اینچ 4K مدل اولتراشارپ (Dell U2720Q)", c_4k_monitors, 27500000,
             "استاندارد طلایی مانیتورهای طراحی دنیا با پنل IPS 4K HDR400، پوشش ۹۵٪ فضای رنگی DCI-P3، هاب پورت‌های کامل تایپ‌سی ۹۰ وات و پایه با قابلیت چرخش عمودی پیوت.",
             "طراحان UI/UX، تدوین‌گران ویدیو، کالریست‌ها و برنامه‌نویسان مک",
             {"رزولوشن": "3840x2160 4K UHD", "پنل": "IPS کالیبره کارخانه delta-E < 2", "اتصال": "USB-C 90W PD"}),
            ("مانیتور ال‌جی ۲۷ اینچ 4K مدل 27UK850", c_4k_monitors, 23000000,
             "مانیتور 4K با حاشیه‌های فوق باریک، پشتیبانی از HDR10، سازگاری کامل با کنسول‌های بازی PS5/Xbox و ورودی تصویر و شارژ همزمان با کابل تایپ‌سی لپ‌تاپ.",
             "طراحان گرافیک و کاربران خانگی کنسول و مک‌بوک",
             {"رزولوشن": "4K IPS", "اسپیکر داخلی": "استریو با فناوری MaxxAudio", "پورت": "Type-C + HDMI x2"}),
            ("مانیتور دل ۲۴ اینچ فول‌اچ‌دی مدل P2419H", c_4k_monitors, 9800000,
             "مانیتور کاری بسیار محبوب و باکیفیت با پایه آسانسوری با چرخش ۹۰ درجه، پنل ضدبازتاب مات IPS، فیلتر نور آبی ComfortView و مصرف بهینه برق.",
             "محیط‌های اداری، دفاتر شرکت‌ها و برنامه‌نویسان",
             {"سایز": "24 اینچ", "پنل": "IPS مات", "پایه‌ها": "تنظیم کامل ارتفاع، چرخش و زاویه"}),
            ("مانیتور ایسوس ۲۴ اینچ گیمینگ ۱۴۴ هرتز (VG249Q)", c_gaming_monitors, 12500000,
             "مانیتور فوق‌العاده سریع با زمان پاسخ‌گویی ۱ میلی‌ثانیه، پنل IPS با زوایای دید باز ۱۷۸ درجه، فناوری FreeSync و کاهش تاری تصویر ELMB.",
             "گیمرهای سبک شوتر اول‌شخص و شوترهای رقابتی",
             {"رفرش‌ریت": "144Hz", "زمان پاسخگویی": "1ms MPRT", "پنل": "IPS Gaming"}),
            ("مانیتور گیمینگ خمیده ۳۴ اینچ شیائومی (Mi Curved 34)", c_gaming_monitors, 28000000,
             "نمایشگر فوق‌عریض التراواید ۲۱:۹ با انحنای ارگونومیک 1500R، رزولوشن WQHD، رفرش‌ریت ۱۴۴ هرتز و پوشش ۱۲۱٪ طیف رنگی sRGB.",
             "برنامه‌نویسان عاشق نمایشگر یکپارچه، تحلیل‌گران مالی و گیمرها",
             {"سایز و انحنا": "34 اینچ 1500R واید", "رزولوشن": "3440x1440 WQHD", "نرخ نوسازی": "144Hz"}),
            ("مانیتور بنکیو ۲۷ اینچ محافظ چشم (BenQ GW2780)", c_gaming_monitors, 11200000,
             "مانیتور مجهز به سنسور هوشمند تنظیم روشنایی بر اساس نور محیط (B.I. Tech)، محافظت چشم بدون فلیکر و حاشیه‌های مینیمال شیک.",
             "کاربران اداری، حسابداران و کسانی که ساعت‌های متوالی پای سیستم هستند",
             {"سایز": "27 اینچ IPS", "فناوری محافظت چشم": "Brightness Intelligence Tech", "پورت": "HDMI, DisplayPort, VGA"}),

            # Accessories
            ("داک استیشن تاندربولت ۳ دل مدل WD19TB", c_accessories, 9500000,
             "داک استیشن فوق حرفه‌ای تاندربولت با توان خروجی ۱۳۰ وات، پشتیبانی همزمان از سه مانیتور 4K، خروجی‌های DisplayPort، پورت‌های متعدد USB و شبکه گیگابیت.",
             "کاربران لپ‌تاپ‌های مهندسی و مدیرانی که در دفتر نیاز به هاب تک‌کابله دارند",
             {"پهنای باند": "40Gbps Thunderbolt 3", "توان شارژ": "130W Dell ExpressCharge"}),
            ("کیبورد مکانیکی لاجیتک مدل MX Mechanical Mini", c_accessories, 8800000,
             "کیبورد مکانیکال باریک و بی‌صدا با کلیدهای سوییچ Tactile Quiet، نورپردازی هوشمند مجاورتی و اتصال همزمان به سه دستگاه با بلوتوث.",
             "برنامه‌نویسان، نویسندگان و شیفتگان تایپ سریع",
             {"سوییچ": "مکانیکی بی‌صدا لاجیتک", "اتصال": "بلوتوث + دانگل Bolt تا ۳ دستگاه"}),
            ("ماوس ارگونومیک لاجیتک مدل MX Master 3S", c_accessories, 6900000,
             "بهترین ماوس اداری و مهندسی جهان با اسکرول الکترومغناطیسی مگ‌اسپید ۱۰۰۰ خط بر ثانیه، کلیک‌های سایلنت ۹۰٪ بی‌صدا و سنسور 8000 DPI قابل استفاده روی شیشه.",
             "طراحان گرافیک، مدل‌سازان سه‌بعدی و مهندسان",
             {"دقت سنسور": "8000 DPI Darkfield", "اسکرول": "MagSpeed الکترومغناطیس"}),
            ("پایه نگهدارنده ارگونومیک آلومینیومی لپ‌تاپ نیلکین", c_accessories, 1450000,
             "استند تمام فلزی با قابلیت تنظیم زاویه و ارتفاع تا ۲۵ سانتیمتر، جریان هوای عالی زیر لپ‌تاپ و تحمل وزن تا ۱۲ کیلوگرم.",
             "تمام کاربرانی که از درد گردن و شانه پای لپ‌تاپ رنج می‌برند",
             {"جنس": "آلومینیوم نقره‌ای ضخیم", "قابلیت": "تنظیم چندمحوره ارگونومیک"}),
            ("کوله پشتی ضدسرقت و ضدآب لپ‌تاپ بنج مدل Bange Pro", c_accessories, 2900000,
             "کوله پشتی اداری و مسافرتی با جایگاه اختصاصی لپ‌تاپ تا ۱۷.۳ اینچ، کپسول‌های ضربه‌گیر بادی، پورت خروجی شارژ USB و قفل زیپ ضدسرقت TSA.",
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
            "1. Konkur Institute: konkur@peyda.ir / Konkur_2026_Demo! (27 services/products + 10 Telegram groups)\n"
            "2. Digital Laptops:   digital@peyda.ir / Digital_2026_Demo! (26 products)\n"
            "Zero synthetic leads generated — ready for real crawler/need_engine discovery.\n"
            + "="*70
        ))


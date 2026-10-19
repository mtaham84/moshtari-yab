import os
import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal
from apps.businesses.models import Business, MessageStyle
from apps.products.models import Category, Product, ProductImage
from apps.billing.models import Wallet, WalletTransaction
from apps.billing.services import ensure_wallet, apply_transaction
from apps.discovery.models import Customer, Opportunity, AIAnalysis, OpportunityProductMatch, MonitoredCommunity
from apps.discovery.sources import PRIVATE, normalize_link

User = get_user_model()


class Command(BaseCommand):
    help = "Seed 3 realistic Iranian demo businesses (Konkur Institute, Paytakht Stock Laptops & Negin Building Stone) with rich products, taxonomies, and wallets."

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

        # Category Tree for Konkur Institute (Strictly Educational Services)
        # Root 1: خدمات آزمون‌های آزمایشی
        c_root_exams, _ = Category.objects.get_or_create(
            business=biz_konkur, name="خدمات آزمون‌های آزمایشی و سنجش جامع", parent=None,
            defaults={"depth": 1, "product_type": "SERVICE"}
        )
        c_mock_exams, _ = Category.objects.get_or_create(
            business=biz_konkur, name="آزمون‌های شبیه‌ساز کنکور سراسری (ماز و قلم‌چی)", parent=c_root_exams,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_step_exams, _ = Category.objects.get_or_create(
            business=biz_konkur, name="آزمون‌های مرحله‌ای و پیشرفت تحصیلی", parent=c_root_exams,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_exam_analysis, _ = Category.objects.get_or_create(
            business=biz_konkur, name="تحلیل آزمون، کارنامه ۵ بعدی و تخمین رتبه", parent=c_root_exams,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )

        # Root 2: خدمات مشاوره و برنامه‌ریزی
        c_root_mentoring, _ = Category.objects.get_or_create(
            business=biz_konkur, name="خدمات مشاوره، برنامه‌ریزی و مربیگری تحصیلی", parent=None,
            defaults={"depth": 1, "product_type": "SERVICE"}
        )
        c_vip_mentoring, _ = Category.objects.get_or_create(
            business=biz_konkur, name="مشاوره رتبه‌سازی VIP و مربیگری تخصصی فردی", parent=c_root_mentoring,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_planning, _ = Category.objects.get_or_create(
            business=biz_konkur, name="برنامه‌ریزی مطالعاتی هفتگی و پیگیری روزانه", parent=c_root_mentoring,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_workshop, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کارگاه‌های مدیریت زمان، استرس و تکنیک آزمون", parent=c_root_mentoring,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_selection, _ = Category.objects.get_or_create(
            business=biz_konkur, name="مشاوره و انتخاب رشته هوشمند دانشگاه سراسری و آزاد", parent=c_root_mentoring,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )

        # Root 3: کلاس‌های کنکور و نکته و تست
        c_root_classes, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کلاس‌های آنلاین کنکور، نکته و تست و جمع‌بندی", parent=None,
            defaults={"depth": 1, "product_type": "SERVICE"}
        )
        c_tajrobi_classes, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کلاس‌های جامع کنکور تجربی (زیست، شیمی، فیزیک، ریاضی)", parent=c_root_classes,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_math_classes, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کلاس‌های جامع کنکور ریاضی (حسابان، هندسه و گسسته)", parent=c_root_classes,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_ensani_classes, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کلاس‌های جامع کنکور انسانی (فنون ادبی، فلسفه و منطق، عربی)", parent=c_root_classes,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_golden_seminar, _ = Category.objects.get_or_create(
            business=biz_konkur, name="دوره‌های طلایی نکته و تست و همایش‌های جمع‌بندی", parent=c_root_classes,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )

        # Root 4: کلاس‌های تقویتی پایه‌ها و آمادگی امتحانات نهایی
        c_root_reinforce, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کلاس‌های تقویتی پایه‌ها و آمادگی امتحانات نهایی", parent=None,
            defaults={"depth": 1, "product_type": "SERVICE"}
        )
        c_twelfth_reinforce, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کلاس‌های تقویتی و تشریحی دوازدهم (ویژه امتحانات نهایی)", parent=c_root_reinforce,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_eleventh_reinforce, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کلاس‌های تقویتی و تشریحی پایه یازدهم", parent=c_root_reinforce,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_tenth_reinforce, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کلاس‌های تقویتی و پایه‌سازی پایه دهم", parent=c_root_reinforce,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )
        c_final_workshops, _ = Category.objects.get_or_create(
            business=biz_konkur, name="کارگاه‌های شب امتحان نهایی و حل نمونه‌سوالات کشوری", parent=c_root_reinforce,
            defaults={"depth": 2, "product_type": "SERVICE"}
        )

        # Remove legacy physical categories if existing
        Category.objects.filter(business=biz_konkur, product_type="PHYSICAL").delete()

        # 28 Pure Services for Konkur Educational Institute
        konkur_services_data = [
            # 1. خدمات آزمون‌های آزمایشی و تحلیلی
            ("پکیج آزمون‌های شبیه‌ساز کنکور سراسری ماز/قلم‌چی (۲۰ مرحله جامع با کارنامه ۵ بعدی)", c_mock_exams, 5900000,
             "۲۰ مرحله آزمون آزمایشی آنلاین مطابق با آخرین تغییرات سازمان سنجش، سوالات تالیفی اساتید برتر، تطابق حداکثری با کنکورهای اخیر، صدور رتبه کشوری و پاسخنامه تشریحی-ویدئویی.",
             "دانش‌آموزان سال دوازدهم و فارغ‌التحصیلان کنکوری تمامی رشته‌ها",
             {"تعداد مراحل": "۲۰ مرحله", "نوع": "آنلاین با امنیت بالا", "پاسخنامه": "تشریحی کامل و ویدئویی"}),

            ("پکیج آزمون‌های طلایی شبیه‌ساز کنکور اردیبهشت و تیر (۸ مرحله فشرده)", c_mock_exams, 2900000,
             "۸ دوره آزمون شبیه‌ساز کامل دفترچه‌های کنکور دقیقا مشابه با سطح دشواری و بودجه‌بندی کنکور سراسری اخیر با تحلیل و رتبه‌بندی لحظه‌ای.",
             "متقاضیان شبیه‌سازی شرایط واقعی کنکور در ماه‌های پایانی منتهی به آزمون",
             {"تعداد": "۸ مرحله شبیه‌ساز", "سطح": "منطبق با آخرین کنکور", "پاسخنامه": "ویدئویی"}),

            ("آزمون‌های مرحله‌ای و پیشرفت تحصیلی مباحث پایه (دهم و یازدهم)", c_step_exams, 1800000,
             "مجموعه آزمون‌های سرفصل به سرفصل برای تثبیت دروس پایه، رفع چالش تست‌زنی و شناسایی نقاط ضعف قبل از شروع جمع‌بندی نهایی.",
             "دانش‌آموزان پایه‌های دهم و یازدهم و داوطلبان دارای ضعف در مباحث پایه",
             {"پوشش": "دهم و یازدهم کامل", "سبک سوالات": "تالیفی استاندارد و کنکوری"}),

            ("پکیج تحلیل ویدئویی و تشریحی سوالات آزمون‌های آزمایشی با اساتید برند", c_exam_analysis, 1400000,
             "تدریس ویدئویی خط به خط پاسخ تشریحی تمامی سوالات آزمون‌ها با بررسی دام‌های آموزشی و تکنیک‌های رد گزینه توسط اساتید رتبه برتر.",
             "دانش‌آموزان خواهان یادگیری نکات تستی از دل سوالات آزمون",
             {"فرمت": "ویدیو با کیفیت بالا", "نکات": "بررسی روش‌های حل سریع"}),

            ("کارنامه تحلیلی ۵ بعدی آزمون، تخمین رتبه کشوری و نمودار پیشرفت", c_exam_analysis, 850000,
             "سیستم هوشمند تحلیل کارنامه با نمودار پیشرفت درس به درس، مقایسه تراز با میانگین پذیرفته‌شدگان سال‌های قبل و تخمین رتبه کشوری.",
             "دانش‌آموزان نیازمند شناخت نقاط قوت و ضعف آماری در هر مبحث",
             {"گزارش": "PDF تفصیلی + مشاوره", "تخمین رتبه": "بر اساس سهمیه مناطق"}),

            # 2. خدمات مشاوره و برنامه‌ریزی تحصیلی
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

            ("طرح مشاوره و هدایت تحصیلی ماهانه (شروع سریع و تعیین سطح)", c_planning, 2800000,
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

            ("پکیج انتخاب رشته هوشمند کنکور سراسری با نرم‌افزار تحلیلی رتبه برتر", c_selection, 1200000,
             "نرم‌افزار انتخاب رشته بر اساس آمار قبولی ۱۰ سال اخیر کانون و سازمان سنجش، اعمال سهمیه‌های مناطق و ایثارگری، تحلیل بومی‌گزینی و اولویت‌بندی ۱۵۰ کد رشته.",
             "داوطلبان مجاز به انتخاب رشته در کنکور سراسری",
             {"پوشش": "تمام دوره‌های روزانه، شبانه، پردیس و فرهنگیان", "چیدمان": "۱۵۰ کد رشته بر اساس علاقه و شانس"}),

            ("جلسه مشاوره فردی و اختصاصی انتخاب رشته حضوری/آنلاین با مشاور ارشد", c_selection, 3500000,
             "جلسه ۲ ساعته فردی با مشاور ارشد برای اولویت‌بندی دقیق رشته‌ها، تحلیل بازار کار، شرایط مهاجرت، دانشگاه‌های خاص، پزشکی و فرهنگیان.",
             "داوطلبان خواهان اطمینان صددرصدی از چیدمان بهینه و آینده‌نگرانه رشته‌ها",
             {"مشاور": "مشاور ارشد با ۱۰ سال سابقه", "مدت": "۲ ساعت اختصاصی"}),

            # 3. کلاس‌های جامع کنکور و نکته و تست
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

            # 4. کلاس‌های تقویتی مختلف و آمادگی امتحانات نهایی
            ("کلاس تقویتی و تشریحی ریاضی و حسابان دوازدهم (ویژه نمره ۲۰ امتحان نهایی و ترمیم معدل)", c_twelfth_reinforce, 3800000,
             "تدریس خط‌به‌خط کتاب درسی، حل تمرینات و مسائل پایان فصل، واکاوی سبک بارم‌بندی مصححین کشوری و حل نمونه سوالات نهایی خرداد، شهریور و دی‌ماه ۱۰ سال اخیر.",
             "دانش‌آموزان دوازدهم تجربی و ریاضی متقاضی ترمیم معدل و کسب نمره کامل در امتحان نهایی",
             {"تعداد جلسات": "۱۶ جلسه آنلاین", "محتوا": "تشریحی‌نویسی و استانداردهای نهایی"}),

            ("کلاس تقویتی و تشریحی زیست‌شناسی دوازدهم (مفاهیم نهایی و قیدهای کتاب)", c_twelfth_reinforce, 3600000,
             "بررسی کلمه به کلمه کتاب درسی دوازدهم، شکل‌ها، فعالیت‌ها و جدول‌ها به شیوه تشریحی و کلیدواژه‌محور جهت کسب بالاترین نمره کتبی در امتحان نهایی سراسری.",
             "دانش‌آموزان سال دوازدهم تجربی برای تاثیر قطعی معدل در کنکور",
             {"پوشش": "کل فصول زیست دوازدهم", "آزمونک": "تصحیح استاندارد تشریحی"}),

            ("کلاس تقویتی و تشریحی فیزیک و شیمی پایه دوازدهم (حل تیپ‌های نهایی)", c_twelfth_reinforce, 3900000,
             "حل کامل مسائل تشریحی فیزیک و شیمی دوازدهم، رسم شکل‌ها، نحوه نوشتن فرمول‌ها و محاسبات کسری مطابق با شیوه تصحیح الکترونیکی اوراق نهایی.",
             "دانش‌آموزان رشته‌های تجربی و ریاضی دوازدهم",
             {"جزوه": "بانک سوالات نهایی طبقه‌بندی شده", "آزمون": "شبیه‌ساز تشریحی"}),

            ("کلاس تقویتی و تشریحی دروس عمومی دوازدهم (فارسی، زبان، دینی و عربی نهایی)", c_twelfth_reinforce, 3200000,
             "دوره فشرده تشریحی دروس عمومی موثر در سوابق تحصیلی، آموزش دقیق قواعد عربی، آرایه‌های ادبی فارسی نهایی، لیسنینگ و گرامر زبان انگلیسی و پیام آیات دینی.",
             "تمامی دانش‌آموزان رشته‌های نظری پایه دوازدهم",
             {"پوشش": "۴ درس عمومی ترازساز نهایی", "مدت": "۲۰ جلسه فشرده"}),

            ("کلاس‌های تقویتی و تشریحی دروس اختصاصی پایه یازدهم (ریاضی، زیست، شیمی و فیزیک)", c_eleventh_reinforce, 3400000,
             "آموزش عمیق و مفهومی مباحث سنگین پایه یازدهم جهت ارتقای معدل کتبی، حل تمرینات چالشی و آمادگی زودهنگام برای امتحانات نهایی خرداد ماه.",
             "دانش‌آموزان پایه یازدهم مدارس دولتی و خاص",
             {"پوشش": "دروس اختصاصی یازدهم", "رویکرد": "مفهومی و تشریحی"}),

            ("کلاس‌های تقویتی و پایه‌سازی دروس ریاضی و علوم پایه دهم", c_tenth_reinforce, 2900000,
             "تقویت پایه‌های علمی دانش‌آموزان دهمی، جبران خلاءهای آموزشی دوره اول متوسطه و ایجاد زیربنای قوی برای دروس اختصاصی دبیرستان.",
             "دانش‌آموزان سال دهم در شروع مسیر متوسطه دوم",
             {"پوشش": "ریاضی ۱، فیزیک ۱، شیمی ۱", "پشتیبانی": "رفع اشکال آنلاین"}),

            ("کارگاه آنلاین «شب امتحان نهایی» (تحلیل بارم‌بندی و حل نمونه‌سوالات احتمالی کشوری)", c_final_workshops, 750000,
             "کارگاه‌های فشرده شب امتحان برای هر درس در ایام امتحانات نهایی خرداد، مرور کلیدی‌ترین نقاط نمره‌ساز، نکات فرار و دام‌های رایج مصححین در برگه امتحانی.",
             "تمام داوطلبان امتحانات نهایی کشوری خردادماه",
             {"زمان": "عصر قبل از هر امتحان نهایی", "فایل": "خلاصه نکات شب امتحان"})
        ]

        saved_konkur_prods = []
        for name, cat, price, desc, target, attrs in konkur_services_data:
            p, _ = Product.objects.update_or_create(
                business=biz_konkur, name=name,
                defaults={
                    "category": cat,
                    "price": price,
                    "description": desc,
                    "target_customer": target,
                    "attributes": attrs,
                    "product_type": "SERVICE",
                    "status": "ACTIVE",
                    "is_discovery_active": True,
                    "discovery_priority": random.randint(3, 5),
                }
            )
            if not p.images.exists():
                img_rel = random.choice(sample_images)
                ProductImage.objects.create(product=p, image=img_rel, is_main=True)
            saved_konkur_prods.append(p)

        self.stdout.write(self.style.SUCCESS(f"Created {len(saved_konkur_prods)} purely educational services for Konkur Institute Business."))

        # Clear synthetic leads, customers, and fake communities
        # Real leads must strictly originate from live Telegram crawler and Twitter/X streams
        Opportunity.objects.filter(business=biz_konkur).delete()
        Customer.objects.filter(business=biz_konkur).delete()
        MonitoredCommunity.objects.filter(business=biz_konkur).delete()
        self.stdout.write(self.style.SUCCESS(
            "Konkur educational institute catalog ready (ONLY services: exams, mentoring, konkur classes, reinforcement classes). "
            "Zero synthetic leads, zero fake messages, zero dummy communities — opportunities come strictly from real Telegram crawler & Twitter/X streams."
        ))


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

        # =============================================================
        # BUSINESS 3: Building Stone & Facade (صنایع سنگ و سنگ‌بری نگین نقش‌جهان)
        # =============================================================
        user_stone, _ = User.objects.get_or_create(
            username="sang@peyda.ir",
            defaults={
                "email": "sang@peyda.ir",
                "first_name": "مهدی",
                "last_name": "سنگ‌شناس (صنایع سنگ نقش‌جهان)",
                "is_active": True,
            }
        )
        user_stone.set_password("Sang_2026_Demo!")
        user_stone.save()

        biz_stone, _ = Business.objects.update_or_create(
            user=user_stone,
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
        self.stdout.write(self.style.SUCCESS(f"Business 3 ready: {biz_stone.name}"))

        MessageStyle.objects.update_or_create(
            business=biz_stone,
            defaults={
                "tone": "FORMAL",
                "max_sentences": 3,
                "use_emoji": True,
                "signature": "ارادتمند - بازرگانی و کارخانه سنگ نقش‌جهان",
                "include_link": True,
                "extra_instructions": "مشاوره تخصصی متراژ، معرفی سنگ مناسب اقلیم پروژه و ارسال تصاویر و فیلم بدون افکت سنگ‌ها",
            }
        )

        # Categories
        c_facade, _ = Category.objects.get_or_create(
            business=biz_stone, name="سنگ نما و انواع تراورتن", parent=None,
            defaults={"depth": 1, "product_type": "PHYSICAL"}
        )
        c_interior, _ = Category.objects.get_or_create(
            business=biz_stone, name="سنگ کف، پله و مشاعات ساختمانی", parent=None,
            defaults={"depth": 1, "product_type": "PHYSICAL"}
        )
        c_slab, _ = Category.objects.get_or_create(
            business=biz_stone, name="سنگ‌های لوکس اسلب و بوک‌مچ", parent=None,
            defaults={"depth": 1, "product_type": "PHYSICAL"}
        )
        c_granite_deco, _ = Category.objects.get_or_create(
            business=biz_stone, name="سنگ گرانیت، سنگ لاشه و دکوراتیو", parent=None,
            defaults={"depth": 1, "product_type": "PHYSICAL"}
        )

        c_trav_super, _ = Category.objects.get_or_create(
            business=biz_stone, name="تراورتن سوپر و ممتاز نما (عباس‌آباد و حاجی‌آباد)", parent=c_facade,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_trav_project, _ = Category.objects.get_or_create(
            business=biz_stone, name="تراورتن اقتصادی پروژه‌ای (دره‌بخاری، آبگرم و رامشه)", parent=c_facade,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_marmarit, _ = Category.objects.get_or_create(
            business=biz_stone, name="سنگ مرمریت کف و لابی (دهبید، لاشتر، پرشین)", parent=c_interior,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_stairs, _ = Category.objects.get_or_create(
            business=biz_stone, name="سنگ پله، زیرپله و کف پارکینگ", parent=c_interior,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_slab_crystal, _ = Category.objects.get_or_create(
            business=biz_stone, name="اسلب چینی و کریستال بوک‌مچ (ازنا و الیگودرز)", parent=c_slab,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_granite, _ = Category.objects.get_or_create(
            business=biz_stone, name="سنگ گرانیت ضدسایش (مروارید و نهبندان)", parent=c_granite_deco,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )
        c_deco, _ = Category.objects.get_or_create(
            business=biz_stone, name="سنگ آنتیک، گیوتین و مالون باغی", parent=c_granite_deco,
            defaults={"depth": 2, "product_type": "PHYSICAL"}
        )

        stone_products_data = [
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

        saved_stone_prods = []
        for name, cat, price, desc, target, attrs in stone_products_data:
            p, _ = Product.objects.update_or_create(
                business=biz_stone, name=name,
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
            if not p.images.exists():
                img_rel = random.choice(sample_images)
                ProductImage.objects.create(product=p, image=img_rel, is_main=True)
            saved_stone_prods.append(p)

        self.stdout.write(self.style.SUCCESS(f"Created {len(saved_stone_prods)} products for Stone Business."))

        # Wallet funding for stone business
        w_stone = ensure_wallet(biz_stone)
        if w_stone.balance_toman < Decimal("500000"):
            apply_transaction(
                biz_stone,
                Decimal("3000000"),
                kind=WalletTransaction.GIFT,
                description="اعتبار هدیه راه‌اندازی و پایش تخصصی سنگ ساختمانی و سنگ نما",
                calls=0,
            )
            w_stone.refresh_from_db()

        Opportunity.objects.filter(business=biz_stone).delete()
        Customer.objects.filter(business=biz_stone).delete()
        self.stdout.write(self.style.SUCCESS("Stone seller catalog ready (0 fake leads, waiting for live crawler opportunities)."))

        self.stdout.write(self.style.SUCCESS(
            "\n" + "="*70 + "\n"
            "SUCCESS: All 3 demo seller accounts seeded with rich real products/services!\n"
            "1. Konkur Institute: konkur@peyda.ir / Konkur_2026_Demo! (31 pure educational services: exams, mentoring, konkur classes, reinforcement classes)\n"
            "2. Digital Laptops:   digital@peyda.ir / Digital_2026_Demo! (26 products)\n"
            "3. Building Stone:    sang@peyda.ir / Sang_2026_Demo! (20 products: travertine, marble, slabs, granite, stairs)\n"
            "Zero synthetic leads, zero fake messages, zero dummy communities — ready for real crawler/need_engine discovery.\n"
            + "="*70
        ))


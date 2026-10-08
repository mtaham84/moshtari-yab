from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from apps.businesses.models import Business
from apps.products.models import Category, Product

User = get_user_model()

class Command(BaseCommand):
    help = "Seeds hierarchical category taxonomy, test seller user, and rich sample products for MVP demo."

    def handle(self, *args, **options):
        self.stdout.write("1. Seeding Hierarchical Categories...")

        # Physical: Clothing
        clothing, _ = Category.objects.get_or_create(
            slug="clothing-fashion",
            defaults={"name": "پوشاک و مد", "product_type": "PHYSICAL", "description": "پوشاک، کیف و کفش"}
        )
        mens_clothing, _ = Category.objects.get_or_create(
            slug="mens-clothing",
            defaults={"name": "پوشاک مردانه", "product_type": "PHYSICAL", "parent": clothing}
        )
        mens_pants, _ = Category.objects.get_or_create(
            slug="mens-pants",
            defaults={"name": "شلوار مردانه", "product_type": "PHYSICAL", "parent": mens_clothing}
        )
        mens_jeans, _ = Category.objects.get_or_create(
            slug="mens-jeans",
            defaults={
                "name": "شلوار جین لی مردانه",
                "product_type": "PHYSICAL",
                "parent": mens_pants,
                "suggested_attributes": [
                    {"key": "material", "label": "جنس پارچه", "placeholder": "مثلاً دنیم پنبه‌ای کشی"},
                    {"key": "cut", "label": "مدل برش", "placeholder": "اسلیم‌فیت، راسته، بگ"},
                    {"key": "size", "label": "سایز", "placeholder": "۳۱ تا ۳۸"},
                    {"key": "color", "label": "رنگ", "placeholder": "سرمه‌ای تیره، ذغالی، آبی"},
                ]
            }
        )

        # Physical: Digital & Laptops
        digital, _ = Category.objects.get_or_create(
            slug="digital-goods",
            defaults={"name": "کالای دیجیتال", "product_type": "PHYSICAL", "description": "موبایل، لپ‌تاپ و تجهیزات الکترونیک"}
        )
        laptops, _ = Category.objects.get_or_create(
            slug="laptops-computers",
            defaults={"name": "رایانه و لپ‌تاپ", "product_type": "PHYSICAL", "parent": digital}
        )
        prog_laptops, _ = Category.objects.get_or_create(
            slug="programming-laptops",
            defaults={
                "name": "لپ‌تاپ مهندسی و برنامه‌نویسی",
                "product_type": "PHYSICAL",
                "parent": laptops,
                "suggested_attributes": [
                    {"key": "cpu", "label": "پردازنده CPU", "placeholder": "Intel Core i7 یا Ryzen 7"},
                    {"key": "ram", "label": "حافظه رم RAM", "placeholder": "16GB DDR5"},
                    {"key": "gpu", "label": "کارت گرافیک", "placeholder": "RTX 4050 6GB"},
                    {"key": "screen", "label": "صفحه نمایش", "placeholder": "15.6 OLED 120Hz"},
                ]
            }
        )

        # Service: Education
        education, _ = Category.objects.get_or_create(
            slug="education-training",
            defaults={"name": "آموزش و آکادمی", "product_type": "SERVICE", "description": "دوره‌ها و مهارت‌های آموزشی"}
        )
        programming, _ = Category.objects.get_or_create(
            slug="programming-dev",
            defaults={"name": "برنامه‌نویسی و نرم‌افزار", "product_type": "SERVICE", "parent": education}
        )
        python_cat, _ = Category.objects.get_or_create(
            slug="python-lang",
            defaults={"name": "زبان برنامه‌نویسی پایتون (Python)", "product_type": "SERVICE", "parent": programming}
        )
        python_course, _ = Category.objects.get_or_create(
            slug="python-project-course",
            defaults={
                "name": "دوره پایتون پروژه‌محور و ورود به بازار کار",
                "product_type": "SERVICE",
                "parent": python_cat,
                "suggested_attributes": [
                    {"key": "duration", "label": "مدت دوره (ساعت)", "placeholder": "۶۰ ساعت ویدیو + تمرین"},
                    {"key": "level", "label": "سطح دوره", "placeholder": "مقدماتی تا پیشرفته"},
                    {"key": "has_mentoring", "label": "منتورینگ و پشتیبانی", "placeholder": "دارد (منتور اختصاصی)"},
                    {"key": "format", "label": "نوع برگزاری", "placeholder": "آنلاین (پروژه‌محور)"},
                ]
            }
        )

        # Service: Business Services (SEO)
        biz_services, _ = Category.objects.get_or_create(
            slug="business-services",
            defaults={"name": "خدمات کسب‌وکار", "product_type": "SERVICE", "description": "مارکتینگ، سئو و طراحی"}
        )
        seo_cat, _ = Category.objects.get_or_create(
            slug="seo-digital-marketing",
            defaults={
                "name": "سئو و بهینه‌سازی موتورهای جستجو",
                "product_type": "SERVICE",
                "parent": biz_services,
                "suggested_attributes": [
                    {"key": "contract_term", "label": "مدت زمان قرارداد", "placeholder": "۳ ماهه"},
                    {"key": "reporting", "label": "گزارش‌دهی", "placeholder": "هفتگی و ماهانه"},
                    {"key": "guarantee_rank", "label": "تضمین رتبه", "placeholder": "صفحه اول گوگل برای ۱۰ کلمه"},
                ]
            }
        )

        self.stdout.write("2. Creating Test Seller Users & Businesses...")
        from decimal import Decimal
        from django.utils import timezone
        from apps.discovery.models import (
            MonitoredCommunity,
            Customer,
            Opportunity,
            AIAnalysis,
            OpportunityProductMatch,
            Evidence,
        )

        test_sellers = [
            {
                "email": "seller@example.com",
                "password": "StrongPassword123!",
                "first_name": "مدیر",
                "last_name": "فروشگاه",
                "biz_name": "فروشگاه و آکادمی مرکزی پیدا",
                "biz_domain": "آموزش برنامه‌نویسی و تجهیزات دیجیتال",
                "desc": "سامانه فروشگاهی و پلتفرم تخصصی دوره‌های مهارتی و تجهیزات برنامه‌نویسی",
            },
            {
                "email": "seller@moshtariyab.com",
                "password": "demo123456",
                "first_name": "آرش",
                "last_name": "کاظمی",
                "biz_name": "آکادمی و بازرگانی نوین آرش",
                "biz_domain": "آموزش برنامه‌نویسی و تجهیزات دیجیتال",
                "desc": "ارائه تخصصی دوره‌های برنامه‌نویسی پایتون و هوش مصنوعی به همراه تأمین لپ‌تاپ‌های تخصصی مهندسی و پوشاک باکیفیت.",
            },
        ]

        for sdata in test_sellers:
            user, _ = User.objects.get_or_create(
                email=sdata["email"],
                defaults={
                    "username": sdata["email"],
                    "first_name": sdata["first_name"],
                    "last_name": sdata["last_name"],
                    "is_active": True,
                }
            )
            user.set_password(sdata["password"])
            user.save()

            business, _ = Business.objects.get_or_create(
                user=user,
                defaults={
                    "name": sdata["biz_name"],
                    "business_type": "HYBRID",
                    "business_domain": sdata["biz_domain"],
                    "description": sdata["desc"],
                    "location": "تهران",
                    "target_customer_description": "دانشجویان و علاقه‌مندان به ورود به بازار کار برنامه‌نویسی و خریداران لپ‌تاپ و پوشاک باکیفیت.",
                    "telegram_account_handle": "@PeydaSalesBot",
                    "daily_discovery_limit": 10,
                }
            )

            self.stdout.write(f"3. Adding Rich Sample Products for {sdata['email']}...")
            # Product 1: Python Course
            prod_python, _ = Product.objects.update_or_create(
                business=business,
                name="دوره جامع پایتون پروژه‌محور و ورود به بازار کار",
                defaults={
                    "product_type": "SERVICE",
                    "category": python_course,
                    "price": 3400000,
                    "url": "https://novinarash.ir/courses/python",
                    "description": "دوره آموزشی عمیق و کاربردی زبان پایتون با تمرکز روی ساخت ۵ پروژه واقعی برای رزومه، منتورینگ یک‌به‌یک خصوصی و آمادگی برای استخدام.",
                    "attributes": {
                        "duration": "۶۰ ساعت آموزش کاربردی",
                        "level": "مقدماتی تا پیشرفته",
                        "has_mentoring": "منتور اختصاصی و پشتیبانی ۲۴ ساعته",
                        "format": "آنلاین (ویدیو + پروژه‌های هفتگی)"
                    },
                    "target_customer": "کسانی که به دنبال یادگیری کاربردی پایتون و ورود سریع به بازار کار هستند و نیاز به تمرین عملی و منتور دارند.",
                    "status": "ACTIVE"
                }
            )

            # Product 2: Jeans
            prod_jeans, _ = Product.objects.update_or_create(
                business=business,
                name="شلوار جین اسلیم‌فیت مردانه سرمه‌ای تیره",
                defaults={
                    "product_type": "PHYSICAL",
                    "category": mens_jeans,
                    "price": 890000,
                    "url": "https://novinarash.ir/shop/mens-jeans-dark-blue",
                    "description": "شلوار جین با پارچه دنیم کشی اعلا، شست سنگشور باکیفیت، رنگ ثابت سرمه‌ای تیره، مناسب استفاده روزمره و محیط کاری با دوخت صنعتی دوبل.",
                    "attributes": {
                        "material": "دنیم پنبه‌ای کشسان ۹۸٪",
                        "cut": "اسلیم‌فیت راسته",
                        "size": "۳۱ تا ۳۸",
                        "color": "سرمه‌ای تیره کلاسیک"
                    },
                    "target_customer": "آقایان جوان و کارمندانی که به دنبال شلوار جین خوش‌پوش، راحت و بادوام با قیمت مناسب هستند.",
                    "status": "ACTIVE"
                }
            )

            # Product 3: Laptop
            prod_laptop, _ = Product.objects.update_or_create(
                business=business,
                name="لپ‌تاپ مهندسی ایسوس Vivobook Pro 15 (مخصوص برنامه‌نویسی)",
                defaults={
                    "product_type": "PHYSICAL",
                    "category": prog_laptops,
                    "price": 52000000,
                    "url": "https://novinarash.ir/shop/asus-vivobook-pro-15",
                    "description": "لپ‌تاپ قدرتمند با پردازنده Core i7 نسل ۱۳، ۱۶ گیگابایت رم DDR5 و صفحه نمایش OLED خیره‌کننده، ایده‌آل برای توسعه نرم‌افزار، ماشین لرنینگ و مالتی‌تسکینگ سنگین.",
                    "attributes": {
                        "cpu": "Intel Core i7-13700H",
                        "ram": "16GB DDR5 5200MHz",
                        "gpu": "RTX 4050 6GB",
                        "screen": "15.6 inch 2.8K OLED 120Hz"
                    },
                    "target_customer": "برنامه‌نویسان، توسعه‌دهندگان وب و دانشجویان مهندسی کامپیوتر که سرعت بالا، صفحه نمایش باکیفیت و دوام باتری برایشان اولویت است.",
                    "status": "ACTIVE"
                }
            )

            # Product 4: SEO Service
            prod_seo, _ = Product.objects.update_or_create(
                business=business,
                name="پکیج خدمات سئو ۳ ماهه و بازاریابی محتوایی",
                defaults={
                    "product_type": "SERVICE",
                    "category": seo_cat,
                    "price": 15000000,
                    "url": "https://novinarash.ir/services/seo-growth",
                    "description": "بهینه‌سازی کامل سئو تکنیکال، محتوایی و لینک‌سازی وب‌سایت‌های فروشگاهی و شرکتی به همراه تدوین استراتژی کلمات کلیدی و تولید ۲۰ مقاله تخصصی سئو شده.",
                    "attributes": {
                        "contract_term": "۳ ماهه",
                        "reporting": "گزارش هفتگی آنالیتیکس و سرچ کنسول",
                        "guarantee_rank": "تضمین حضور حداقل ۱۰ کلمه کلیدی در صفحه اول"
                    },
                    "target_customer": "صاحبان وب‌سایت‌های نوپا و فروشگاه‌های آنلاین که ورودی ارگانیک گوگل پایینی دارند و قصد افزایش فروش دارند.",
                    "status": "ACTIVE"
                }
            )

            self.stdout.write(f"4. Seeding Telegram Communities for {sdata['email']}...")
            telegram_communities = [
                {
                    "community_type": "GROUP",
                    "name": "برنامه‌نویسان و پایتون‌کاران ایران",
                    "handle_or_link": "@python_iran_dev",
                    "category": python_course,
                    "description": "جامعه عمومی برنامه‌نویسان، دانشجویان و متقاضیان دوره‌های پایتون و جنگو",
                    "members_count": 48500,
                    "messages_scanned_count": 3120,
                    "leads_discovered_count": 42,
                },
                {
                    "community_type": "CHANNEL",
                    "name": "بازار و خرید لپ‌تاپ‌های مهندسی",
                    "handle_or_link": "@it_laptops_iran",
                    "category": prog_laptops,
                    "description": "اطلاع‌رسانی قیمت‌ها و درخواست‌های خرید لپ‌تاپ‌های کاری و برنامه‌نویسی",
                    "members_count": 27300,
                    "messages_scanned_count": 1840,
                    "leads_discovered_count": 28,
                },
                {
                    "community_type": "GROUP",
                    "name": "استارتاپ‌ها، سئو و توسعه وب",
                    "handle_or_link": "@iran_seo_growth",
                    "category": seo_cat,
                    "description": "تبادل نظر صاحبان کسب‌وکار درباره سئو، بهینه‌سازی رتبه گوگل و کمپین‌های دیجیتال مارکتینگ",
                    "members_count": 19400,
                    "messages_scanned_count": 940,
                    "leads_discovered_count": 16,
                },
                {
                    "community_type": "CHANNEL",
                    "name": "پوشاک و استایل مردانه شیک‌پوشان",
                    "handle_or_link": "@mens_style_tehran",
                    "category": mens_jeans,
                    "description": "معرفی مدل‌های روز شلوار، کت و پوشاک آقایان و پاسخ به سوالات خریداران",
                    "members_count": 35200,
                    "messages_scanned_count": 1420,
                    "leads_discovered_count": 19,
                },
            ]

            for c in telegram_communities:
                MonitoredCommunity.objects.update_or_create(
                    business=business,
                    handle_or_link=c["handle_or_link"],
                    defaults={
                        "platform": "telegram",
                        "community_type": c["community_type"],
                        "name": c["name"],
                        "category": c["category"],
                        "description": c["description"],
                        "members_count": c["members_count"],
                        "messages_scanned_count": c["messages_scanned_count"],
                        "leads_discovered_count": c["leads_discovered_count"],
                        "is_active": True,
                        "last_scanned_at": timezone.now(),
                    }
                )

            self.stdout.write(f"5. Seeding Discovered Opportunities for {sdata['email']}...")
            sample_opportunities = [
                {
                    "sender_name": "فرهاد رستمی",
                    "sender_username": "farhad_dev_98",
                    "message": "سلام دوستان، من لیسانس کامپیوترم اما پروژه‌ای کار نکردم. کسی دوره خوب پایتون که از صفر تا پیشرفته یاد بده و حتما منتورینگ داشته باشه سراغ داره؟ بودجه تا ۳ الی ۴ تومن دارم، می‌خوام تا عید رزومه بسازم استخدام بشم.",
                    "category": python_course,
                    "category_path": "آموزش و آکادمی > برنامه‌نویسی و نرم‌افزار > زبان برنامه‌نویسی پایتون (Python) > دوره پایتون پروژه‌محور و ورود به بازار کار",
                    "product": prod_python,
                    "need": "آموزش جامع و پروژه‌محور پایتون به همراه منتورینگ اختصاصی جهت ورود سریع به بازار کار",
                    "intent_score": 0.94,
                    "product_fit_score": 0.98,
                    "confidence": 0.95,
                    "why_selected": "کاربر صریحاً تقاضای دوره پایتون با منتورینگ و بودجه ۳ تا ۴ میلیون تومانی کرده که انطباق ۹۸ درصدی با محصول دوره پایتون دارد.",
                    "suggested_reply": "سلام فرهاد عزیز، دوره پایتون پروژه‌محور نوین آرش با ۶۰ ساعت آموزش، ۵ پروژه رزومه‌ساز و منتورینگ یک‌به‌یک دقیقاً پاسخگوی نیاز شما برای ورود به بازار کار است. مایلید سرفصل‌های دوره رو براتون ارسال کنم؟",
                    "msg_id": "tg_msg_884920",
                    "status": "QUALIFIED",
                },
                {
                    "sender_name": "رضا صادقی",
                    "sender_username": "reza_frontend",
                    "message": "بچه‌ها لپ‌تاپ واسه کار برنامه‌نویسی تا حدود ۵۰ میلیون چی پیشنهاد میدین؟ حداقل ۱۶ گیگ رم و صفحه درست‌درمون داشته باشه چشمم موقع کد زدن طولانی نسوزه و باتریش خوب باشه.",
                    "category": prog_laptops,
                    "category_path": "کالای دیجیتال > رایانه و لپ‌تاپ > لپ‌تاپ مهندسی و برنامه‌نویسی",
                    "product": prod_laptop,
                    "need": "خرید لپ‌تاپ مهندسی مناسب برنامه‌نویسی با رم حداقل ۱۶ گیگ، نمایشگر باکیفیت و بودجه ۵۰ میلیون تومان",
                    "intent_score": 0.91,
                    "product_fit_score": 0.95,
                    "confidence": 0.92,
                    "why_selected": "تقاضای خرید لپ‌تاپ با مشخصات فنی صریح (رم ۱۶ گیگابایت، صفحه باکیفیت و بودجه ۵۰ میلیونی) کاملاً بر لپ‌تاپ مهندسی ایسوس منطبق است.",
                    "suggested_reply": "سلام آقا رضا، لپ‌تاپ مهندسی Asus Vivobook Pro 15 با صفحه نمایش ۲.۸K OLED (ضد خستگی چشم)، ۱۶GB رم DDR5 و پردازنده نسل ۱۳ Core i7 در همین بازه قیمتی قرار دارد. آیا مایل به دریافت مشخصات کامل و لینک خرید هستید؟",
                    "msg_id": "tg_msg_884921",
                    "status": "NEW",
                },
                {
                    "sender_name": "مریم کاظمیان",
                    "sender_username": "maryam_store_admin",
                    "message": "سلام دوستان سئوکار، برای فروشگاه اینترنتیمون که ۳ ماهه لانچ شده نیاز به یه تیم یا متخصص سئو داریم که بتونه ورودی گوگل رو افزایش بده و گزارش منظم بده. لطفاً اگر کسی پکیج ۳ ماهه مطمئن داره شرایط و تعرفه بفرسته.",
                    "category": seo_cat,
                    "category_path": "خدمات کسب‌وکار > سئو و بهینه‌سازی موتورهای جستجو",
                    "product": prod_seo,
                    "need": "خدمات سئو، ارتقای رتبه گوگل و بهینه‌سازی فروشگاه آنلاین با گزارش‌های تحلیلی منظم در قرارداد ۳ ماهه",
                    "intent_score": 0.96,
                    "product_fit_score": 0.92,
                    "confidence": 0.94,
                    "why_selected": "تقاضای تجاری قطعی برای دریافت خدمات سئو ۳ ماهه با تضمین رتبه و گزارش‌دهی دوره‌ای منطبق بر پکیج سئو نوین آرش.",
                    "suggested_reply": "سلام سرکار خانم کاظمیان، پکیج خدمات سئو ۳ ماهه ما شامل آنالیز تکنیکال، تولید محتوای تخصصی و گزارش هفتگی سرچ کنسول دقیقاً برای فروشگاه‌های نوپا طراحی شده است. مایلید یک جلسه بررسی رایگان هماهنگ کنیم؟",
                    "msg_id": "tg_msg_884922",
                    "status": "QUALIFIED",
                },
                {
                    "sender_name": "علی احمدی",
                    "sender_username": "ali_ahmadi_teh",
                    "message": "سلام، شلوار جین لی سرمه‌ای رنگ تیره راسته یا اسلیم که کش بیاد و پارچه‌ش جنس مرغوب باشه و رنگش نره از کجا می‌تونم سفارش بدم؟ سایز ۳۴ می‌خوام که دوام بالایی داشته باشه.",
                    "category": mens_jeans,
                    "category_path": "پوشاک و مد > پوشاک مردانه > شلوار مردانه > شلوار جین لی مردانه",
                    "product": prod_jeans,
                    "need": "خرید شلوار جین لی سرمه‌ای تیره اسلیم‌فیت با پارچه کشسان و باکیفیت در سایز ۳۴",
                    "intent_score": 0.88,
                    "product_fit_score": 0.97,
                    "confidence": 0.91,
                    "why_selected": "نیاز قطعی به خرید شلوار جین با رنگ و مشخصات منطبق با شلوار جین اسلیم‌فیت سرمه‌ای تیره فروشگاه.",
                    "suggested_reply": "سلام علی آقا، شلوار جین اسلیم‌فیت دنیم کشسان نوین آرش در سایز ۳۴ و رنگ سرمه‌ای تیره کلاسیک با شست سنگشور باکیفیت و دوام بالا موجود است. در صورت تمایل لینک مشاهده جزئیات خدمت شما ارسال شود؟",
                    "msg_id": "tg_msg_884923",
                    "status": "REVIEWED",
                },
            ]

            for op_data in sample_opportunities:
                customer, _ = Customer.objects.get_or_create(
                    business=business,
                    source_platform="telegram",
                    source_username=op_data["sender_username"],
                    defaults={
                        "name": op_data["sender_name"],
                        "external_user_id": f"tg_usr_{op_data['sender_username']}",
                        "source_profile_url": f"https://t.me/{op_data['sender_username']}",
                    }
                )

                opp, _ = Opportunity.objects.update_or_create(
                    business=business,
                    source_message_id=op_data["msg_id"],
                    defaults={
                        "customer": customer,
                        "category": op_data["category"],
                        "category_name_snapshot": op_data["category"].name,
                        "category_path_snapshot": op_data["category_path"],
                        "category_confidence": op_data["confidence"],
                        "source_platform": "telegram",
                        "source_raw_message": op_data["message"],
                        "normalized_message": op_data["message"],
                        "status": op_data["status"],
                        "source_message_timestamp": timezone.now(),
                        "trace_metadata": {
                            "pipeline_version": "2.0.0",
                            "filter_passed": True,
                            "routing_depth": 4,
                        }
                    }
                )

                AIAnalysis.objects.update_or_create(
                    opportunity=opp,
                    defaults={
                        "need": op_data["need"],
                        "intent_score": op_data["intent_score"],
                        "product_fit_score": op_data["product_fit_score"],
                        "confidence": op_data["confidence"],
                        "why_selected": op_data["why_selected"],
                        "suggested_reply": op_data["suggested_reply"],
                        "model_name": "llama-3.3-70b-versatile",
                        "tokens_used": 185,
                        "cost_usd": Decimal("0.000185"),
                        "cost_toman": 18,
                    }
                )

                OpportunityProductMatch.objects.update_or_create(
                    opportunity=opp,
                    product=op_data["product"],
                    defaults={
                        "match_score": op_data["product_fit_score"],
                        "recommendation_reason": op_data["why_selected"],
                        "rank": 1,
                    }
                )

                Evidence.objects.update_or_create(
                    opportunity=opp,
                    evidence_type="customer_message",
                    defaults={
                        "content": op_data["message"][:200],
                        "source_reference": f"پیام کاربر @{op_data['sender_username']} در تلگرام",
                    }
                )

        self.stdout.write(self.style.SUCCESS(
            "SUCCESS! Rich seed completed on PostgreSQL.\n"
            "Test Sellers:\n"
            "1. seller@example.com | Password: StrongPassword123!\n"
            "2. seller@moshtariyab.com | Password: demo123456\n"
            "Categories, Products, Telegram Monitored Communities, and Discovered Opportunities seeded."
        ))

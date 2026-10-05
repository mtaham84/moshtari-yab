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

        self.stdout.write("2. Creating Test Seller User & Business...")
        seller_email = "seller@moshtariyab.com"
        user, created = User.objects.get_or_create(
            email=seller_email,
            defaults={
                "username": seller_email,
                "first_name": "آرش",
                "last_name": "کاظمی",
                "is_active": True,
            }
        )
        user.set_password("demo123456")
        user.save()

        business, _ = Business.objects.get_or_create(
            user=user,
            defaults={
                "name": "آکادمی و بازرگانی نوین آرش",
                "business_type": "HYBRID",
                "business_domain": "آموزش برنامه‌نویسی و تجهیزات دیجیتال",
                "description": "ارائه تخصصی دوره‌های برنامه‌نویسی پایتون و هوش مصنوعی به همراه تأمین لپ‌تاپ‌های تخصصی مهندسی و پوشاک باکیفیت.",
                "location": "تهران",
                "target_customer_description": "دانشجویان و علاقه‌مندان به ورود به بازار کار برنامه‌نویسی و مهندسان نرم‌افزار نیازمند لپ‌تاپ پرسرعت.",
            }
        )

        self.stdout.write("3. Adding Rich Sample Products...")
        # Product 1: Python Course
        Product.objects.update_or_create(
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
        Product.objects.update_or_create(
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
        Product.objects.update_or_create(
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
        Product.objects.update_or_create(
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

        self.stdout.write(self.style.SUCCESS(
            "SUCCESS! Seed completed.\n"
            "Test Seller: seller@moshtariyab.com | Password: demo123456\n"
            "Business: آکادمی و بازرگانی نوین آرش\n"
            "4 Products & Full Category Tree Seeded."
        ))

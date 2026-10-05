from .models import Category

def suggest_category_for_product(name: str, description: str = "", product_type: str = None):
    """
    Analyzes product name and description to suggest the most accurate leaf category in the taxonomy.
    """
    text = f"{name} {description}".lower()

    # Query all active categories with their ancestors
    categories = Category.objects.filter(is_active=True).select_related("parent")
    if product_type:
        categories = categories.filter(product_type=product_type)

    best_match = None
    best_score = 0
    best_reason = ""

    # Keyword mappings for high-accuracy MVP matching
    domain_keywords = {
        "پایتون": ("دوره-پایتون", "شناسایی کلمات کلیدی پایتون و کدنویسی", 96),
        "برنامه": ("برنامه-نویسی", "تشخیص حوزه مهندسی نرم‌افزار و برنامه‌نویسی", 92),
        "طراحی وب": ("طراحی-وب", "تشخیص حوزه طراحی سایت و فرانت‌اند", 90),
        "سئو": ("سئو-مارکتینگ", "تشخیص خدمات بهینه‌سازی موتورهای جستجو", 94),
        "مارکتینگ": ("سئو-مارکتینگ", "تشخیص خدمات دیجیتال مارکتینگ", 89),
        "جین": ("شلوار-جین", "تشخیص پوشاک دنیم و شلوار جین", 97),
        "شلوار": ("شلوار-مردانه", "تشخیص پوشاک و شلوار", 91),
        "مانتو": ("مانتو-زنانه", "تشخیص پوشاک بانوان و مانتو", 95),
        "پیراهن": ("پوشاک-مردانه", "تشخیص البسه مردانه", 88),
        "سامسونگ": ("موبایل-سامسونگ", "تشخیص برند سامسونگ و گوشی هوشمند", 98),
        "آیفون": ("موبایل-اپل", "تشخیص برند اپل و تلفن هوشمند", 98),
        "موبایل": ("گوشی-هوشمند", "تشخیص رسته تلفن همراه و گجت", 92),
        "لپتاپ": ("لپ-تاپ", "تشخیص رسته رایانه همراه و لپ‌تاپ", 94),
        "لپ تاپ": ("لپ-تاپ", "تشخیص رسته رایانه همراه و لپ‌تاپ", 94),
        "کفش": ("کفش-اسپرت", "تشخیص رسته پاپوش و کفش", 93),
        "مشاوره": ("مشاوره-کسب-و-کار", "تشخیص خدمات مشاوره‌ای", 90),
    }

    # 1. First priority: Check domain keywords for specific leaf
    for kw, (slug_hint, reason, score) in domain_keywords.items():
        if kw in text:
            target = categories.filter(slug__icontains=slug_hint).first()
            if not target:
                target = categories.filter(name__icontains=kw).first()
            if target and score > best_score:
                best_match = target
                best_score = score
                best_reason = f"بر اساس کلیدواژه «{kw}»، {reason}."

    # 2. Second pass: general name matching
    if not best_match:
        for cat in categories:
            cat_name = cat.name.lower()
            if cat_name in text:
                score = 70 + (10 if not cat.children.exists() else 0)
                if score > best_score:
                    best_match = cat
                    best_score = score
                    best_reason = f"تطابق مستقیم با نام دسته «{cat.name}»."

    # 3. Fallback to first available category if no match
    if not best_match:
        best_match = categories.filter(parent__isnull=False).first() or categories.first()
        best_score = 55
        best_reason = "دسته‌بندی عمومی بر اساس نوع محصول پیشنهادی."

    if not best_match:
        return None

    return {
        "category_id": best_match.id,
        "name": best_match.name,
        "full_path": best_match.get_full_path(),
        "product_type": best_match.product_type,
        "confidence": best_score,
        "reason": best_reason,
        "suggested_attributes": best_match.suggested_attributes or [],
    }

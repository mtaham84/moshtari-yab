import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.core.form_helpers import parse_bounded_int, parse_form_list
from apps.core.image_service import validate_and_sanitize_image
from .models import Category, Product, ProductImage

log = logging.getLogger(__name__)

# High-frequency Iranian e-commerce keyword synonyms to category concepts
SYNONYM_GROUPS: dict[str, list[str]] = {
    # Underwear / Socks
    "شورت": ["لباس زیر و جوراب", "لباس زیر", "شورت"],
    "سوتین": ["لباس زیر و جوراب", "لباس زیر", "سوتین"],
    "زیرپوش": ["لباس زیر و جوراب", "لباس زیر", "زیرپوش"],
    "مایو": ["لباس زیر و جوراب", "مایو", "ورزشی"],
    "جوراب": ["لباس زیر و جوراب", "جوراب"],
    "کاستوم": ["لباس زیر و جوراب", "لباس خواب و راحتی"],
    "گن": ["لباس زیر و جوراب", "گن"],
    "لباس زیر": ["لباس زیر و جوراب", "لباس زیر"],

    # Sleepwear
    "خواب": ["لباس خواب و راحتی", "لباس راحتی"],
    "راحتی": ["لباس خواب و راحتی", "لباس راحتی"],
    "پیژامه": ["لباس خواب و راحتی", "لباس راحتی"],
    "روبدوشامبر": ["لباس خواب و راحتی"],

    # Shirts & Tops
    "پیراهن": ["پیراهن مردانه", "پیراهن و پیراهن مجلسی", "پیراهن دخترانه", "پیراهن"],
    "شومیز": ["بلوز و شومیز", "شومیز"],
    "بلوز": ["بلوز و شومیز", "بلوز و شلوار پسرانه", "بلوز"],
    "تیشرت": ["تیشرت و پولوشرت", "تیشرت و تاپ", "تیشرت"],
    "تی شرت": ["تیشرت و پولوشرت", "تیشرت و تاپ", "تیشرت"],
    "تاپ": ["تیشرت و تاپ", "تاپ"],
    "پولوشرت": ["تیشرت و پولوشرت", "پولوشرت"],
    "پلوشرت": ["تیشرت و پولوشرت"],
    "تونیک": ["بلوز و شومیز", "مانتو و پالتو"],
    "بادی": ["سرهمی و بادی", "تیشرت و تاپ"],

    # Outerwear
    "مانتو": ["مانتو و پالتو", "مانتو"],
    "پالتو": ["مانتو و پالتو", "کاپشن و پالتو", "پالتو"],
    "کاپشن": ["کاپشن و پالتو", "کاپشن و بارانی", "کاپشن"],
    "بارانی": ["کاپشن و پالتو", "کاپشن و بارانی"],
    "کت": ["کت و شلوار", "کت"],
    "کت و شلوار": ["کت و شلوار"],
    "هودی": ["هودی و سویشرت", "هودی"],
    "سویشرت": ["هودی و سویشرت", "سویشرت"],
    "سوئیشرت": ["هودی و سویشرت"],
    "بافت": ["پلیور و بافت", "ژاکت", "لباس راحتی"],
    "پلیور": ["پلیور و بافت", "ژاکت"],

    # Bottoms
    "شلوار": ["شلوار مردانه", "شلوار جین و کتان", "شلوار و سرهمی", "بلوز و شلوار پسرانه", "شلوار"],
    "جین": ["شلوار جین و کتان", "شلوار جین لی مردانه", "شلوار جین", "جین"],
    "کتان": ["شلوار جین و کتان", "شلوار مردانه"],
    "اسلش": ["شلوار مردانه", "شلوار و سرهمی", "لباس راحتی"],
    "لگ": ["شلوار و سرهمی", "ورزشی"],
    "دامن": ["دامن و شلوارک", "دامن"],
    "شلوارک": ["دامن و شلوارک", "لباس راحتی"],

    # Shoes & Footwear
    "کفش": ["کفش رسمی مردانه", "کفش پاشنه‌بلند زنانه", "کفش روزمره", "کفش ورزشی و کتانی", "کفش"],
    "کتانی": ["کفش ورزشی و کتانی", "کفش روزمره"],
    "صندل": ["صندل و دمپایی", "صندل"],
    "دمپایی": ["صندل و دمپایی", "دمپایی"],
    "بوت": ["نیم‌بوت و بوت", "بوت"],
    "نیم بوت": ["نیم‌بوت و بوت"],
    "پاشنه بلند": ["کفش پاشنه‌بلند زنانه"],

    # Bags
    "کیف": ["کیف دستی زنانه", "کیف دوشی و کمری", "کوله پشتی", "کیف لپ‌تاپ", "کیف"],
    "کوله": ["کوله پشتی"],
    "کوله پشتی": ["کوله پشتی"],
    "چمدان": ["چمدان و ساک مسافرتی", "چمدان"],
    "ساک": ["چمدان و ساک مسافرتی"],

    # Accessories
    "ساعت": ["ساعت مچی", "ساعت"],
    "عینک": ["عینک آفتابی", "عینک"],
    "شال": ["شال و روسری", "شال"],
    "روسری": ["شال و روسری", "روسری"],
    "کلاه": ["کلاه و دستکش", "کلاه"],
    "دستکش": ["کلاه و دستکش", "دستکش"],
    "کمربند": ["کمربند"],
    "کراوات": ["کراوات و پاپیون", "کراوات"],
    "پاپیون": ["کراوات و پاپیون"],
    "بدلیجات": ["بدلیجات و زیورآلات", "بدلیجات"],
    "زیورآلات": ["بدلیجات و زیورآلات", "طلا، جواهر و زیورآلات"],
    "دستبند": ["بدلیجات و زیورآلات"],
    "گردنبند": ["بدلیجات و زیورآلات"],
    "گوشواره": ["بدلیجات و زیورآلات"],
    "انگشتر": ["بدلیجات و زیورآلات"],

    # Digital & Tech
    "پایتون": ["دوره پایتون", "آموزش پایتون", "برنامه‌نویسی"],
    "جنگو": ["دوره پایتون", "برنامه‌نویسی"],
    "برنامه نویسی": ["برنامه‌نویسی", "خدمات نرم‌افزاری"],
    "کدنویسی": ["برنامه‌نویسی"],
    "طراحی وب": ["طراحی-وب", "طراحی سایت"],
    "طراحی سایت": ["طراحی سایت", "خدمات نرم‌افزاری"],
    "سئو": ["سئو", "دیجیتال مارکتینگ"],
    "مارکتینگ": ["دیجیتال مارکتینگ", "سئو"],
    "لپتاپ": ["لپ‌تاپ", "رایانه همراه"],
    "لپ تاپ": ["لپ‌تاپ", "رایانه همراه"],
    "گوشی": ["گوشی هوشمند", "موبایل"],
    "موبایل": ["گوشی هوشمند", "موبایل"],
    "آیفون": ["گوشی هوشمند", "موبایل-اپل", "موبایل"],
    "سامسونگ": ["گوشی هوشمند", "موبایل-سامسونگ", "موبایل"],
    "مانیتور": ["مانیتور", "قطعات کامپیوتر"],
    "هدفون": ["هدفون و هندزفری", "لوازم جانبی دیجیتال"],
    "هندزفری": ["هدفون و هندزفری", "لوازم جانبی دیجیتال"],

    # Health & Dental
    "دندانپزشکی": ["دندان‌پزشکی", "خدمات دندانپزشکی"],
    "ایمپلنت": ["ایمپلنت", "خدمات دندانپزشکی"],
    "ارتودنسی": ["ارتودنسی", "خدمات دندانپزشکی"],
    "کامپوزیت": ["کامپوزیت", "خدمات دندانپزشکی"],
    "لمینت": ["لمینت", "خدمات دندانپزشکی"],
}

ACCESSORY_WORDS = {"ساعت", "عینک", "شال", "روسری", "کلاه", "دستکش", "کمربند", "کراوات", "پاپیون", "بدلیجات", "زیورآلات", "دستبند", "گردنبند", "گوشواره", "انگشتر"}
WOMEN_SPECIFIC_WORDS = {"زنانه", "دخترانه", "خانم", "بانوان", "دختر", "مانتو", "شومیز", "سوتین", "دامن", "روسری", "چادر"}
MEN_SPECIFIC_WORDS = {"مردانه", "پسرانه", "آقا", "آقایان", "پسر", "کراوات", "پاپیون", "کت و شلوار"}
KIDS_SPECIFIC_WORDS = {"بچگانه", "کودک", "نوزاد", "نوزادی", "بچه"}


def suggest_category_for_product(name: str, description: str = "", product_type: str | None = None,
                                 business: Any = None, allow_ai: bool = False) -> dict[str, Any] | None:
    """
    Analyzes product name and description to suggest the most specific leaf category (preferring level 3).
    Prioritizes seller's own categories, but also searches system categories and auto-adopts the best leaf
    if the seller has not yet adopted it.
    """
    clean_name = (name or "").strip()
    if not clean_name:
        return None

    text = f"{clean_name} {description or ''}".lower()
    query_words = set(re.findall(r"[\w]+", text))

    # 1. Fetch candidate categories:
    # First priority: business's own categories
    own_categories: list[Category] = []
    if business:
        own_qs = Category.objects.filter(business=business, is_active=True).select_related("parent")
        if product_type in ("PHYSICAL", "SERVICE"):
            own_qs = own_qs.filter(product_type=product_type)
        own_categories = list(own_qs)

    # Second priority: system categories (business__isnull=True)
    sys_qs = Category.objects.filter(business__isnull=True, is_active=True).select_related("parent")
    if product_type in ("PHYSICAL", "SERVICE"):
        sys_qs = sys_qs.filter(product_type=product_type)

    # If business has specific business_types, restrict system categories to those roots
    if business and hasattr(business, "business_types") and business.business_types.exists():
        root_ids = [bt.root_category_id for bt in business.business_types.all() if bt.root_category_id]
        if root_ids:
            root_paths = list(Category.objects.filter(id__in=root_ids).values_list("full_path", flat=True))
            q_roots = Q()
            for rp in root_paths:
                q_roots |= Q(full_path__startswith=rp)
            sys_qs = sys_qs.filter(q_roots)

    system_categories = list(sys_qs)

    # Combine: keep own categories first, and system categories that haven't been adopted yet
    adopted_origin_ids = {c.origin_id for c in own_categories if c.origin_id}
    unadopted_sys = [c for c in system_categories if c.id not in adopted_origin_ids]
    categories = own_categories + unadopted_sys

    if not categories:
        # Fallback to active categories strictly scoped to caller's business or system (no foreign leak)
        fallback_qs = Category.objects.filter(is_active=True).select_related("parent")
        if business:
            fallback_qs = fallback_qs.filter(Q(business=business) | Q(business__isnull=True))
        else:
            fallback_qs = fallback_qs.filter(business__isnull=True)
        categories = list(fallback_qs)

    if not categories:
        return None

    # Detect gender / target audience intent
    is_women = bool(query_words.intersection(WOMEN_SPECIFIC_WORDS))
    is_men = bool(query_words.intersection(MEN_SPECIFIC_WORDS))
    is_kids = bool(query_words.intersection(KIDS_SPECIFIC_WORDS))
    has_accessory_intent = bool(query_words.intersection(ACCESSORY_WORDS))

    # Pre-build children count to detect leaf status and depth
    children_count: dict[int, int] = {}
    for c in categories:
        if c.parent_id:
            children_count[c.parent_id] = children_count.get(c.parent_id, 0) + 1

    best_match: Category | None = None
    best_score = -1000
    best_reason = ""

    for cat in categories:
        cname = cat.name.lower()
        full_path = cat.get_full_path().lower()
        is_leaf = (children_count.get(cat.id, 0) == 0)
        depth = cat.depth or (len(cat.get_ancestors()) + 1)

        relevance_score = 0
        has_positive_match = False
        match_reasons = []

        # 1. Direct name matching
        if cname == clean_name.lower():
            relevance_score += 170
            has_positive_match = True
            match_reasons.append(f"تطابق دقیق عنوان «{cat.name}»")
        elif cname in clean_name.lower():
            relevance_score += 110
            has_positive_match = True
            match_reasons.append(f"تطابق عبارت «{cat.name}»")
        elif clean_name.lower() in cname:
            relevance_score += 75
            has_positive_match = True
            match_reasons.append(f"هم‌پوشانی با «{cat.name}»")

        # 2. Word overlap between query and category name
        cat_words = set(re.findall(r"[\w]+", cname))
        overlap = query_words.intersection(cat_words)
        if overlap:
            relevance_score += len(overlap) * 40
            has_positive_match = True

        # 3. Synonym and concept matching with priority ranking
        matched_synonyms = []
        for syn_word, target_concepts in SYNONYM_GROUPS.items():
            if syn_word in query_words:
                for idx, concept in enumerate(target_concepts):
                    # Earlier concepts in the list get a higher ranking priority
                    prio_bonus = max(0, 20 - (idx * 6))
                    if concept in cname:
                        relevance_score += 130 + prio_bonus
                        matched_synonyms.append(syn_word)
                        has_positive_match = True
                        break
                    elif concept in full_path or (concept in cat.slug if cat.slug else False):
                        relevance_score += 70 + prio_bonus
                        matched_synonyms.append(syn_word)
                        has_positive_match = True
                        break

        if matched_synonyms:
            match_reasons.append(f"تشخیص مفهوم تخصصی «{', '.join(set(matched_synonyms))}»")

        # 4. Gender / Audience Filtering
        if is_women:
            if "زنانه" in full_path or "دخترانه" in full_path:
                relevance_score += 85
            if "مردانه" in full_path or "پسرانه" in full_path:
                relevance_score -= 300
            if "اکسسوری" in full_path and not has_accessory_intent:
                relevance_score -= 250

        if is_men:
            if "مردانه" in full_path or "پسرانه" in full_path:
                relevance_score += 85
            if "زنانه" in full_path or "دخترانه" in full_path:
                relevance_score -= 300
            if "اکسسوری" in full_path and not has_accessory_intent:
                relevance_score -= 250

        if is_kids:
            if "بچگانه" in full_path or "نوزاد" in full_path:
                relevance_score += 90
            elif "مردانه" in full_path or "زنانه" in full_path:
                relevance_score -= 100

        # Preference for business's already-owned categories over unadopted system nodes
        if business and cat.business_id == getattr(business, "id", None):
            relevance_score += 25

        # 5. Calculate Final Score ONLY if there was positive topical match
        if has_positive_match and relevance_score > 0:
            score = relevance_score + (depth * 35)
            if is_leaf:
                score += 50
            if depth == 1:
                score -= 120  # Never choose top-level root category if subcategories exist
        else:
            score = -500  # Disqualified if no topical match

        if score > best_score:
            best_score = score
            best_match = cat
            best_reason = "؛ ".join(match_reasons) if match_reasons else f"تطابق شاخه «{cat.name}» در سطح {depth}"

    # Threshold check: requires a genuine match score (>= 100)
    if best_match and best_score >= 100:
        # If best match is a system category and we have a tenant business, auto-adopt it!
        if business and best_match.business_id is None:
            try:
                from apps.products.taxonomy import adopt_nodes
                adopt_nodes(business, [best_match.id])
                adopted = Category.objects.filter(business=business, origin=best_match, is_active=True).first()
                if adopted:
                    best_match = adopted
            except Exception as exc:
                log.warning("Auto-adoption of category %s failed: %s", best_match.id, exc)

        confidence = min(99, max(75, int(60 + (best_score / 6))))
        return {
            "category_id": best_match.id,
            "name": best_match.name,
            "full_path": best_match.get_full_path(),
            "product_type": best_match.product_type,
            "confidence": confidence,
            "reason": best_reason,
            "suggested_attributes": best_match.suggested_attributes or [],
        }

    # P02.01: If heuristic matching yielded no result and allow_ai is False (e.g. GET/unmetered requests), do not invoke LLM
    if not allow_ai:
        return None

    # If heuristic score is insufficient and AI is authorized, attempt AI model classification
    try:
        from apps.core.llm import chat_json
        tree_lines = [f"{c.id}: {c.get_full_path()}" for c in categories]
        system_prompt = (
            "You are an expert Iranian marketplace taxonomy classifier. "
            "Given a product title and description, select the SINGLE BEST category ID from the candidate categories. "
            "CRITICAL: Always select the deepest, most specific leaf category (level 3 or deepest level). "
            "Never select a parent category when a specific child fits. "
            "Return strictly JSON: {\"category_id\": <number>, \"reason\": \"<Persian explanation>\"}"
        )
        user_prompt = f"Product title: {clean_name}\nDescription: {description}\n\nCATEGORIES:\n" + "\n".join(tree_lines[:60])
        ai_resp = chat_json(system_prompt, user_prompt, purpose="suggest_category", max_tokens=150, timeout=12.0)
        if isinstance(ai_resp, dict) and ai_resp.get("category_id"):
            ai_cat_id = int(ai_resp["category_id"])
            matched_cat = next((c for c in categories if c.id == ai_cat_id), None)
            if matched_cat:
                # P02.01 / R13 Defense: Even if AI hallucinates an ID, ensure it cannot be a foreign category
                if matched_cat.business_id and matched_cat.business_id != getattr(business, "id", None):
                    log.warning("AI suggested foreign category %s for business %s; strictly discarded.", matched_cat.id, getattr(business, "id", None))
                    return None

                if business and matched_cat.business_id is None:
                    try:
                        from apps.products.taxonomy import adopt_nodes
                        adopt_nodes(business, [matched_cat.id])
                        adopted = Category.objects.filter(business=business, origin=matched_cat, is_active=True).first()
                        if adopted:
                            matched_cat = adopted
                    except Exception as exc:
                        log.warning("Auto-adoption after AI failed: %s", exc)

                return {
                    "category_id": matched_cat.id,
                    "name": matched_cat.name,
                    "full_path": matched_cat.get_full_path(),
                    "product_type": matched_cat.product_type,
                    "confidence": 95,
                    "reason": str(ai_resp.get("reason") or f"تشخیص توسط هوش مصنوعی بر اساس رسته «{matched_cat.name}»"),
                    "suggested_attributes": matched_cat.suggested_attributes or [],
                }
    except Exception as exc:
        log.warning("AI category classification fallback failed: %s", exc)

    return None


# ---------------------------------------------------------------------------
# Product form, validation, and lifecycle services (P08.17 - R39)
# ---------------------------------------------------------------------------

VALID_PRODUCT_TYPES = {"PHYSICAL", "SERVICE"}
VALID_STATUSES = {"ACTIVE", "INACTIVE"}


def categories_for_business(business):
    """Sellers who set up their own tree pick only from it; older tenants keep the global + custom list."""
    own = Category.objects.filter(business=business, is_active=True)
    if own.exists():
        return own.select_related("parent").order_by("full_path")
    return Category.objects.filter(
        Q(business__isnull=True) | Q(business=business),
        is_active=True,
    ).select_related("parent")


def parse_and_validate_price(price_val: str | None) -> tuple[Decimal | None, str | None]:
    if price_val is None or not str(price_val).strip():
        return None, None
    clean_val = str(price_val).strip().replace(",", "").replace("،", "").replace(" ", "")
    try:
        dec = Decimal(clean_val)
        if dec < 0:
            return None, "قیمت نمی‌تواند منفی باشد."
        if dec > Decimal("999999999999"):
            return None, "قیمت واردشده بیش از سقف مجاز است."
        return dec.quantize(Decimal("1")), None
    except (InvalidOperation, ValueError, TypeError):
        return None, "قیمت واردشده نامعتبر است؛ لطفاً یک مقدار عددی صحیح وارد نمایید."


def validate_product_input(data: dict, categories_qs) -> tuple[dict, list[str]]:
    """
    R20 & R19 Unified Form Validator:
    Enforces identical validation across add and edit views:
    - name and description are required
    - product_type, status, and discovery_priority choices/ranges are enforced
    - price is safely validated as Decimal (never silent None or float truncation)
    - checkboxes switch to False when unchecked on form submission (R19 fix)
    """
    errors = []
    cleaned = {}

    name = data.get("name", "").strip()[:255]
    if not name:
        errors.append("نام محصول یا خدمت الزامی است.")
    cleaned["name"] = name

    desc = data.get("description", "").strip()
    if not desc:
        errors.append("شرح مزایا و کاربرد محصول برای شناخت ایجنت الزامی است.")
    cleaned["description"] = desc

    ptype = data.get("product_type", "PHYSICAL").strip()
    if ptype not in VALID_PRODUCT_TYPES:
        errors.append("نوع محصول باید «کالایی» یا «خدماتی» باشد.")
    cleaned["product_type"] = ptype

    status = data.get("status", "ACTIVE").strip()
    if status not in VALID_STATUSES:
        errors.append("وضعیت محصول نامعتبر است.")
    cleaned["status"] = status

    prio_str = data.get("discovery_priority", "1")
    try:
        prio = int(prio_str)
        if not (1 <= prio <= 5):
            errors.append("اولویت جستجو باید بین ۱ تا ۵ باشد.")
        cleaned["discovery_priority"] = prio
    except (ValueError, TypeError):
        errors.append("اولویت جستجو باید عددی بین ۱ تا ۵ باشد.")
        cleaned["discovery_priority"] = 1

    price_val = data.get("price", "")
    price, price_err = parse_and_validate_price(price_val)
    if price_err:
        errors.append(price_err)
    cleaned["price"] = price

    cat_id = data.get("category_id")
    cleaned["category"] = None
    if cat_id and str(cat_id).strip() and str(cat_id).isdigit():
        cat = categories_qs.filter(id=int(cat_id)).first()
        if not cat:
            errors.append("دسته‌بندی انتخابی معتبر نیست.")
        else:
            cleaned["category"] = cat

    # Checkboxes: when full form submitted, missing key in POST means False (fixes R19)
    cleaned["instagram_discovery_enabled"] = data.get("instagram_discovery_enabled") in ["on", "true", "1"]
    cleaned["is_discovery_active"] = data.get("is_discovery_active") in ["on", "true", "1"]
    cleaned["telegram_outreach_enabled"] = data.get("telegram_outreach_enabled") in ["on", "true", "1"]
    cleaned["x_outreach_enabled"] = data.get("x_outreach_enabled") in ["on", "true", "1"]
    cleaned["x_search_enabled"] = data.get("x_search_enabled") in ["on", "true", "1"]

    cleaned["url"] = data.get("url", "").strip()[:2048]
    cleaned["target_customer"] = data.get("target_customer", "").strip()
    cleaned["instagram_terms"] = parse_form_list(data.get("instagram_terms", ""))
    cleaned["instagram_hashtags"] = parse_form_list(data.get("instagram_hashtags", ""), hashtags=True)
    cleaned["instagram_max_posts"] = parse_bounded_int(data.get("instagram_max_posts"), 5, 1, 100)
    cleaned["instagram_window_days"] = parse_bounded_int(data.get("instagram_window_days"), 7, 1, 90)

    return cleaned, errors


def parse_dynamic_attributes(post_data) -> dict[str, str]:
    """Parses dynamic custom and category-suggested attributes from POST data."""
    attributes = {}
    attr_keys = post_data.getlist("custom_attr_key")
    attr_vals = post_data.getlist("custom_attr_value")
    for k, v in zip(attr_keys, attr_vals):
        k_clean = k.strip()[:100]
        v_clean = v.strip()[:500]
        if k_clean and v_clean:
            attributes[k_clean] = v_clean

    for key, val in post_data.items():
        if key.startswith("attr_") and val.strip():
            attr_name = key[5:][:100]
            if attr_name not in attributes:
                attributes[attr_name] = val.strip()[:500]
    return attributes


def sanitize_and_validate_images(images: list, max_allowed: int = 10) -> tuple[list, list[str]]:
    """
    Validates and sanitizes a list of uploaded images using image_service.
    Returns (valid_images, warnings).
    """
    valid_images = []
    warnings = []
    for img_file in images[:max_allowed]:
        try:
            clean_img = validate_and_sanitize_image(img_file)
            valid_images.append(clean_img)
        except DjangoValidationError as val_err:
            warnings.append(f"فایل «{getattr(img_file, 'name', 'تصویر')}» نادیده گرفته شد: {val_err.message}")
        except Exception as exc:
            warnings.append(f"خطا در پردازش تصویر «{getattr(img_file, 'name', 'تصویر')}»: {exc}")
    return valid_images, warnings


def save_product(
    business,
    cleaned_data: dict,
    attributes: dict,
    valid_images: list,
    *,
    product: Product | None = None,
    main_img_index: int = 0,
) -> tuple[Product, bool]:
    """
    Atomically creates or updates a product along with its images.
    Performs compensatory disk cleanup if transaction fails.
    Returns (saved_product, is_organizing_flag).
    """
    from apps.products import taxonomy

    staged_images = []
    is_new = product is None

    try:
        with transaction.atomic():
            if is_new:
                product = Product(
                    business=business,
                    attributes=attributes,
                    **cleaned_data,
                )
            else:
                for field, val in cleaned_data.items():
                    setattr(product, field, val)
                product.attributes = attributes

            product.full_clean()
            product.save()

            if is_new:
                for idx, clean_img in enumerate(valid_images):
                    pi = ProductImage.objects.create(
                        product=product,
                        image=clean_img,
                        is_main=(idx == main_img_index or (main_img_index >= len(valid_images) and idx == 0)),
                        order=idx,
                    )
                    staged_images.append(pi.image)
            else:
                has_main = product.images.filter(is_main=True).exists()
                current_images_count = product.images.count()
                for idx, clean_img in enumerate(valid_images):
                    pi = ProductImage.objects.create(
                        product=product,
                        image=clean_img,
                        is_main=(not has_main and idx == 0),
                        order=current_images_count + idx,
                    )
                    staged_images.append(pi.image)

    except Exception:
        # Compensatory cleanup: if transaction rolled back, purge any staged files written to disk
        for img_field in staged_images:
            try:
                if img_field and img_field.storage.exists(img_field.name):
                    img_field.storage.delete(img_field.name)
            except Exception:
                pass
        raise

    organizing = taxonomy.maybe_organize(business)
    return product, organizing


def resolve_product_save_error(exc: Exception) -> str:
    """Translates database or validation exceptions into safe, user-friendly Persian messages."""
    from apps.core.typed_errors import sanitize_text

    if isinstance(exc, DjangoValidationError):
        return f"داده‌های محصول نامعتبر است: {sanitize_text(exc)}"
    elif isinstance(exc, IntegrityError):
        return "خطا در ثبت محصول: داده تکراری یا تداخل یکپارچگی در اطلاعات وجود دارد."
    return "خطا در پردازش و ذخیره اطلاعات محصول. لطفاً مقادیر وارد شده را بررسی فرمایید."


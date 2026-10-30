import logging
import re
from typing import Any
from django.db.models import Q
from .models import Category

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
                                 business: Any = None) -> dict[str, Any] | None:
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
        # Fallback to all active categories if still empty
        categories = list(Category.objects.filter(is_active=True).select_related("parent"))

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

    # If heuristic score is insufficient, attempt AI model classification
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

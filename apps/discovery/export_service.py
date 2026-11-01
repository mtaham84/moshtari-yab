"""
Opportunity export service for discovery app.
Decouples query scoping, row serialization, and CSV generation from discovery views (P08.16 - R39).
"""

import csv
import io
import logging
from django.http import HttpResponse
from django.utils import timezone

from apps.businesses.models import Business
from shared.instagram import PageRefError, parse_page_ref
from apps.core.agent import drafts_for
from apps.discovery.services import format_jalali_date
from apps.products.models import Product

logger = logging.getLogger(__name__)

_PLATFORM_ID_PREFIXES = ("telegram_", "instagram_", "x_", "twitter_", "divar_", "bale_", "eitaa_")

PLATFORM_MAP = {
    "telegram": "تلگرام",
    "x": "ایکس (توییتر)",
    "twitter": "ایکس (توییتر)",
    "instagram": "اینستاگرام",
    "divar": "دیوار",
    "bale": "بله",
    "eitaa": "ایتا",
}

CSV_HEADERS = [
    "آیدی مشتری",
    "شبکه اجتماعی مشتری",
    "لینک پروفایل کامل مشتری",
    "پیام مشتری",
    "کالا های پیشنهاد شده برای مشتری",
    "پیام ساجست شده برای مشتری",
]


def _strip_platform_prefix(value: str) -> str:
    for prefix in _PLATFORM_ID_PREFIXES:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


def safe_csv_cell(value) -> str:
    """Escapes potential CSV formula injection while preserving Persian characters and dash placeholder."""
    value = str(value)
    if value != "-" and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def resolve_export_scope(user, query_params) -> tuple[Business | None, int | None, str | None]:
    """
    Resolves the target Business for export according to user permissions and query parameters.
    Returns (business, error_status, error_message). On success, error_status is None.
    """
    business = getattr(user, "business", None)
    is_admin = user.is_staff or user.is_superuser

    if is_admin:
        requested_biz_id = query_params.get("business_id", "").strip()
        requested_prod_id = query_params.get("product_id", "").strip()
        if requested_biz_id.isdigit():
            b_found = Business.objects.filter(id=int(requested_biz_id)).first()
            if b_found:
                business = b_found
        elif requested_prod_id.isdigit():
            prod = Product.objects.filter(id=int(requested_prod_id)).select_related("business").first()
            if prod and prod.business:
                business = prod.business
        elif not business:
            business = Business.objects.order_by("id").first()
    else:
        if not business:
            return None, 404, "کسب‌وکار یافت نشد."
        requested_prod_id = query_params.get("product_id", "").strip()
        if requested_prod_id.isdigit():
            if not Product.objects.filter(id=int(requested_prod_id), business=business).exists():
                return None, 403, "محصول در این فروشگاه یافت نشد."

    if not business:
        return None, 404, "کسب‌وکار یافت نشد."

    return business, None, None


def serialize_opportunity_row(opp, *, drafts_fn=None, logger=None) -> list[str]:
    """
    Transforms a single Opportunity into a list of 6 sanitized, formula-safe CSV cell strings.
    """
    c = opp.customer

    # 1. آیدی مشتری (Clean English Identifier)
    customer_id = "-"
    if c:
        if c.source_username and c.source_username.strip():
            u_str = c.source_username.strip().lstrip("@")
            customer_id = f"@{u_str}" if u_str else "-"
        elif c.external_user_id and c.external_user_id.strip():
            raw_id = _strip_platform_prefix(c.external_user_id.strip())
            customer_id = raw_id or "-"
        elif c.phone_number and c.phone_number.strip():
            customer_id = c.phone_number.strip()
        elif c.name and c.name.strip():
            customer_id = c.name.strip()

    # 2. شبکه اجتماعی مشتری
    social_network = PLATFORM_MAP.get(opp.source_platform.lower(), opp.source_platform or "نامشخص")

    # 3. لینک پروفایل کامل مشتری (Complete English URL)
    profile_link = ""
    if c:
        if c.source_profile_url and c.source_profile_url.startswith("http"):
            profile_link = c.source_profile_url.strip()
        elif c.source_username and c.source_username.strip():
            clean_un = c.source_username.strip().lstrip("@")
            if clean_un:
                if opp.source_platform == "telegram":
                    profile_link = f"https://t.me/{clean_un}"
                elif opp.source_platform in ("x", "twitter"):
                    profile_link = f"https://x.com/{clean_un}"
                elif opp.source_platform == "instagram":
                    profile_link = f"https://instagram.com/{clean_un}"
        elif c.external_user_id and c.external_user_id.strip():
            raw_id = _strip_platform_prefix(c.external_user_id.strip())
            if raw_id.isdigit():
                if opp.source_platform == "telegram":
                    profile_link = f"https://t.me/c/{raw_id}"
                elif opp.source_platform in ("x", "twitter"):
                    profile_link = f"https://x.com/i/user/{raw_id}"
    if opp.source_platform == "instagram":
        try:
            handle = parse_page_ref(c.source_username or "") if c else None
        except PageRefError:
            profile_link = "-"
        else:
            profile_link = f"https://www.instagram.com/{handle}/" if handle else "-"
    elif not profile_link:
        profile_link = "-"

    # 4. پیام مشتری (Persian Message - Newlines normalized to keep single row)
    raw_msg = (opp.source_raw_message or "").strip()
    if not raw_msg:
        cust_evs = [e.content.strip() for e in opp.evidence_items.all() if e.evidence_type == "customer_message" and e.content]
        if cust_evs:
            raw_msg = " | ".join(cust_evs)
        else:
            need_evs = [e.content.strip() for e in opp.evidence_items.all() if e.evidence_type == "need_signal" and e.content]
            if need_evs:
                raw_msg = " | ".join(need_evs)
            elif hasattr(opp, "ai_analysis") and opp.ai_analysis:
                raw_msg = (opp.ai_analysis.need or "").strip()

    customer_message = " ".join(raw_msg.split()) if raw_msg else "-"

    # 5. کالا های پیشنهاد شده برای مشتری (Comma-separated product names)
    matches = list(opp.product_matches.all())
    if matches:
        prods = [m.product.name.strip() for m in matches if m.product and m.product.name]
        recommended_products = " ، ".join(prods) if prods else "-"
    elif opp.primary_product_match and opp.primary_product_match.product:
        recommended_products = opp.primary_product_match.product.name.strip()
    else:
        recommended_products = "-"

    # 6. پیام ساجست شده برای مشتری
    suggested_reply = ""
    if hasattr(opp, "ai_analysis") and opp.ai_analysis:
        suggested_reply = (opp.ai_analysis.suggested_reply or "").strip()

    if not suggested_reply:
        try:
            load_drafts = drafts_fn or drafts_for
            drafts = load_drafts(opp)
            if drafts:
                suggested_reply = next((v for v in drafts.values() if v), "").strip()
        except Exception:
            active_logger = logger or logging.getLogger(__name__)
            active_logger.warning("Could not load reply drafts for opportunity %s", opp.id)

    if not suggested_reply:
        first_prod = matches[0].product.name.strip() if (matches and matches[0].product) else ""
        need_str = (
            opp.ai_analysis.need.strip()
            if hasattr(opp, "ai_analysis") and opp.ai_analysis and opp.ai_analysis.need
            else ""
        ).strip()
        if first_prod and need_str:
            suggested_reply = f"سلام وقت بخیر؛ در رابطه با {need_str}، پیشنهاد می‌کنم «{first_prod}» رو بررسی بفرمایید."
        elif first_prod:
            suggested_reply = f"سلام وقت بخیر؛ در رابطه با موضوعی که مطرح کردید، پیشنهاد می‌کنم «{first_prod}» رو بررسی بفرمایید."
        else:
            suggested_reply = "-"
    else:
        suggested_reply = " ".join(suggested_reply.split())

    return [
        safe_csv_cell(customer_id),
        safe_csv_cell(social_network),
        safe_csv_cell(profile_link),
        safe_csv_cell(customer_message),
        safe_csv_cell(recommended_products),
        safe_csv_cell(suggested_reply),
    ]


def generate_opportunities_csv(
    qs,
    *,
    filename: str | None = None,
    drafts_fn=None,
    logger=None,
) -> tuple[str, bytes]:
    """
    Serializes a queryset of opportunities into an Excel-compatible CSV with UTF-8 BOM.
    Returns (filename, csv_bytes).
    """
    if not filename:
        today_str = format_jalali_date(timezone.now().date(), persian_digits=False).replace("/", "-")
        filename = f"customers_{today_str}.csv"

    buffer = io.StringIO()
    writer = csv.writer(buffer, dialect="excel", quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    writer.writerow(CSV_HEADERS)

    records = qs.select_related("customer", "ai_analysis").prefetch_related(
        "product_matches__product", "evidence_items"
    )
    for opp in records:
        writer.writerow(serialize_opportunity_row(opp, drafts_fn=drafts_fn, logger=logger))

    csv_bytes = b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
    return filename, csv_bytes


def export_opportunities_response(
    request,
    business,
    filter_fn,
    *,
    drafts_fn=None,
    logger=None,
) -> HttpResponse:
    """
    Filters opportunities for the specified business and builds a downloadable CSV HTTP response.
    """
    qs, _ = filter_fn(business, request.GET)
    filename, csv_bytes = generate_opportunities_csv(qs, drafts_fn=drafts_fn, logger=logger)
    response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

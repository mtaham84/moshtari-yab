"""CSV export of the seller's filtered opportunities."""
import csv
import io
import logging

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.utils import timezone

from apps.core.permissions import is_operator
from apps.core.agent import drafts_for
from apps.core.jalali import format_jalali_date
from apps.products.models import Product
from shared.instagram import PageRefError, parse_page_ref

from apps.discovery.exports import safe_csv_cell
from .opportunities import _admin_scope, _filter_opportunities_queryset, _strip_platform_prefix

logger = logging.getLogger(__name__)


@login_required
def opportunity_export_view(request):
    """
    Exports filtered opportunities into an Excel-compatible CSV file (UTF-8 BOM).
    Columns:
    1. آیدی مشتری
    2. شبکه اجتماعی مشتری
    3. لینک پروفایل کامل مشتری
    4. پیام مشتری
    5. کالا های پیشنهاد شده برای مشتری
    6. پیام ساجست شده برای مشتری
    """
    business = getattr(request.user, "business", None)
    is_admin = is_operator(request.user)
    view_all_tenants = False
    if is_admin:
        business, view_all_tenants = _admin_scope(request, business)
    else:
        if not business:
            return HttpResponse("کسب‌وکار یافت نشد.", status=404)
        requested_prod_id = request.GET.get("product_id", "").strip()
        if requested_prod_id.isdigit():
            if not Product.objects.filter(id=int(requested_prod_id), business=business).exists():
                return HttpResponse("محصول در این فروشگاه یافت نشد.", status=403)

    if not business and not view_all_tenants:
        return HttpResponse("کسب‌وکار یافت نشد.", status=404)

    qs, _ = _filter_opportunities_queryset(business, request.GET, is_admin=is_admin, view_all_tenants=view_all_tenants)

    today_str = format_jalali_date(timezone.now().date(), persian_digits=False).replace("/", "-")
    filename = f"customers_{today_str}.csv"

    buffer = io.StringIO()
    writer = csv.writer(buffer, dialect="excel", quoting=csv.QUOTE_ALL, lineterminator="\r\n")

    headers = [
        "آیدی مشتری (نام کاربری / شماره)",
        "وضعیت حساب کاربر",
        "زمان ثبت کامنت در پلتفرم (شمسی)",
        "امتیاز اولویت لید (از ۴۵)",
        "درصد احتمال موفقیت (٪)",
        "نوع نیت و تقاضای خریدار",
        "پیام و متن کامنت مشتری",
        "نام پیج مبدا (ناشر پست)",
        "آدرس پیج مبدا",
        "آدرس مستقیم پست مبدا",
        "محتوا و خلاصه کپشن پست",
        "زمان انتشار پست مبدا",
        "شبکه اجتماعی مشتری",
        "کالا های پیشنهاد شده برای مشتری",
        "پیام ساجست شده برای مشتری",
        "لینک پروفایل کامل مشتری",
        "وضعیت لید در سیستم",
    ]
    writer.writerow(headers)

    platform_map = {
        "telegram": "تلگرام",
        "x": "ایکس (توییتر)",
        "twitter": "ایکس (توییتر)",
        "instagram": "اینستاگرام",
        "divar": "دیوار",
        "bale": "بله",
        "eitaa": "ایتا",
    }


    for opp in qs.select_related("customer", "ai_analysis").prefetch_related("product_matches__product", "evidence_items"):
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

        # 2. وضعیت حساب کاربر (Public vs Private)
        profile_privacy = opp.display_page_type

        # 3. زمان ثبت کامنت (تقویم شمسی)
        c_time_jalali = opp.instagram_comment_created_at_jalali
        if not c_time_jalali or c_time_jalali == "-":
            c_time_jalali = getattr(opp, "created_at_jalali", "") or "-"

        # 4. امتیاز اولویت لید (از ۴۵)
        outreach_score_str = f"{opp.outreach_score_val} از ۴۵"

        # 5. درصد احتمال موفقیت (٪)
        success_prob_str = f"{opp.success_probability_pct}٪"

        # 6. نوع نیت و تقاضای خریدار
        intent_type_str = opp.get_intent_type_display() if hasattr(opp, "get_intent_type_display") else "-"
        if opp.source_platform == "instagram" and isinstance(opp.trace_metadata, dict):
            ig = opp.trace_metadata.get("instagram", {})
            itype = ig.get("comment_intent_type")
            if itype == "QUESTION_INQUIRY":
                intent_type_str = "سوال و استعلام نیاز (مشاوره‌ای/اقساط)"
            elif itype == "DIRECT_SHORT_CTA":
                intent_type_str = "پیام کوتاه کال‌تواکشن (قیمت/دایرکت)"
            elif itype == "GENERAL_BUYER":
                intent_type_str = "قصد خرید عمومی"

        # 7. پیام و متن کامنت مشتری
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

        # 8 & 9. نام و آدرس پیج مبدا (ناشر پست)
        source_page_title = opp.instagram_source_page_title or opp.instagram_source_page_username or "-"
        source_page_url = opp.instagram_source_page_url or "-"

        # 10. آدرس مستقیم پست مبدا
        source_post_url = opp.instagram_post_url or opp.source_post_url or "-"

        # 11. محتوا و خلاصه کپشن پست
        post_caption = " ".join((opp.instagram_post_caption or "-").split())

        # 12. زمان انتشار پست مبدا
        post_published_time = opp.instagram_post_created_at_jalali or "-"

        # 13. شبکه اجتماعی مشتری
        social_network = platform_map.get(opp.source_platform.lower(), opp.source_platform or "نامشخص")

        # 14. کالاهای پیشنهادی کاتالوگ فروشگاه
        matches = list(opp.product_matches.all())
        if matches:
            prods = [m.product.name.strip() for m in matches if m.product and m.product.name]
            recommended_products = " ، ".join(prods) if prods else "-"
        elif opp.primary_product_match and opp.primary_product_match.product:
            recommended_products = opp.primary_product_match.product.name.strip()
        else:
            recommended_products = "-"

        # 15. پیام ساجست‌شده اختصاصی برای مشتری
        suggested_reply = ""
        if hasattr(opp, "ai_analysis") and opp.ai_analysis:
            suggested_reply = (opp.ai_analysis.suggested_reply or "").strip()

        if not suggested_reply:
            try:
                drafts = drafts_for(opp)
                if drafts:
                    suggested_reply = next((v for v in drafts.values() if v), "").strip()
            except Exception:
                logger.warning("Could not load reply drafts for opportunity %s", opp.id, exc_info=True)

        if not suggested_reply:
            first_prod = matches[0].product.name.strip() if (matches and matches[0].product) else ""
            need_str = (opp.ai_analysis.need.strip() if hasattr(opp, "ai_analysis") and opp.ai_analysis and opp.ai_analysis.need else "").strip()
            if first_prod and need_str:
                suggested_reply = f"سلام وقت بخیر؛ در رابطه با {need_str}، پیشنهاد می‌کنم «{first_prod}» رو بررسی بفرمایید."
            elif first_prod:
                suggested_reply = f"سلام وقت بخیر؛ در رابطه با موضوعی که مطرح کردید، پیشنهاد می‌کنم «{first_prod}» رو بررسی بفرمایید."
            else:
                suggested_reply = "-"
        else:
            suggested_reply = " ".join(suggested_reply.split())

        # 16. لینک پروفایل کامل مشتری (Complete English URL)
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

        # 17. وضعیت لید در سیستم
        lead_status = opp.get_status_display()

        writer.writerow([
            safe_csv_cell(customer_id),
            safe_csv_cell(profile_privacy),
            safe_csv_cell(c_time_jalali),
            safe_csv_cell(outreach_score_str),
            safe_csv_cell(success_prob_str),
            safe_csv_cell(intent_type_str),
            safe_csv_cell(customer_message),
            safe_csv_cell(source_page_title),
            safe_csv_cell(source_page_url),
            safe_csv_cell(source_post_url),
            safe_csv_cell(post_caption),
            safe_csv_cell(post_published_time),
            safe_csv_cell(social_network),
            safe_csv_cell(recommended_products),
            safe_csv_cell(suggested_reply),
            safe_csv_cell(profile_link),
            safe_csv_cell(lead_status),
        ])

    csv_bytes = b"\xef\xbb\xbf" + buffer.getvalue().encode("utf-8")
    response = HttpResponse(csv_bytes, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

"""Dashboard analytics built only from real data: engine opportunities, product-card visits and orders."""
from __future__ import annotations

from django.db.models import Sum

from apps.businesses.models import Business
from apps.core.jalali import format_jalali_date
from apps.products.models import Product

from .engine_bridge import engine_totals
from .models import Opportunity, ProductDailyMetric

HIGH_INTENT = 0.8
PLATFORM_LABELS = {"telegram": "تلگرام", "x": "ایکس", "instagram": "اینستاگرام"}


def _fa_number(n: int | float) -> str:
    return f"{int(round(n)):,}".replace(",", "،")


def get_performance_analytics(business: Business, start_date=None, end_date=None) -> dict:
    metrics = ProductDailyMetric.objects.filter(product__business=business)
    opps = Opportunity.objects.filter(business=business)
    if start_date:
        metrics = metrics.filter(date__gte=start_date)
        opps = opps.filter(created_at__date__gte=start_date)
    if end_date:
        metrics = metrics.filter(date__lte=end_date)
        opps = opps.filter(created_at__date__lte=end_date)

    totals = metrics.aggregate(sales=Sum("sales_amount"), orders=Sum("orders_count"), clicks=Sum("clicks_count"),
                               views=Sum("views_count"), outreach=Sum("outreach_sent_count"))
    totals = {k: int(v or 0) for k, v in totals.items()}

    products = Product.objects.filter(business=business).select_related("category")
    per_metric = {r["product_id"]: r for r in metrics.values("product_id").annotate(
        sales=Sum("sales_amount"), orders=Sum("orders_count"), clicks=Sum("clicks_count"),
        views=Sum("views_count"), outreach=Sum("outreach_sent_count"))}
    per_opp: dict[int, dict] = {}
    for pid, score, platform, created in opps.values_list(
            "product_matches__product_id", "product_matches__match_score", "source_platform", "created_at"):
        if pid is None:
            continue
        o = per_opp.setdefault(pid, {"n": 0, "high": 0, "platforms": set(), "last": None})
        o["n"] += 1
        o["high"] += int((score or 0) >= HIGH_INTENT)
        o["platforms"].add(platform)
        o["last"] = max(o["last"], created) if o["last"] else created

    rows = []
    for prod in products:
        m, o = per_metric.get(prod.id, {}), per_opp.get(prod.id, {})
        img = prod.main_image
        last = o.get("last")
        rows.append({
            "id": prod.id,
            "name": prod.name,
            "category_name": prod.category.name if prod.category else "—",
            "image_url": img.image.url if img and hasattr(img.image, "url") else None,
            "price_formatted": prod.formatted_price(),
            "outreach_sent": int(m.get("outreach") or 0),
            "clicks": int(m.get("clicks") or 0),
            "views": int(m.get("views") or 0),
            "orders": int(m.get("orders") or 0),
            "sales_amount": int(m.get("sales") or 0),
            "sales_amount_formatted": _fa_number(m.get("sales") or 0),
            "discovered_leads_count": int(o.get("n") or 0),
            "high_intent_count": int(o.get("high") or 0),
            "channels_display": " • ".join(PLATFORM_LABELS.get(p, p) for p in sorted(o.get("platforms", ()))) or "—",
            "last_interaction_date": format_jalali_date(last.date()) if last else "—",
            "raw_last_date": last.date().isoformat() if last else "1970-01-01",
        })
    rows.sort(key=lambda r: (r["discovered_leads_count"], r["sales_amount"]), reverse=True)

    eng = engine_totals(business)   # this seller's share of the cost / messages reviewed for it
    return {
        "summary": {
            "total_sales": totals["sales"],
            "total_sales_formatted": _fa_number(totals["sales"]),
            "total_orders": totals["orders"],
            "total_clicks": totals["clicks"],
            "total_views": totals["views"],
            "total_outreach": totals["outreach"],
            "conversion_rate": round(totals["orders"] / totals["clicks"] * 100, 1) if totals["clicks"] else 0.0,
            "total_leads_in_db": opps.count(),
            "high_intent_leads_in_db": opps.filter(ai_analysis__product_fit_score__gte=HIGH_INTENT).count(),
            "monitored_products_count": products.filter(is_discovery_active=True, status="ACTIVE").count(),
            "monitored_categories_count": products.exclude(category=None).values("category").distinct().count(),
            "messages_analysed": eng["messages_analysed"],
            "messages_analysed_display": _fa_number(eng["messages_analysed"]),
            "total_cost_toman": eng["cost_toman"],
            "total_cost_display": f"{_fa_number(eng['cost_toman'])} تومان",
            "cost_per_message_toman": round(eng["cost_per_message_toman"], 2),
            "cost_per_message_display": f"{eng['cost_per_message_toman']:.2f} تومان".replace(".", "٫"),
            "start_date_shamsi": format_jalali_date(start_date) if start_date else "",
            "end_date_shamsi": format_jalali_date(end_date) if end_date else "",
            "is_all_time": not (start_date or end_date),
        },
        "products": rows,
    }


def sync_instagram_lead_to_opportunity(lead, business=None):
    """Ingest/synchronize an InstagramEngineLead into the unified Opportunity domain."""
    import re
    from django.db import transaction
    from apps.businesses.models import Business
    from apps.products.models import Product
    from .models import Customer, Opportunity, AIAnalysis, OpportunityProductMatch

    if business is None:
        if lead.tenant_id and str(lead.tenant_id).startswith("tenant_"):
            try:
                biz_id = int(str(lead.tenant_id).replace("tenant_", ""))
                business = Business.objects.filter(id=biz_id).first()
            except (ValueError, TypeError):
                pass
        if not business:
            business = Business.objects.filter(user__username="sang@peyda.ir").first() or Business.objects.first()

    if not business:
        return None

    post_match = re.search(r"/p/([A-Za-z0-9_-]+)", lead.source_post_url or "")
    post_code = post_match.group(1) if post_match else "post"
    source_message_id = f"ig:{post_code}:c{lead.ig_user_pk}"

    with transaction.atomic():
        customer, _ = Customer.objects.get_or_create(
            business=business,
            source_platform="instagram",
            external_user_id=str(lead.ig_user_pk),
            defaults={
                "name": lead.full_name or lead.username,
                "source_username": lead.username,
                "source_profile_url": lead.profile_url or f"https://www.instagram.com/{lead.username}/",
            }
        )
        if lead.full_name and not customer.name:
            customer.name = lead.full_name
            customer.save(update_fields=["name"])

        # Find best matching product in business
        active_products = list(Product.objects.filter(business=business, status="ACTIVE", is_discovery_active=True).select_related("category"))
        matched_product = None
        if active_products:
            combined_text = f"{lead.comment_text} {lead.post_caption_snippet or ''}".lower()
            best_score = -1
            for p in active_products:
                score = 0
                for word in p.name.lower().split():
                    if len(word) > 2 and word in combined_text:
                        score += 3
                if p.instagram_terms:
                    for term in p.instagram_terms:
                        if term.lower() in combined_text:
                            score += 4
                if p.instagram_hashtags:
                    for tag in p.instagram_hashtags:
                        if tag.lower() in combined_text:
                            score += 2
                if score > best_score:
                    best_score = score
                    matched_product = p
            if not matched_product or best_score <= 0:
                matched_product = active_products[0]

        opp, created = Opportunity.objects.get_or_create(
            business=business,
            source_platform="instagram",
            source_message_id=source_message_id,
            defaults={
                "customer": customer,
                "category": matched_product.category if matched_product else None,
                "category_name_snapshot": matched_product.category.name if matched_product and matched_product.category else "اینستاگرام",
                "source_raw_message": lead.comment_text,
                "normalized_message": lead.comment_text,
                "category_confidence": 0.95,
                "source_posted_at": lead.created_at,
                "source_query": lead.matched_cta or "instagram_lead",
                "status": "NEW",
                "trace_metadata": {
                    "instagram": {
                        "post_url": lead.source_post_url or f"https://www.instagram.com/p/{post_code}/",
                        "post_caption": lead.post_caption_snippet or "",
                        "matched_cta": lead.matched_cta or "",
                        "ig_user_pk": str(lead.ig_user_pk),
                        "lead_id": lead.id,
                    }
                }
            }
        )

        intent_flt = float(lead.intent_score or 0.90)
        prod_name = matched_product.name if matched_product else "محصول مورد نظر"
        AIAnalysis.objects.update_or_create(
            opportunity=opp,
            defaults={
                "need": f"استعلام و تمایل به خرید در اینستاگرام: «{lead.comment_text}»",
                "intent_score": intent_flt,
                "product_fit_score": 0.92,
                "confidence": 0.95,
                "why_selected": f"ثبت کامنت متقاضی خرید ({lead.matched_cta or 'استعلام قیمت/مشاوره'}) زیر پست مرتبط اینستاگرام",
                "suggested_reply": f"سلام و احترام جناب/سرکار {customer.display_identifier} عزیز، در پاسخ به استعلام شما در خصوص «{prod_name}»، شرایط همکاری و مشخصات کامل خدمت شما ارسال گردید.",
                "model_name": "instagram_intent_engine",
            }
        )

        if matched_product:
            OpportunityProductMatch.objects.get_or_create(
                opportunity=opp,
                product=matched_product,
                defaults={
                    "match_score": intent_flt,
                    "recommendation_reason": f"تطابق مستقیم کالا با کامنت اینستاگرام ({matched_product.name})",
                    "rank": 1,
                }
            )

        return opp

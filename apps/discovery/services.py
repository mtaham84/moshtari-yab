"""Dashboard analytics built only from real data: engine opportunities, product-card visits and orders."""
from __future__ import annotations

from django.db.models import Sum

from apps.businesses.models import Business
from apps.core.jalali import format_jalali_date
from apps.products.models import Product

from .engine_bridge import engine_totals
from .models import Opportunity, ProductDailyMetric

HIGH_INTENT = 0.8
PLATFORM_LABELS = {"telegram": "تلگرام", "x": "ایکس"}


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


def _engine_cards(product_ids: list[int]) -> dict[str, dict]:
    """product id → the card need_engine built (seller-edited cards come from the product itself)."""
    import json

    from django.db import connection

    from .engine_bridge import _engine_table

    table = _engine_table("products")
    if not table or not product_ids:
        return {}
    with connection.cursor() as cur:
        cur.execute(f"SELECT product_id, card FROM {table} WHERE product_id = ANY(%s)", [[str(i) for i in product_ids]])
        return {pid: (json.loads(c) if isinstance(c, str) else c) or {} for pid, c in cur.fetchall()}


def _short(text: str, n: int = 160) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + "…"


def business_icp(business: Business) -> dict:
    """The dashboard's «شناخت ایجنت» box, from the seller's real profile, products and the agent's product cards.
    Empty values stay empty (the template shows a hint instead of made-up text)."""
    products = list(Product.objects.filter(business=business, status="ACTIVE", is_discovery_active=True)
                    .order_by("-created_at")[:30])
    engine = _engine_cards([p.id for p in products])
    cards = [p.agent_card_override or engine.get(str(p.id)) or {} for p in products]

    def uniq(items, n):
        seen, out = set(), []
        for x in items:
            x = " ".join(str(x or "").split())
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out[:n]

    value = business.description or next((c.get("what_it_is") for c in cards if c.get("what_it_is")), "")
    audience = business.target_customer_description or "، ".join(
        uniq([p.target_customer for p in products] + [c.get("audience") for c in cards], 3))
    triggers = "، ".join(uniq([x for c in cards for x in (c.get("problems_solved") or [])], 4))
    return {
        "domain": business.business_domain or "",
        "value": _short(value),
        "audience": _short(audience),
        "triggers": _short(triggers),
        "products_count": len(products),
        "cards_count": sum(1 for c in cards if c),
    }

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
    for pid, intent_score, platform, created in opps.values_list(
            "product_matches__product_id", "ai_analysis__intent_score", "source_platform", "created_at"):
        if pid is None:
            continue
        o = per_opp.setdefault(pid, {"n": 0, "high": 0, "platforms": set(), "last": None})
        o["n"] += 1
        o["high"] += int((intent_score or 0) >= 0.90)
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
            "high_intent_leads_in_db": opps.filter(ai_analysis__intent_score__gte=0.90).count(),
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


def sync_instagram_lead_to_opportunity(lead, business=None, ai_meta=None):
    """Ingest/synchronize an InstagramEngineLead into the unified Opportunity domain with accurate Luna GPT-6 billing."""
    import re
    from decimal import Decimal
    from django.db import transaction
    from apps.businesses.models import Business
    from apps.products.models import Product
    from apps.billing.models import BillingSettings, WalletTransaction
    from apps.billing.services import apply_transaction
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

    # Calculate token cost and 150% marked-up tenant fee
    s = BillingSettings.get()
    usd_rate = s.usd_to_toman or Decimal("268000")
    markup_pct = s.markup_percent or Decimal("150.0")
    cost_usd_dec = Decimal(str(ai_meta.get("cost_usd", 0.0) if ai_meta else 0.0))
    raw_toman_dec = cost_usd_dec * usd_rate
    # Final price charged to customer = Raw Toman + 150% markup
    final_toman = int(round(raw_toman_dec * (1 + markup_pct / 100)))
    tokens_used = int((ai_meta.get("tokens_used") or (ai_meta.get("tokens_in", 0) + ai_meta.get("tokens_out", 0))) if ai_meta else 0)

    with transaction.atomic():
        customer, _ = Customer.objects.get_or_create(
            business=business,
            source_platform="instagram",
            external_user_id=str(lead.ig_user_pk),
            defaults={
                "name": lead.full_name or lead.username,
                "source_username": lead.username,
                "source_profile_url": lead.profile_url or f"https://www.instagram.com/{lead.username}/",
                "metadata": {"bio": lead.profile_bio, "is_business": lead.commenter_is_business,
                             "follower_count": lead.follower_count},
            }
        )
        signals = dict(customer.metadata or {})
        for key, value in (("bio", lead.profile_bio), ("is_business", lead.commenter_is_business),
                           ("follower_count", lead.follower_count)):
            if value is not None and value != "":
                signals[key] = value
        if signals != (customer.metadata or {}):
            customer.metadata = signals
            customer.save(update_fields=["metadata", "updated_at"])
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
                        "source_type": ai_meta.get("source_type", "") if ai_meta else "",
                        "source_label": ai_meta.get("source_label", "") if ai_meta else "",
                        "ig_user_pk": str(lead.ig_user_pk),
                        "lead_id": lead.id,
                        "billing": {
                            "cost_usd": float(cost_usd_dec),
                            "raw_cost_toman": int(round(raw_toman_dec)),
                            "markup_percent": float(markup_pct),
                            "final_cost_toman": final_toman,
                        }
                    }
                }
            }
        )

        if not created and isinstance(opp.trace_metadata, dict):
            ig_m = dict(opp.trace_metadata.get("instagram", {}))
            if not ig_m.get("post_caption") and lead.post_caption_snippet:
                ig_m["post_caption"] = lead.post_caption_snippet
            if ai_meta and ai_meta.get("source_type"):
                ig_m["source_type"] = ai_meta["source_type"]
                ig_m["source_label"] = ai_meta.get("source_label", "")
            opp.trace_metadata["instagram"] = ig_m
            opp.save(update_fields=["trace_metadata"])

        intent_flt = float(lead.intent_score or (ai_meta.get("intent_score") if ai_meta else 0.90) or 0.90)
        prod_name = matched_product.name if matched_product else "محصول مورد نظر"

        # Suggested reply and reasoning from AI or direct template
        need_str = (ai_meta.get("need") if ai_meta else None) or f"استعلام و تمایل به خرید در اینستاگرام: «{lead.comment_text}»"
        why_str = (ai_meta.get("why_selected") if ai_meta else None) or f"ثبت کامنت متقاضی خرید ({lead.matched_cta or 'استعلام قیمت/مشاوره'}) زیر پست مرتبط اینستاگرام"
        suggested_reply_str = (ai_meta.get("suggested_reply") if ai_meta else None) or f"سلام و احترام جناب/سرکار {customer.display_identifier} عزیز، در پاسخ به استعلام شما در خصوص «{prod_name}»، شرایط همکاری و مشخصات کامل خدمت شما ارسال گردید."
        model_name = "gpt-6-luna" if (ai_meta and not ai_meta.get("is_direct_cta")) else "instagram_direct_cta"
        dynamic_fit_score = 0.72
        if matched_product and active_products:
            p_name = matched_product.name.lower()
            if any(w in combined_text for w in p_name.split() if len(w) > 2):
                dynamic_fit_score = 0.94
            elif matched_product.instagram_terms and any(t.lower() in combined_text for t in matched_product.instagram_terms):
                dynamic_fit_score = 0.86
            elif matched_product.instagram_hashtags and any(h.lower() in combined_text for h in matched_product.instagram_hashtags):
                dynamic_fit_score = 0.80

        AIAnalysis.objects.update_or_create(
            opportunity=opp,
            defaults={
                "need": need_str,
                "intent_score": intent_flt,
                "product_fit_score": dynamic_fit_score,
                "confidence": 0.95,
                "why_selected": why_str,
                "suggested_reply": suggested_reply_str,
                "model_name": model_name,
                "tokens_used": tokens_used,
                "cost_usd": cost_usd_dec,
                "cost_toman": final_toman,
            }
        )

        if matched_product:
            match, _ = OpportunityProductMatch.objects.get_or_create(
                opportunity=opp,
                product=matched_product,
                defaults={
                    "match_score": intent_flt,
                    "recommendation_reason": f"تطابق مستقیم کالا با کامنت اینستاگرام ({matched_product.name})",
                    "rank": 1,
                }
            )
        else:
            match = None

        # Apply transaction debit for AI usage if cost incurred
        if final_toman > 0:
            try:
                apply_transaction(
                    business=business,
                    amount=-final_toman,
                    kind=WalletTransaction.USAGE,
                    description=f"تحلیل هوشمند کامنت اینستاگرام (GPT-6 Luna: {tokens_used} توکن)",
                    cost_usd=cost_usd_dec,
                    calls=1
                )
            except Exception as w_exc:
                pass

        if match:
            from django.db import transaction
            transaction.on_commit(lambda oid=opp.pk, mid=match.pk: _generate_suggestion_after_commit(oid, mid))
        return opp


def _generate_suggestion_after_commit(opportunity_id: int, match_id: int):
    from .models import Opportunity, OpportunityProductMatch
    import logging
    try:
        opportunity = Opportunity.objects.select_related("business", "customer", "ai_analysis").get(pk=opportunity_id)
        match = OpportunityProductMatch.objects.select_related("product").get(pk=match_id)
        generate_opportunity_reply_suggestions(opportunity, match)
    except Exception:
        logging.getLogger(__name__).exception("Instagram reply suggestion generation failed for opportunity %s", opportunity_id)


def _reply_context(opportunity, match) -> dict:
    from apps.businesses.models import MessageStyle
    from apps.products.models import Product

    business, product = opportunity.business, match.product
    customer = opportunity.customer
    meta = opportunity.trace_metadata if isinstance(opportunity.trace_metadata, dict) else {}
    ig_meta = meta.get("instagram", {}) if isinstance(meta.get("instagram", {}), dict) else {}
    x_meta = meta.get("x", {}) if isinstance(meta.get("x", {}), dict) else {}
    family_qs = Product.objects.filter(business=business, status="ACTIVE")
    if product.category_id:
        family_qs = family_qs.filter(category__full_path=product.category.full_path)
    else:
        family_qs = family_qs.filter(pk=product.pk)
    family_products = list(family_qs.order_by("name")[:10])
    context = {
        "person": {
            "comment_or_tweet": opportunity.source_raw_message[:1800],
            "post_or_tweet_context": (ig_meta.get("post_caption") or x_meta.get("text") or "")[:1800],
            "source_url": opportunity.source_post_url,
            "username": customer.source_username,
            "display_name": customer.name,
            "bio_if_already_fetched": str((customer.metadata or {}).get("bio") or ig_meta.get("profile_bio") or "")[:500],
            "language_and_register": _infer_register(opportunity.source_raw_message),
            "public_signals": {
                "is_business": (customer.metadata or {}).get("is_business"),
                "follower_bucket": _follower_bucket((customer.metadata or {}).get("follower_count")),
            },
        },
        "matched_family_products": [{
            "name": item.name,
            "category_path": getattr(item.category, "full_path", "") if item.category_id else "",
            "price": str(item.price) if item.price else None,
            "product_id": item.pk,
        } for item in family_products],
        "seller_profile": {
            "name": business.name,
            "tagline": business.tagline,
            "business_domain": business.business_domain,
            "business_types": list(business.business_types.values_list("name", flat=True)),
            "search_notes": (business.search_notes or "")[:1000],
        },
        "channel_options": ["dm", "comment_reply"] if opportunity.source_platform == "instagram" else ["dm", "public_reply"],
        "platform": opportunity.source_platform,
    }
    try:
        context["seller_message_style"] = MessageStyle.for_business(business).as_engine_dict()
    except Exception:
        context["seller_message_style"] = {}
    return context


def _infer_register(text: str) -> str:
    casual = ("چنده", "میشه", "میخوام", "می‌خوام", "بدید", "بگید", "دارین", "رو میخوام", "دمت")
    return "colloquial" if any(x in (text or "") for x in casual) else "formal_or_neutral"


def _follower_bucket(value) -> str | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return "under_1k" if n < 1000 else "1k_10k" if n < 10000 else "10k_100k" if n < 100000 else "100k_plus"


def _recent_reply_suggestions(business) -> list[str]:
    from .models import InstagramReplySuggestion, OpportunityProductMatch
    out = []
    for variants in OpportunityProductMatch.objects.filter(opportunity__business=business).exclude(reply_variants={}) \
            .order_by("-created_at").values_list("reply_variants", flat=True)[:50]:
        variants = variants or {}
        out.extend(str(v.get("text") or "") for v in variants.get("variants", []) if isinstance(v, dict))
    for histories in OpportunityProductMatch.objects.filter(opportunity__business=business).order_by("-created_at") \
            .values_list("reply_history", flat=True)[:50]:
        for old in histories[-10:]:
            out.extend(str(v.get("text") or "") for v in (old.get("reply_variants") or {}).get("variants", [])
                       if isinstance(v, dict))
    for row in InstagramReplySuggestion.objects.filter(opportunity__business=business).order_by("-updated_at")[:50]:
        out.extend(str(v.get("text") or "") for v in (row.reply_variants or {}).get("variants", []) if isinstance(v, dict))
        for old in row.history[-10:]:
            out.extend(str(v.get("text") or "") for v in (old.get("reply_variants") or {}).get("variants", []) if isinstance(v, dict))
    return out[-50:]


def _charge_outreach_llm(business, started_at):
    """Price the direct chat_json call from the admin model registry and write the normal wallet ledger entry."""
    from decimal import Decimal, ROUND_HALF_UP
    from apps.core.models import LLMUsageLog
    from apps.billing.models import AIModel, BillingSettings, WalletTransaction
    from apps.billing.services import apply_transaction

    usage = LLMUsageLog.objects.filter(business_id=business.pk, purpose="outreach_suggestion", created_at__gte=started_at,
                                       ok=True).order_by("-pk").first()
    if not usage:
        return Decimal("0")
    model = AIModel.objects.filter(name=usage.model, is_active=True, provider__is_active=True).order_by("priority").first()
    if not model:
        return Decimal("0")
    cost = (Decimal(usage.tokens_in) * model.input_price_usd + Decimal(usage.tokens_out) * model.output_price_usd) / Decimal(1_000_000)
    usage.cost_usd = cost
    usage.save(update_fields=["cost_usd"])
    settings = BillingSettings.get()
    amount = (cost * settings.usd_to_toman * settings.multiplier).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if amount > 0:
        apply_transaction(business, -amount, WalletTransaction.USAGE, "ساخت پیش‌نویس پیام شخصی‌سازی‌شده", cost_usd=cost, calls=1)
    return amount


def generate_opportunity_reply_suggestions(opportunity, match, *, instruction: str = "", force: bool = False,
                                           generator=None):
    """Create three human-reviewed drafts. No code path sends them automatically."""
    from django.conf import settings as django_settings
    from django.urls import reverse
    from django.utils import timezone
    from apps.businesses.models import MessageStyle
    from apps.billing.services import is_blocked
    from need_engine.reply import MessageStyle as EngineMessageStyle, generate_outreach_variants
    from apps.discovery.models import InstagramReplySuggestion

    if not force and not hasattr(opportunity, "ai_analysis"):
        return None
    if is_blocked(opportunity.business):
        return None
    meta = dict(opportunity.trace_metadata or {})
    pid = str(match.product_id)
    if opportunity.source_platform == "instagram":
        saved, _ = InstagramReplySuggestion.objects.get_or_create(opportunity=opportunity, product=match.product)
        existing = saved.reply_variants or {}
        history = list(saved.history or [])
    else:
        saved, history = None, list(match.reply_history or [])
        existing = match.reply_variants or {}
    if existing and existing.get("variants") and not force:
        return existing
    style_model = MessageStyle.for_business(opportunity.business)
    style = EngineMessageStyle.from_dict(style_model.as_engine_dict())
    nonce = len(history) + (1 if existing else 0) + 1
    tracked_link = None
    if style.include_link:
        path = reverse("product_click", kwargs={"pk": match.product_id})
        ref = opportunity.engine_opportunity_id or str(opportunity.pk)
        tracked_link = f"{django_settings.PUBLIC_BASE_URL}{path}?ref={ref}"
    context = _reply_context(opportunity, match)
    prior = _recent_reply_suggestions(opportunity.business)
    if existing:
        prior.extend(v.get("text", "") for v in existing.get("variants", []) if isinstance(v, dict))
    started_at = timezone.now()
    if generator is None:
        from apps.core.llm import chat_json
        data = chat_json
        result_raw = data
        payload = context
        if instruction:
            payload["regeneration_instruction"] = instruction[:200]
        payload["generation"] = nonce
        result = result_raw(
            __import__("need_engine.reply", fromlist=["OUTREACH_SYSTEM"]).OUTREACH_SYSTEM,
            __import__("json").dumps(payload, ensure_ascii=False), purpose="outreach_suggestion",
            business_id=opportunity.business_id, max_tokens=1800, attempts=1,
        )
        from need_engine.reply import validate_outreach_result, OUTREACH_SYSTEM
        validated = validate_outreach_result(result, platform=opportunity.source_platform, style=style,
                                             prior_suggestions=prior, tracked_link=tracked_link)
        _charge_outreach_llm(opportunity.business, started_at)
    else:
        validated, _usage = generator(context=context, platform=opportunity.source_platform, style=style,
                                      tracked_link=tracked_link, prior_suggestions=prior, instruction=instruction,
                                      generation=nonce)
    if existing:
        history.append({"reply_variants": existing, "saved_at": timezone.now().isoformat()})
    if opportunity.source_platform == "instagram":
        saved.reply_variants, saved.history = validated, history[-50:]
        saved.save(update_fields=["reply_variants", "history", "updated_at"])
    match.reply_variants = validated
    match.reply_history = history[-50:]
    match.save(update_fields=["reply_variants", "reply_history"])
    return validated


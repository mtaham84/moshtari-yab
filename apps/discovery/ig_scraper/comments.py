"""Reading a post's comments once, judging them for one tenant's product family, and saving the leads."""
from datetime import timezone

from django.utils import timezone as dj_timezone

from apps.discovery.ig_scraper import accounts


def reply_template_for_post(run, post_hash: str, caption: str, cta_word: str, family_context: dict, business_id: int) -> str:
    """
    Generate a personalized DM reply template based on the post's caption and inquiry context.
    Caches it per post hash so 100 people asking 'قیمت' on the same post only incur 1 LLM call,
    and every subsequent lead is generated for free by substituting their username!
    """
    import json, hashlib
    from django.core.cache import cache
    key = f"ig_tpl:{post_hash[:16]}:{hashlib.md5((cta_word or 'general').encode()).hexdigest()[:8]}"
    cached_tpl = cache.get(key)
    if cached_tpl:
        return cached_tpl

    from apps.core.llm import chat_json
    from apps.core.prompts import get_system_prompt
    default_prompt_system = (
        "شما متخصص فروش حرفه‌ای در دایرکت اینستاگرام هستید. "
        "با توجه به کپشن پست زیر و مشخصات فروشگاه، یک پیام دایرکت کوتاه، صمیمی، محترمانه و ترغیب‌کننده "
        "برای پاسخ به کامنت استعلام قیمت/خرید بنویسید.\n"
        "الزامات مهم:\n"
        "۱. حتماً در ابتدای پیام از برچسب {username} برای نام کاربر استفاده کنید (مثال: سلام {username} عزیز، وقتتون بخیر).\n"
        "۲. متن باید متناسب با کالای معرفی‌شده در کپشن باشد و حس ارسال پیام اختصاصی به کاربر را بدهد.\n"
        "۳. لینک مستقیم، آیدی کانال تلگرام یا شماره تماس قرار ندهید.\n"
        "۴. خروجی را در قالب JSON برگردانید: {\"template\": \"متن پیام...\"}"
    )
    prompt_system = get_system_prompt("ig_caption_reply_template", default_prompt_system)
    payload = {
        "post_caption": (caption or "")[:800],
        "inquiry_type": cta_word or "استعلام قیمت و موجودی",
        "category": family_context.get("category_path", ""),
        "product_titles": family_context.get("product_titles", [])[:4],
        "seller_notes": (family_context.get("seller_notes") or "")[:250],
    }
    try:
        res = chat_json(prompt_system, json.dumps(payload, ensure_ascii=False), purpose="instagram_caption_reply_template", business_id=business_id)
        if isinstance(res, dict) and res.get("template"):
            tpl = str(res["template"]).strip()
        else:
            prod = (family_context.get("product_titles") or ["کالای انتخابی"])[0]
            tpl = f"سلام {{username}} عزیز، وقتتون بخیر. در خصوص «{prod}» که در این پست استعلام فرمودید، قیمت و مشخصات خدمت شما ارسال شد."
    except Exception as exc:
        run.stderr.write(f"Notice: reply template LLM error: {exc}")
        prod = (family_context.get("product_titles") or ["کالای انتخابی"])[0]
        tpl = f"سلام {{username}} عزیز، وقتتون بخیر. در پاسخ به استعلام شما در خصوص «{prod}»، جزئیات کامل خدمت شما تقدیم می‌شود."

    cache.set(key, tpl, timeout=30 * 86400)
    return tpl


def inspect_family_medias(run, cl, account, target, source, medias, min_comments: int):
    """Cache each post/comments once globally, then score the cached comments for this tenant/family."""
    import hashlib
    from datetime import datetime, timedelta
    from apps.discovery.models import InstagramCrawledPost, InstagramEngineLead
    from instagram_engine.analyzer import (extract_caption_cta, evaluate_comment_smart,
                                            llm_evaluate_family_batch)
    from apps.discovery.instagram_families import lead_quality_score

    business, family, plan = target["business"], target["family"], target["plan"]
    post_cutoff = dj_timezone.now() - timedelta(days=30)
    comment_cutoff = dj_timezone.now() - timedelta(days=5)
    effective_min = max(1, int(min_comments))
    target_qualifying = 3
    posts_inspected = qualifying_posts = leads_saved = 0
    seen = set()
    buyer_signals, negatives = list(plan.buyer_signals or []), list(plan.negative_signals or [])
    family_context = {"category_path": family.category_path, "product_titles": family.titles[:10],
                      "seller_notes": family.notes[:1000], "business_domain": business.business_domain}

    run.stdout.write(run.style.HTTP_INFO(
        f"  [Scraping Source]: {source.source_type} -> '{source.label}' | Total Medias to inspect: {len(medias or [])}"
    ))

    for media in medias or []:
        if posts_inspected >= 30:
            break
        media_id = str(getattr(media, "id", "") or getattr(media, "pk", ""))
        if not media_id or media_id in seen:
            continue
        seen.add(media_id)
        posts_inspected += 1

        code = str(getattr(media, "code", "") or "")
        post_url = f"https://www.instagram.com/p/{code}/" if code else f"id:{media_id}"
        taken_at = getattr(media, "taken_at", None)
        if taken_at and dj_timezone.is_naive(taken_at):
            taken_at = dj_timezone.make_aware(taken_at, timezone.utc)
        comment_count = int(getattr(media, "comment_count", 0) or 0)

        # Rule 1: Post age limit <= 30 days
        if not taken_at or taken_at < post_cutoff:
            run.stdout.write(run.style.WARNING(
                f"    [{posts_inspected}] ⏭️ [رد شد - پست قدیمی]: {post_url} (تاریخ: {taken_at.strftime('%Y-%m-%d') if taken_at else 'نامشخص'} > ۳۰ روز)"
            ))
            continue

        # Rule 2: Minimum comment count >= --min-comments
        if comment_count < effective_min:
            run.stdout.write(run.style.WARNING(
                f"    [{posts_inspected}] ⏭️ [رد شد - کامنت کم]: {post_url} (کامنت: {comment_count} < {effective_min})"
            ))
            continue

        qualifying_posts += 1
        run.stdout.write(run.style.SUCCESS(
            f"    🎯 [پست واجد شرایط #{qualifying_posts}]: {post_url} | انتشار: {taken_at.strftime('%Y-%m-%d')} | کامنت: {comment_count} | خواندن کامنت‌های ۵ روز اخیر..."
        ))

        digest = hashlib.sha256(f"ig_post:{media_id}".encode()).hexdigest()
        cached = InstagramCrawledPost.objects.filter(post_hash=digest).first()
        caption = str(getattr(media, "caption_text", "") or (cached.caption if cached else ""))
        raw_comments = []

        # Re-fetch comments if not cached or if post was crawled more than 5 days ago (to catch new comments within the 5-day freshness window)
        should_fetch_live = (
            not cached
            or not cached.comments_cached
            or (cached.crawled_at and cached.crawled_at < dj_timezone.now() - timedelta(days=5))
        )

        if not should_fetch_live and cached and cached.comments_cached:
            raw_comments = list(cached.comments or [])
        else:
            try:
                from apps.discovery.anti_ban import INSTAGRAM_MAX_COMMENTS_PER_POST, InstagramAntiBanManager
                raw = accounts.instagram_api_call(run, account, cl.media_comments, media_id, amount=min(comment_count, INSTAGRAM_MAX_COMMENTS_PER_POST))
                InstagramAntiBanManager.sleep_after_comment_fetch()
            except Exception:
                try:
                    from apps.discovery.anti_ban import INSTAGRAM_MAX_COMMENTS_PER_POST, InstagramAntiBanManager
                    raw = accounts.instagram_api_call(run, account, cl.media_comments_gql, media_id, amount=min(comment_count, INSTAGRAM_MAX_COMMENTS_PER_POST))
                    InstagramAntiBanManager.sleep_after_comment_fetch()
                except Exception as exc:
                    run.stderr.write(f"Comment fetch failed for {code or media_id}: {exc}")
                    continue
            raw_comments = []
            for comment in raw or []:
                user = getattr(comment, "user", None)
                if user is None:
                    continue
                user_pk = str(getattr(user, "pk", "") or getattr(user, "id", ""))
                username = str(getattr(user, "username", "") or "")
                text = str(getattr(comment, "text", "") or "")
                if not user_pk or not username or not text:
                    continue
                c_time = getattr(comment, "created_at_utc", None) or getattr(comment, "created_at", None)
                if c_time and dj_timezone.is_naive(c_time):
                    c_time = dj_timezone.make_aware(c_time, timezone.utc)
                raw_comments.append({
                    "id": str(getattr(comment, "pk", "") or getattr(comment, "id", "") or user_pk),
                    "user_pk": user_pk, "username": username,
                    "full_name": str(getattr(user, "full_name", "") or username), "text": text[:1500],
                    "created_at": c_time.isoformat() if c_time else None,
                    "is_business": getattr(user, "is_business", None),
                    "is_private": bool(getattr(user, "is_private", False)),
                    "follower_count": getattr(user, "follower_count", None),
                    "bio": str(getattr(user, "biography", "") or "")[:500],
                })
            if cached is None:
                try:
                    cached = InstagramCrawledPost.objects.create(
                        post_hash=digest, media_id=media_id, media_code=code, account_username=account.username,
                        posted_at=taken_at or dj_timezone.now(), caption=caption,
                        comments=raw_comments, comment_count=comment_count, comments_cached=True,
                    )
                except Exception:
                    cached = InstagramCrawledPost.objects.filter(post_hash=digest).first()
            else:
                cached.caption, cached.comments, cached.comment_count, cached.comments_cached, cached.crawled_at = (
                    caption, raw_comments, comment_count, True, dj_timezone.now()
                )
                cached.save(update_fields=["caption", "comments", "comment_count", "comments_cached", "crawled_at"])

        # Rule 3: Comments age limit strictly <= 5 days
        comment_cutoff = dj_timezone.now() - timedelta(days=5)
        eligible_comments = []
        for row in raw_comments:
            c_date = row.get("created_at")
            if c_date:
                try:
                    dt = datetime.fromisoformat(c_date)
                    if dj_timezone.is_naive(dt):
                        dt = dj_timezone.make_aware(dt, timezone.utc)
                    if dt < comment_cutoff:
                        continue
                    row["_dt"] = dt
                except Exception:
                    pass
            eligible_comments.append(row)

        run.stdout.write(
            f"      💬 [بررسی کامنت‌ها]: {len(eligible_comments)} کامنت مربوط به ۵ روز اخیر (از مجموع {len(raw_comments)} کامنت)"
        )

        if not eligible_comments:
            continue

        cta = extract_caption_cta(caption)
        evaluations, ambiguous = {}, []
        for row in eligible_comments:
            eval_row = evaluate_comment_smart(row["text"], caption=caption, cta_word=cta,
                                              product_context=family.category_path or family.title,
                                              buyer_signals=buyer_signals, negative_signals=negatives)
            evaluations[row["id"]] = eval_row
            if eval_row.get("is_ambiguous"):
                ambiguous.append({"id": row["id"], "username": row["username"], "text": row["text"]})
        ai = {}
        for offset in range(0, len(ambiguous), 25):
            from apps.billing.services import is_blocked
            if is_blocked(business):
                run.stdout.write(run.style.WARNING(
                    f"Skipping Instagram LLM evaluation for tenant {business.pk}: اعتبار تمام شده"
                ))
                break
            ai.update(llm_evaluate_family_batch(ambiguous[offset:offset + 25], business_id=business.pk,
                                                family_context=family_context, buyer_signals=buyer_signals))
        evaluations.update(ai)

        # Build or retrieve the cached reply template for this post once
        post_reply_template = None
        if any(evaluations.get(r["id"], {}).get("is_lead") for r in eligible_comments):
            post_reply_template = reply_template_for_post(
                run,
                post_hash=digest,
                caption=caption,
                cta_word=cta or "استعلام قیمت و موجودی",
                family_context=family_context,
                business_id=business.pk
            )

        post_leads_saved = 0
        for row in eligible_comments:
            result = evaluations.get(row["id"], {})
            if not result.get("is_lead"):
                continue

            media_user = getattr(media, "user", None)
            src_user = str(getattr(media_user, "username", "") or (source.label.lstrip("@") if source.source_type == "page" else "") or "")
            src_title = str(getattr(media_user, "full_name", "") or src_user)

            from apps.discovery.instagram_scoring import calculate_lead_outreach_score
            c_dt = row.get("_dt")
            is_priv = bool(row.get("is_private", False))
            calc = calculate_lead_outreach_score(
                comment_text=row["text"],
                comment_time=c_dt,
                is_private=is_priv,
                cta_word=cta or "",
                source_page_username=src_user,
                post_caption=caption,
                source_post_url=post_url,
            )

            if not calc.get("is_eligible", True):
                continue

            leads_saved += 1
            post_leads_saved += 1
            result["source_type"] = source.source_type
            result["source_label"] = source.label
            display_name = str(row.get("full_name") or row.get("username") or "").strip()
            if post_reply_template and not result.get("suggested_reply"):
                result["suggested_reply"] = post_reply_template.replace("{username}", display_name or "کاربر")
            score = lead_quality_score(result.get("intent_score", .8), is_business=row.get("is_business"),
                                       follower_count=row.get("follower_count"), bio=row.get("bio", ""),
                                       seller_notes=family.notes, family_path=family.category_path,
                                       product_titles=family.titles)
            post_time = taken_at

            lead, created = InstagramEngineLead.objects.get_or_create(
                tenant_id=target["tenant_id"], ig_user_pk=row["user_pk"],
                defaults={"username": row["username"], "full_name": row["full_name"],
                          "profile_url": f"https://www.instagram.com/{row['username']}/",
                          "comment_text": row["text"], "source_post_url": post_url,
                          "post_caption_snippet": caption[:600], "matched_cta": result.get("matched_cta", ""),
                          "intent_score": score, "status": "NEW",
                          "comment_created_at": c_dt,
                          "is_private": is_priv,
                          "comment_intent_type": calc["intent_type"],
                          "outreach_score": calc["total_score"],
                          "success_probability": calc.get("success_probability", 0),
                          "source_page_username": src_user,
                          "source_page_title": src_title,
                          "post_created_at": post_time,
                          "score_breakdown": calc,
                          "profile_bio": row.get("bio", ""), "commenter_is_business": row.get("is_business"),
                          "follower_count": row.get("follower_count")},
            )

            if not created:
                lead.outreach_score = calc["total_score"]
                lead.success_probability = calc.get("success_probability", 0)
                lead.comment_intent_type = calc["intent_type"]
                lead.score_breakdown = calc
                if not lead.source_page_username and src_user:
                    lead.source_page_username = src_user
                if not lead.post_created_at and post_time:
                    lead.post_created_at = post_time
                if c_dt and (not lead.comment_created_at or c_dt > lead.comment_created_at):
                    lead.comment_text = row["text"]
                    lead.comment_created_at = c_dt
                    lead.is_private = is_priv
                lead.save(update_fields=[
                    "comment_text", "comment_created_at", "is_private",
                    "outreach_score", "success_probability", "comment_intent_type",
                    "score_breakdown", "source_page_username", "post_created_at", "updated_at"
                ])

            from apps.discovery.services import sync_instagram_lead_to_opportunity
            sync_instagram_lead_to_opportunity(lead, business=business, ai_meta=result)
        run.stdout.write(run.style.SUCCESS(
            f"      ✅ استخراج {post_leads_saved} مشتری واقعی از پست {post_url}"
        ))
        from apps.discovery.anti_ban import InstagramAntiBanManager
        InstagramAntiBanManager.sleep_after_post(run.stdout)

        if qualifying_posts >= target_qualifying and posts_inspected >= 15:
            run.stdout.write(run.style.SUCCESS(
                f"  ✨ به حد نصاب رسید: {qualifying_posts} پست واجد شرایط از میان {posts_inspected} پست بررسی‌شده."
            ))
            break

    retired = False
    if posts_inspected >= 15 and qualifying_posts < target_qualifying:
        retired = True

    return posts_inspected, qualifying_posts, leads_saved, retired

"""
Family inspection service for active Instagram scraper (P08.13 / R39).

Decouples post evaluation, comment age filtering, reply template caching,
and lead extraction from the monolithic run_instagram_scraper command.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Tuple

from django.core.cache import cache
from django.utils import timezone as dj_timezone

from apps.core.llm import chat_json
from apps.discovery.models import InstagramCrawledPost, InstagramEngineLead
from apps.discovery.instagram_families import lead_quality_score

log = logging.getLogger("discovery.family_inspection")


@dataclass(frozen=True)
class FamilyInspectionResult:
    """
    Standardized result of inspecting media posts for a family target.
    """
    posts_inspected: int
    qualifying_posts: int
    leads_saved: int
    retired: bool

    def as_tuple(self) -> Tuple[int, int, int, bool]:
        return (self.posts_inspected, self.qualifying_posts, self.leads_saved, self.retired)


class FamilyInspectionService:
    """
    Service responsible for inspecting media posts, caching crawled comments,
    scoring buyer intent, and saving discovery leads.
    """

    def __init__(
        self,
        stdout_writer: Optional[Callable[[str], Any]] = None,
        stderr_writer: Optional[Callable[[str], Any]] = None,
        api_caller: Optional[Callable[..., Any]] = None,
        comment_evaluator: Optional[Callable[..., Dict[str, Any]]] = None,
        caption_cta_extractor: Optional[Callable[[str], str]] = None,
        llm_batch_evaluator: Optional[Callable[..., Dict[str, Any]]] = None,
    ):
        self.stdout_write = stdout_writer or log.info
        self.stderr_write = stderr_writer or log.error
        self.api_call = api_caller or (lambda account, method, *args, **kwargs: method(*args, **kwargs))
        self.evaluate_comment = comment_evaluator or (lambda *args, **kwargs: {"is_lead": False})
        self.extract_cta = caption_cta_extractor or (lambda caption: "")
        self.llm_evaluate_batch = llm_batch_evaluator or (lambda *args, **kwargs: {})

    def get_or_create_post_reply_template(
        self,
        post_hash: str,
        caption: str,
        cta_word: str,
        family_context: Dict[str, Any],
        business_id: int,
    ) -> str:
        """
        Generate or fetch cached personalized DM reply template based on post caption and inquiry context.
        """
        key = f"ig_tpl:{post_hash[:16]}:{hashlib.md5((cta_word or 'general').encode()).hexdigest()[:8]}"
        cached_tpl = cache.get(key)
        if cached_tpl:
            return cached_tpl

        prompt_system = (
            "شما متخصص فروش حرفه‌ای در دایرکت اینستاگرام هستید. "
            "با توجه به کپشن پست زیر و مشخصات فروشگاه، یک پیام دایرکت کوتاه، صمیمی، محترمانه و ترغیب‌کننده "
            "برای پاسخ به کامنت استعلام قیمت/خرید بنویسید.\n"
            "الزامات مهم:\n"
            "۱. حتماً در ابتدای پیام از برچسب {username} برای نام کاربر استفاده کنید (مثال: سلام {username} عزیز، وقتتون بخیر).\n"
            "۲. متن باید متناسب با کالای معرفی‌شده در کپشن باشد و حس ارسال پیام اختصاصی به کاربر را بدهد.\n"
            "۳. لینک مستقیم، آیدی کانال تلگرام یا شماره تماس قرار ندهید.\n"
            "۴. خروجی را در قالب JSON برگردانید: {\"template\": \"متن پیام...\"}"
        )
        payload = {
            "post_caption": (caption or "")[:800],
            "inquiry_type": cta_word or "استعلام قیمت و موجودی",
            "category": family_context.get("category_path", ""),
            "product_titles": family_context.get("product_titles", [])[:4],
            "seller_notes": (family_context.get("seller_notes") or "")[:250],
        }
        try:
            res = chat_json(
                prompt_system,
                json.dumps(payload, ensure_ascii=False),
                purpose="instagram_caption_reply_template",
                business_id=business_id,
            )
            if isinstance(res, dict) and res.get("template"):
                tpl = str(res["template"]).strip()
            else:
                prod = (family_context.get("product_titles") or ["کالای انتخابی"])[0]
                tpl = f"سلام {{username}} عزیز، وقتتون بخیر. در خصوص «{prod}» که در این پست استعلام فرمودید، قیمت و مشخصات خدمت شما ارسال شد."
        except Exception as exc:
            self.stderr_write(f"Notice: reply template LLM error: {exc}")
            prod = (family_context.get("product_titles") or ["کالای انتخابی"])[0]
            tpl = f"سلام {{username}} عزیز، وقتتون بخیر. در پاسخ به استعلام شما در خصوص «{prod}»، جزئیات کامل خدمت شما تقدیم می‌شود."

        cache.set(key, tpl, timeout=30 * 86400)
        return tpl

    def inspect_family_medias(
        self,
        cl: Any,
        account: Any,
        target: Dict[str, Any],
        source: Any,
        medias: List[Any],
        min_comments: int = 10,
    ) -> FamilyInspectionResult:
        """
        Inspect media posts for a family target, evaluate comments, and extract discovery leads.
        """
        business, family, plan = target["business"], target["family"], target["plan"]
        post_cutoff = dj_timezone.now() - timedelta(days=30)
        comment_cutoff = dj_timezone.now() - timedelta(days=10)
        effective_min = 10
        target_qualifying = 3
        posts_inspected = qualifying_posts = leads_saved = 0
        seen = set()
        buyer_signals = list(plan.buyer_signals or [])
        negatives = list(plan.negative_signals or [])
        family_context = {
            "category_path": family.category_path,
            "product_titles": family.titles[:10],
            "seller_notes": family.notes[:1000],
            "business_domain": business.business_domain,
        }

        self.stdout_write(
            f"  [Scraping Source]: {source.source_type} -> '{source.label}' | Total Medias to inspect: {len(medias or [])}"
        )

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
                taken_at = dj_timezone.make_aware(taken_at, dj_timezone.utc)
            comment_count = int(getattr(media, "comment_count", 0) or 0)

            # Rule 1: Post age limit <= 30 days
            if not taken_at or taken_at < post_cutoff:
                self.stdout_write(
                    f"    [{posts_inspected}] ⏭️ [رد شد - پست قدیمی]: {post_url} (تاریخ: {taken_at.strftime('%Y-%m-%d') if taken_at else 'نامشخص'} > ۳۰ روز)"
                )
                continue

            # Rule 2: Minimum comment count >= 10
            if comment_count < effective_min:
                self.stdout_write(
                    f"    [{posts_inspected}] ⏭️ [رد شد - کامنت کم]: {post_url} (کامنت: {comment_count} < ۱۰)"
                )
                continue

            qualifying_posts += 1
            self.stdout_write(
                f"    🎯 [پست واجد شرایط #{qualifying_posts}]: {post_url} | انتشار: {taken_at.strftime('%Y-%m-%d')} | کامنت: {comment_count} | خواندن کامنت‌های ۱۰ روز اخیر..."
            )

            digest = hashlib.sha256(f"ig_post:{media_id}".encode()).hexdigest()
            cached = InstagramCrawledPost.objects.filter(post_hash=digest).first()
            caption = str(getattr(media, "caption_text", "") or (cached.caption if cached else ""))
            raw_comments = []
            if cached and cached.comments_cached:
                raw_comments = list(cached.comments or [])
            else:
                try:
                    raw = self.api_call(account, cl.media_comments, media_id, amount=min(comment_count, 100))
                except Exception:
                    try:
                        raw = self.api_call(account, cl.media_comments_gql, media_id, amount=min(comment_count, 100))
                    except Exception as exc:
                        self.stderr_write(f"Comment fetch failed for {code or media_id}: {exc}")
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
                        c_time = dj_timezone.make_aware(c_time, dj_timezone.utc)
                    raw_comments.append({
                        "id": str(getattr(comment, "pk", "") or getattr(comment, "id", "") or user_pk),
                        "user_pk": user_pk,
                        "username": username,
                        "full_name": str(getattr(user, "full_name", "") or username),
                        "text": text[:1500],
                        "created_at": c_time.isoformat() if c_time else None,
                        "is_business": getattr(user, "is_business", None),
                        "follower_count": getattr(user, "follower_count", None),
                        "bio": str(getattr(user, "biography", "") or "")[:500],
                    })
                if cached is None:
                    try:
                        cached = InstagramCrawledPost.objects.create(
                            post_hash=digest,
                            media_id=media_id,
                            media_code=code,
                            account_username=account.username,
                            posted_at=taken_at or dj_timezone.now(),
                            caption=caption,
                            comments=raw_comments,
                            comment_count=comment_count,
                            comments_cached=True,
                        )
                    except Exception:
                        cached = InstagramCrawledPost.objects.filter(post_hash=digest).first()
                if cached and not cached.comments_cached:
                    cached.caption, cached.comments, cached.comment_count, cached.comments_cached = caption, raw_comments, comment_count, True
                    cached.save(update_fields=["caption", "comments", "comment_count", "comments_cached"])

            # Rule 3: Comments age limit <= 10 days
            eligible_comments = []
            for row in raw_comments:
                c_date = row.get("created_at")
                if c_date:
                    try:
                        dt = datetime.fromisoformat(c_date)
                        if dj_timezone.is_naive(dt):
                            dt = dj_timezone.make_aware(dt, dj_timezone.utc)
                        if dt < comment_cutoff:
                            continue
                    except Exception:
                        pass
                eligible_comments.append(row)

            self.stdout_write(
                f"      💬 [بررسی کامنت‌ها]: {len(eligible_comments)} کامنت مربوط به ۱۰ روز اخیر (از مجموع {len(raw_comments)} کامنت)"
            )

            if not eligible_comments:
                continue

            cta = self.extract_cta(caption)
            evaluations, ambiguous = {}, []
            for row in eligible_comments:
                eval_row = self.evaluate_comment(
                    row["text"],
                    caption=caption,
                    cta_word=cta,
                    product_context=family.category_path or family.title,
                    buyer_signals=buyer_signals,
                    negative_signals=negatives,
                )
                evaluations[row["id"]] = eval_row
                if eval_row.get("is_ambiguous"):
                    ambiguous.append({"id": row["id"], "username": row["username"], "text": row["text"]})
            ai = {}
            for offset in range(0, len(ambiguous), 25):
                from apps.billing.services import is_blocked
                if is_blocked(business):
                    self.stdout_write(
                        f"Skipping Instagram LLM evaluation for tenant {business.pk}: اعتبار تمام شده"
                    )
                    break
                ai.update(
                    self.llm_evaluate_batch(
                        ambiguous[offset:offset + 25],
                        business_id=business.pk,
                        family_context=family_context,
                        buyer_signals=buyer_signals,
                    )
                )
            evaluations.update(ai)

            # Build or retrieve the cached reply template for this post once
            post_reply_template = None
            if any(evaluations.get(r["id"], {}).get("is_lead") for r in eligible_comments):
                post_reply_template = self.get_or_create_post_reply_template(
                    post_hash=digest,
                    caption=caption,
                    cta_word=cta or "استعلام قیمت و موجودی",
                    family_context=family_context,
                    business_id=business.pk,
                )

            post_leads_saved = 0
            for row in eligible_comments:
                result = evaluations.get(row["id"], {})
                if not result.get("is_lead"):
                    continue
                leads_saved += 1
                post_leads_saved += 1
                result["source_type"] = source.source_type
                result["source_label"] = source.label
                display_name = str(row.get("full_name") or row.get("username") or "").strip()
                if post_reply_template and not result.get("suggested_reply"):
                    result["suggested_reply"] = post_reply_template.replace("{username}", display_name or "کاربر")
                score = lead_quality_score(
                    result.get("intent_score", .8),
                    is_business=row.get("is_business"),
                    follower_count=row.get("follower_count"),
                    bio=row.get("bio", ""),
                    seller_notes=family.notes,
                    family_path=family.category_path,
                    product_titles=family.titles,
                )
                lead, created = InstagramEngineLead.objects.get_or_create(
                    tenant_id=target["tenant_id"],
                    ig_user_pk=row["user_pk"],
                    defaults={
                        "username": row["username"],
                        "full_name": row["full_name"],
                        "profile_url": f"https://www.instagram.com/{row['username']}/",
                        "comment_text": row["text"],
                        "source_post_url": post_url,
                        "post_caption_snippet": caption[:600],
                        "matched_cta": result.get("matched_cta", ""),
                        "intent_score": score,
                        "status": "NEW",
                        "profile_bio": row.get("bio", ""),
                        "commenter_is_business": row.get("is_business"),
                        "follower_count": row.get("follower_count"),
                    },
                )
                from apps.discovery.services import sync_instagram_lead_to_opportunity
                sync_instagram_lead_to_opportunity(lead, business=business, ai_meta=result)
            self.stdout_write(
                f"      ✅ استخراج {post_leads_saved} مشتری واقعی از پست {post_url}"
            )

            if qualifying_posts >= target_qualifying and posts_inspected >= 15:
                self.stdout_write(
                    f"  ✨ به حد نصاب رسید: {qualifying_posts} پست واجد شرایط از میان {posts_inspected} پست بررسی‌شده."
                )
                break

        retired = False
        if qualifying_posts < target_qualifying:
            retired = True

        return FamilyInspectionResult(
            posts_inspected=posts_inspected,
            qualifying_posts=qualifying_posts,
            leads_saved=leads_saved,
            retired=retired,
        )

import random
import time
from datetime import datetime, timezone
from typing import List, Optional

from django.core.management.base import BaseCommand
from django.utils import timezone as dj_timezone

from apps.discovery.models import (
    InstagramEngineAccount,
    InstagramEngineLead,
    InstagramCrawledPost,
    InstagramCrawlExecution,
)
from instagram_engine.analyzer import get_effective_caption_cta, is_buyer_comment, evaluate_comment_smart
from instagram_engine.config import settings


class Command(BaseCommand):
    help = "Runs Instagram Scraper to collect leads from posts with at least 50 comments."

    def add_arguments(self, parser):
        parser.add_argument(
            "--query",
            type=str,
            default="",
            help="Primary search query to discover hashtags for (e.g. 'سنگ ساختمانی').",
        )
        parser.add_argument(
            "--hashtags",
            type=str,
            default="",
            help="Comma-separated list of explicit hashtags to scrape (e.g. 'سنگ_ساختمانی,تراورتن,سنگ_نما').",
        )
        parser.add_argument(
            "--min-comments",
            type=int,
            default=50,
            help="Minimum number of comments required on a post to qualify for comment scraping (default: 50).",
        )
        parser.add_argument(
            "--max-posts",
            type=int,
            default=6,
            help="Maximum posts to inspect per hashtag (default: 6).",
        )
        parser.add_argument(
            "--tenant",
            type=str,
            default="",
            help="Tenant identifier to tag collected leads with (e.g. 'tenant_3' for stone business).",
        )
        parser.add_argument(
            "--loop",
            action="store_true",
            help="Run continuously in background with periodic discovery cycles.",
        )
        parser.add_argument(
            "--interval",
            type=int,
            default=5,
            help="Delay in minutes between discovery cycles in loop mode (default: 5).",
        )
        parser.add_argument(
            "--all-businesses",
            action="store_true",
            help="Automatically collect and scrape hashtags for all active businesses in database.",
        )

    def handle(self, *args, **options):
        query = options["query"]
        explicit_tags = [t.strip().lstrip("#") for t in options["hashtags"].split(",") if t.strip()]
        min_comments = options["min_comments"]
        max_posts = options["max_posts"]
        tenant_id = options["tenant"]

        loop_mode = options["loop"]
        interval_minutes = options["interval"]
        all_businesses = options["all_businesses"] or (not explicit_tags and not query and not tenant_id)

        self.stdout.write(self.style.MIGRATE_HEADING("=== Instagram Lead Discovery & Dataset Collector ==="))
        self.stdout.write(f"Loop Mode: {loop_mode} (Interval: {interval_minutes}m) | All Businesses: {all_businesses}")
        self.stdout.write(f"Min Comments Threshold: >= {min_comments} comments")

        # 2. Initialize Client Library
        try:
            from instagrapi import Client
        except ImportError:
            self.stderr.write(self.style.ERROR("instagrapi is not installed in the environment!"))
            return

        while True:
            # 1. Fetch Active Scraper Accounts Pool
            scraper_accounts = list(
                InstagramEngineAccount.objects.filter(
                    role="SCRAPER",
                    status="ACTIVE",
                )
                .exclude(session_json__isnull=True)
                .order_by("last_action_at")
            )

            if not scraper_accounts:
                self.stderr.write(self.style.WARNING("No active SCRAPER account with stored session found in database!"))
                if loop_mode:
                    time.sleep(60)
                    continue
                return

            account_names = [f"@{a.username} (ID: {a.id})" for a in scraper_accounts]
            self.stdout.write(self.style.SUCCESS(f"Active scraper accounts pool ({len(scraper_accounts)} accounts): {', '.join(account_names)}"))


            # Determine targets
            if all_businesses:
                from apps.businesses.models import Business
                from apps.products.models import Product
                targets = []
                for b in Business.objects.all():
                    prods = Product.objects.filter(business=b, status="ACTIVE", is_discovery_active=True, instagram_discovery_enabled=True)
                    tags = set()
                    terms = set()
                    for p in prods:
                        if p.instagram_hashtags:
                            tags.update(p.instagram_hashtags)
                        if p.instagram_terms:
                            terms.update(p.instagram_terms)

                    # Always include the core business domain and main industry names
                    domain = (getattr(b, "business_domain", "") or "").strip()
                    if domain:
                        terms.add(domain)
                        tags.add(domain.replace(" ", "_"))

                    # Core keyword injections based on business profile
                    name_and_domain = f"{b.name} {domain}".lower()
                    if "سنگ" in name_and_domain:
                        core_stone_terms = ["سنگ ساختمانی", "سنگبری", "سنگ نما", "تراورتن", "مرمریت"]
                        for st in core_stone_terms:
                            terms.add(st)
                            tags.add(st.replace(" ", "_"))
                    if "دندان" in name_and_domain:
                        core_dental_terms = ["دندانپزشکی", "دندان‌پزشکی", "ایمپلنت دندان", "کامپوزیت دندان", "لمینت دندان"]
                        for dt in core_dental_terms:
                            terms.add(dt)
                            tags.add(dt.replace(" ", "_"))

                    if not tags and not terms:
                        if getattr(b, "name", ""):
                            cleaned_name = b.name.strip()
                            terms.add(cleaned_name)
                            tags.add(cleaned_name.replace(" ", "_"))

                    if tags or terms:
                        targets.append({
                            "tenant_id": f"tenant_{b.id}",
                            "business": b,
                            "name": b.name,
                            "hashtags": list(tags),
                            "terms": list(terms),
                            "query": list(terms)[0] if terms else (list(tags)[0] if tags else b.name),
                        })
            else:
                default_tenant = tenant_id or "tenant_default"
                targets = [{
                    "tenant_id": default_tenant,
                    "business": None,
                    "name": default_tenant,
                    "hashtags": explicit_tags,
                    "terms": [query] if query else [],
                    "query": query or "سنگ ساختمانی",
                }]

            self.stdout.write(f"\nProcessing {len(targets)} business discovery targets...")

            # 30-Day TTL Cache Cleanup: Purge post hashes older than 30 days
            from apps.discovery.models import InstagramCrawledPost
            from datetime import timedelta
            thirty_days_ago = dj_timezone.now() - timedelta(days=30)
            try:
                purged_count, _ = InstagramCrawledPost.objects.filter(crawled_at__lt=thirty_days_ago).delete()
                if purged_count > 0:
                    self.stdout.write(self.style.NOTICE(f"🧹 [30-Day TTL Cache]: Purged {purged_count} post hashes older than 30 days."))
            except Exception as ttl_exc:
                self.stderr.write(f"Notice: 30-day TTL hash cleanup notice: {ttl_exc}")

            cycle_leads = 0
            for idx, target in enumerate(targets):
                account = scraper_accounts[idx % len(scraper_accounts)]
                self.stdout.write(self.style.SUCCESS(f"\n[Assigning Scraper]: @{account.username} (ID: {account.id}) for target: {target['name']}"))
                cl = self._build_client(account)
                try:
                    leads_found = self._scrape_target(cl, account, target, min_comments, max_posts)
                    cycle_leads += leads_found
                except Exception as exc:
                    self.stderr.write(f"Error while scraping target {target['name']}: {exc}")
                time.sleep(random.uniform(4.0, 8.0))

            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== Discovery Cycle Complete ({cycle_leads} leads captured) ==="))

            if not loop_mode:
                break

            self.stdout.write(self.style.NOTICE(f"Sleeping for {interval_minutes} minutes before next cycle..."))
            time.sleep(interval_minutes * 60)

    def _build_client(self, account):
        from instagrapi import Client
        cl = Client()
        cl.delay_range = [2, 5]

        if account.session_json:
            cl.set_settings(account.session_json)
            cl.username = account.username
            cookies = account.session_json.get("cookies", {})
            for k, v in cookies.items():
                try:
                    cl.public.cookies.set(k, v, domain=".instagram.com")
                    cl.private.cookies.set(k, v, domain=".instagram.com")
                except Exception:
                    pass

        if account.proxy_url:
            from apps.billing.manage_views import _test_proxy_connection
            proxy_ok, proxy_msg = _test_proxy_connection(account.proxy_url, timeout=2.5)
            if proxy_ok:
                cl.set_proxy(account.proxy_url)
            else:
                self.stdout.write(self.style.WARNING(f"Proxy '{account.proxy_url}' unreachable ({proxy_msg}). Falling back to direct connection."))

        return cl

    def _scrape_target(self, cl, account, target, min_comments, max_posts) -> int:
        tenant_id = target["tenant_id"]
        business_name = target["name"]
        tags_to_scrape = list(target["hashtags"])
        query = target.get("query") or ""

        self.stdout.write(self.style.HTTP_INFO(f"\n--- Target: {business_name} ({tenant_id}) ---"))

        if not tags_to_scrape and query:
            normalized_query_tag = query.strip().replace(" ", "_")
            tags_to_scrape.append(normalized_query_tag)
            try:
                self.stdout.write(f"Searching related hashtags on Instagram for '{query}'...")
                search_results = cl.search_hashtags(query)
                for h in search_results:
                    name = getattr(h, "name", None) or getattr(h, "id", None)
                    if name and name not in tags_to_scrape:
                        tags_to_scrape.append(name)
                    if len(tags_to_scrape) >= 3:
                        break
            except Exception as e:
                self.stdout.write(f"Hashtag search fallback warning: {e}")

        self.stdout.write(f"Hashtags to inspect: {tags_to_scrape}")

        total_posts_inspected = 0
        total_qualifying_posts = 0
        total_leads_saved = 0

        # MMR-driven diverse hashtag selection
        from shared.mmr import select_by_mmr
        selected_tags = select_by_mmr(
            tags_to_scrape,
            lambda t: t.replace("_", " "),
            reference_context=f"{business_name} {query}",
            top_k=4,
            lambda_param=0.65,
        )
        self.stdout.write(f"MMR selected diverse hashtags for this cycle: {selected_tags}")

        # Scrape loop for target
        for tag in selected_tags:
            self.stdout.write(self.style.WARNING(f"\n  [#] Hashtag: #{tag}"))
            tag_posts_inspected = 0
            tag_qualifying_posts = 0
            tag_leads_saved = 0
            tag_err = ""
            medias = []
            seen_media_ids = set()

            # 1. Fetch Top / Viral Posts
            try:
                top_medias = cl.hashtag_medias_top(tag, amount=max_posts)
            except Exception:
                try:
                    top_medias = cl.hashtag_medias_top_v1(tag, amount=max_posts)
                except Exception as exc:
                    top_medias = []
                    self.stderr.write(f"    Notice: hashtag_medias_top warning for #{tag}: {exc}")
            for m in top_medias:
                if str(m.id) not in seen_media_ids:
                    seen_media_ids.add(str(m.id))
                    medias.append(m)
            if top_medias:
                self.stdout.write(f"    Retrieved {len(top_medias)} TOP posts for #{tag}.")

            # 2. Fetch Recent Posts
            try:
                recent_medias = cl.hashtag_medias_recent(tag, amount=max_posts)
            except Exception:
                try:
                    recent_medias = cl.hashtag_medias_recent_v1(tag, amount=max_posts)
                except Exception as exc:
                    recent_medias = []
                    self.stderr.write(f"    Notice: hashtag_medias_recent warning for #{tag}: {exc}")
            for m in recent_medias:
                if str(m.id) not in seen_media_ids:
                    seen_media_ids.add(str(m.id))
                    medias.append(m)
            if recent_medias:
                self.stdout.write(f"    Retrieved {len(recent_medias)} RECENT posts for #{tag}.")

            # 3. Fallback to Web GraphQL if private mobile API returns no posts
            if not medias:
                try:
                    gql_medias, _ = cl.hashtag_medias_paginated_gql(tag, amount=max_posts)
                    for m in gql_medias:
                        if str(m.id) not in seen_media_ids:
                            seen_media_ids.add(str(m.id))
                            medias.append(m)
                    if gql_medias:
                        self.stdout.write(f"    Retrieved {len(gql_medias)} posts via GraphQL for #{tag}.")
                except Exception as exc:
                    self.stderr.write(f"    Notice: hashtag_medias_paginated_gql warning for #{tag}: {exc}")

            for media in medias:
                total_posts_inspected += 1
                tag_posts_inspected += 1
                media_id = str(media.id)
                code = getattr(media, "code", "")
                source_post_url = f"https://www.instagram.com/p/{code}/" if code else None
                caption_text = getattr(media, "caption_text", "") or ""
                comment_count = getattr(media, "comment_count", 0) or 0

                # 1. Age Pre-Filter: Strictly reject posts published more than 30 days ago
                import hashlib
                from datetime import timedelta
                from apps.discovery.models import InstagramCrawledPost
                thirty_days_ago = dj_timezone.now() - timedelta(days=30)
                taken_at = getattr(media, "taken_at", None)
                if taken_at:
                    if dj_timezone.is_naive(taken_at):
                        taken_at = dj_timezone.make_aware(taken_at, dj_timezone.utc)
                    if taken_at < thirty_days_ago:
                        self.stdout.write(
                            self.style.WARNING(
                                f"    ⏭️ [SKIPPED POST > 30 DAYS OLD]: {source_post_url} (Published: {taken_at.strftime('%Y-%m-%d')})"
                            )
                        )
                        continue

                # 2. Multi-Account Post Hash Deduplication: Never crawl the same post twice across any account
                post_hash = hashlib.sha256(f"ig_post:{media_id}".encode("utf-8")).hexdigest()
                if InstagramCrawledPost.objects.filter(post_hash=post_hash).exists():
                    self.stdout.write(
                        self.style.WARNING(
                            f"    ⏭️ [SKIPPED POST ALREADY CRAWLED (Hash Dedup)]: {source_post_url} (Hash: {post_hash[:10]})"
                        )
                    )
                    continue

                # Strict Filter: Must have at least min_comments (50)
                if comment_count < min_comments:
                    continue

                # Domain Relevance Filter: Skip posts that are outside the business domain (e.g. real estate/villa sales for stone business)
                from instagram_engine.post_filters import is_irrelevant_post_for_business
                is_irrel, irrel_reason = is_irrelevant_post_for_business(caption_text, business_name)
                if is_irrel:
                    self.stdout.write(
                        self.style.WARNING(
                            f"    ⏭️ [SKIPPED UNRELATED POST: {irrel_reason}] {source_post_url}"
                        )
                    )
                    continue

                # 3. Atomic Reservation: Register post in InstagramCrawledPost before comment extraction
                try:
                    InstagramCrawledPost.objects.create(
                        post_hash=post_hash,
                        media_id=media_id,
                        media_code=code,
                        account_username=account.username,
                        posted_at=taken_at or dj_timezone.now(),
                    )
                except Exception:
                    self.stdout.write(
                        self.style.WARNING(
                            f"    ⏭️ [SKIPPED CONCURRENTLY CLAIMED POST]: {source_post_url}"
                        )
                    )
                    continue

                total_qualifying_posts += 1
                tag_qualifying_posts += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    🎯 [QUALIFYING POST] {source_post_url} | Comments: {comment_count} (>= {min_comments}) | Hash: {post_hash[:10]}"
                    )
                )

                cta_trigger = get_effective_caption_cta(caption_text)
                fetch_amount = min(comment_count, 100)
                try:
                    comments = cl.media_comments(media_id, amount=fetch_amount)
                except Exception as exc:
                    try:
                        comments = cl.media_comments_gql(media_id, amount=fetch_amount)
                    except Exception:
                        self.stderr.write(f"    Failed to fetch comments: {exc}")
                        continue

                # Statistical detection of repeated keywords (followers requesting DM)
                from instagram_engine.analyzer import detect_repeated_comment_triggers
                all_comment_texts = [getattr(c, "text", "") for c in comments if getattr(c, "text", "")]
                repeated_triggers = detect_repeated_comment_triggers(all_comment_texts, min_count=2)
                if repeated_triggers:
                    self.stdout.write(
                        self.style.NOTICE(f"    🔄 [DETECTED REPEATED DM TRIGGERS]: {repeated_triggers}")
                    )

                for c in comments:
                    user_obj = getattr(c, "user", None)
                    if not user_obj:
                        continue

                    user_pk = str(getattr(user_obj, "pk", "") or getattr(user_obj, "id", ""))
                    username = getattr(user_obj, "username", "")
                    full_name = getattr(user_obj, "full_name", "") or username
                    comment_text = getattr(c, "text", "")

                    if not user_pk or not username or not comment_text:
                        continue

                    # Multi-tier smart comment evaluation & zero-cost emoji/toxicity filter
                    eval_res = evaluate_comment_smart(
                        comment_text=comment_text,
                        caption=caption_text,
                        cta_word=cta_trigger,
                        product_context=business_name,
                        repeated_triggers=repeated_triggers,
                    )

                    if eval_res.get("is_emoji"):
                        # Zero token cost, skipped early
                        continue

                    if eval_res.get("is_toxic"):
                        self.stdout.write(
                            self.style.WARNING(
                                f"      🚫 [REJECTED TOXIC/DISS] @{username}: {eval_res.get('reason')} | '{comment_text[:45]}'"
                            )
                        )
                        continue

                    if not eval_res.get("is_lead"):
                        continue

                    score = float(eval_res.get("intent_score") or 0.90)
                    match_reason = eval_res.get("matched_cta") or "direct"
                    profile_url = f"https://www.instagram.com/{username}/"
                    post_snippet = caption_text[:600] if caption_text else None

                    lead, created = InstagramEngineLead.objects.get_or_create(
                        tenant_id=tenant_id,
                        ig_user_pk=user_pk,
                        defaults={
                            "username": username,
                            "full_name": full_name,
                            "profile_url": profile_url,
                            "comment_text": comment_text,
                            "source_post_url": source_post_url,
                            "post_caption_snippet": post_snippet,
                            "matched_cta": match_reason,
                            "intent_score": score,
                            "status": "NEW",
                        },
                    )

                    if created:
                        total_leads_saved += 1
                        tag_leads_saved += 1
                        lead_type_label = "⚡ مستقیم (Zero Cost)" if eval_res.get("is_direct_cta") else "🤖 هوش مصنوعی (Luna GPT-6)"
                        self.stdout.write(
                            self.style.SUCCESS(
                                f"      ⭐ Lead Discovered ({lead_type_label}): @{username} (score: {score}) | '{comment_text[:50]}'"
                            )
                        )

                    # Sync directly into unified Opportunity domain with AI and billing metadata
                    try:
                        from apps.discovery.services import sync_instagram_lead_to_opportunity
                        sync_instagram_lead_to_opportunity(lead, business=target.get("business"), ai_meta=eval_res)
                    except Exception as sync_exc:
                        self.stderr.write(f"Notice: Opportunity sync skipped: {sync_exc}")

                time.sleep(random.uniform(4.0, 8.0))

            # Record audit execution in InstagramCrawlExecution for this hashtag
            exec_status = "SUCCESS" if tag_qualifying_posts > 0 else ("NO_POSTS" if tag_posts_inspected == 0 else "SUCCESS")
            if tag_err and not medias:
                exec_status = "FAILED"
            msg_val = (
                f"بررسی {tag_posts_inspected} پست، {tag_qualifying_posts} پست حائز شرایط (کامنت >= {min_comments})، {tag_leads_saved} لید جدید کشف شد."
                if not (tag_err and not medias)
                else f"خطا در اسکن هشتگ #{tag}: {tag_err}"
            )
            try:
                InstagramCrawlExecution.objects.create(
                    business=target.get("business"),
                    tenant_id=tenant_id,
                    query_text=f"#{tag}" if not tag.startswith("#") else tag,
                    target_type="HASHTAG",
                    account_username=account.username,
                    account=account,
                    status=exec_status,
                    posts_inspected=tag_posts_inspected,
                    qualifying_posts=tag_qualifying_posts,
                    leads_found=tag_leads_saved,
                    message=msg_val,
                    crawled_at=dj_timezone.now(),
                )
                self.stdout.write(self.style.SUCCESS(f"    📝 [Crawl Log Saved]: #{tag} by @{account.username} ({exec_status})"))
            except Exception as log_exc:
                self.stderr.write(f"Notice: Failed to save InstagramCrawlExecution for #{tag}: {log_exc}")

            time.sleep(random.uniform(3.0, 6.0))

        # Check active competitor/target pages for business
        if target.get("business"):
            from apps.discovery.models import InstagramPage
            comp_pages = InstagramPage.objects.filter(business=target["business"], is_active=True)
            for page in comp_pages:
                handle = page.handle.lstrip("@").strip()
                self.stdout.write(self.style.WARNING(f"\n  [👤] Scraper @{account.username} inspecting competitor page: @{handle}"))
                page_posts_inspected = 0
                page_qualifying_posts = 0
                page_leads_saved = 0
                page_err = ""
                page_medias = []
                try:
                    uid = cl.user_id_from_username(handle)
                    page_medias = cl.user_medias(uid, amount=max_posts)
                except Exception as exc:
                    page_err = str(exc)
                    self.stderr.write(f"    Notice: Error fetching medias from @{handle}: {exc}")

                for media in page_medias:
                    page_posts_inspected += 1
                    total_posts_inspected += 1
                    media_id = str(media.id)
                    code = getattr(media, "code", "")
                    source_post_url = f"https://www.instagram.com/p/{code}/" if code else None
                    caption_text = getattr(media, "caption_text", "") or ""
                    comment_count = getattr(media, "comment_count", 0) or 0

                    from datetime import timedelta
                    import hashlib
                    thirty_days_ago = dj_timezone.now() - timedelta(days=30)
                    taken_at = getattr(media, "taken_at", None)
                    if taken_at:
                        if dj_timezone.is_naive(taken_at):
                            taken_at = dj_timezone.make_aware(taken_at, dj_timezone.utc)
                        if taken_at < thirty_days_ago:
                            continue

                    post_hash = hashlib.sha256(f"ig_post:{media_id}".encode("utf-8")).hexdigest()
                    if InstagramCrawledPost.objects.filter(post_hash=post_hash).exists():
                        continue

                    if comment_count < min_comments:
                        continue

                    try:
                        InstagramCrawledPost.objects.create(
                            post_hash=post_hash,
                            media_id=media_id,
                            media_code=code,
                            account_username=account.username,
                            posted_at=taken_at or dj_timezone.now(),
                        )
                    except Exception:
                        continue

                    page_qualifying_posts += 1
                    total_qualifying_posts += 1
                    self.stdout.write(self.style.SUCCESS(f"    🎯 [QUALIFYING PAGE POST] {source_post_url} | Comments: {comment_count}"))

                    cta_trigger = get_effective_caption_cta(caption_text)
                    fetch_amount = min(comment_count, 100)
                    try:
                        comments = cl.media_comments(media_id, amount=fetch_amount)
                    except Exception:
                        try:
                            comments = cl.media_comments_gql(media_id, amount=fetch_amount)
                        except Exception:
                            continue

                    from instagram_engine.analyzer import detect_repeated_comment_triggers
                    all_comment_texts = [getattr(c, "text", "") for c in comments if getattr(c, "text", "")]
                    repeated_triggers = detect_repeated_comment_triggers(all_comment_texts, min_count=2)

                    for c in comments:
                        user_obj = getattr(c, "user", None)
                        if not user_obj:
                            continue
                        user_pk = str(getattr(user_obj, "pk", "") or getattr(user_obj, "id", ""))
                        username = getattr(user_obj, "username", "")
                        full_name = getattr(user_obj, "full_name", "") or username
                        comment_text = getattr(c, "text", "")
                        if not user_pk or not username or not comment_text:
                            continue

                        eval_res = evaluate_comment_smart(
                            comment_text=comment_text,
                            caption=caption_text,
                            cta_word=cta_trigger,
                            product_context=business_name,
                            repeated_triggers=repeated_triggers,
                        )
                        if eval_res.get("is_emoji") or eval_res.get("is_toxic") or not eval_res.get("is_lead"):
                            continue

                        score = float(eval_res.get("intent_score") or 0.90)
                        match_reason = eval_res.get("matched_cta") or "direct"
                        lead, created = InstagramEngineLead.objects.get_or_create(
                            tenant_id=tenant_id,
                            ig_user_pk=user_pk,
                            defaults={
                                "username": username,
                                "full_name": full_name,
                                "profile_url": f"https://www.instagram.com/{username}/",
                                "comment_text": comment_text,
                                "source_post_url": source_post_url,
                                "post_caption_snippet": caption_text[:600] if caption_text else None,
                                "matched_cta": match_reason,
                                "intent_score": score,
                                "status": "NEW",
                            },
                        )
                        if created:
                            page_leads_saved += 1
                            total_leads_saved += 1
                            self.stdout.write(self.style.SUCCESS(f"      ⭐ Lead from @{handle}: @{username} (score: {score})"))

                        try:
                            from apps.discovery.services import sync_instagram_lead_to_opportunity
                            sync_instagram_lead_to_opportunity(lead, business=target.get("business"), ai_meta=eval_res)
                        except Exception:
                            pass

                page_status = "SUCCESS" if page_qualifying_posts > 0 else ("NO_POSTS" if page_posts_inspected == 0 else "SUCCESS")
                if page_err and not page_medias:
                    page_status = "FAILED"
                try:
                    InstagramCrawlExecution.objects.create(
                        business=target.get("business"),
                        tenant_id=tenant_id,
                        query_text=f"@{handle}",
                        target_type="PAGE",
                        account_username=account.username,
                        account=account,
                        status=page_status,
                        posts_inspected=page_posts_inspected,
                        qualifying_posts=page_qualifying_posts,
                        leads_found=page_leads_saved,
                        message=f"بررسی {page_posts_inspected} پست از صفحه @{handle}، {page_qualifying_posts} پست حائز شرایط، {page_leads_saved} سرنخ ثبت شد." if not (page_err and not page_medias) else f"خطای دسترسی: {page_err}",
                        crawled_at=dj_timezone.now(),
                    )
                    self.stdout.write(self.style.SUCCESS(f"    📝 [Crawl Log Saved]: @{handle} by @{account.username} ({page_status})"))
                except Exception as log_exc:
                    self.stderr.write(f"Notice: Failed to log InstagramCrawlExecution for @{handle}: {log_exc}")

        # Update account action count
        account.daily_action_count += total_qualifying_posts
        account.last_action_at = dj_timezone.now()
        account.save(update_fields=["daily_action_count", "last_action_at"])

        return total_leads_saved

import random
import time
from datetime import datetime, timezone
from typing import List, Optional

from django.core.management.base import BaseCommand
from django.utils import timezone as dj_timezone

from apps.discovery.models import InstagramEngineAccount, InstagramEngineLead
from instagram_engine.analyzer import get_effective_caption_cta, is_buyer_comment
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
            default=20,
            help="Delay in minutes between discovery cycles in loop mode (default: 20).",
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

        # 1. Fetch Active Scraper Account
        account = (
            InstagramEngineAccount.objects.filter(
                role="SCRAPER",
                status="ACTIVE",
            )
            .exclude(session_json__isnull=True)
            .order_by("last_action_at")
            .first()
        )

        if not account:
            self.stderr.write(self.style.ERROR("No active SCRAPER account with stored session found in database!"))
            return

        self.stdout.write(self.style.SUCCESS(f"Using scraper account: @{account.username} (ID: {account.id})"))

        # 2. Initialize Client with Session & Anti-Ban Proxy
        try:
            from instagrapi import Client
        except ImportError:
            self.stderr.write(self.style.ERROR("instagrapi is not installed in the environment!"))
            return

        cl = Client()
        if account.proxy_url:
            from apps.billing.manage_views import _test_proxy_connection
            proxy_ok, proxy_msg = _test_proxy_connection(account.proxy_url, timeout=2.5)
            if proxy_ok:
                self.stdout.write(f"Binding verified proxy: {account.proxy_url}")
                cl.set_proxy(account.proxy_url)
            else:
                self.stdout.write(self.style.WARNING(f"Proxy '{account.proxy_url}' unreachable ({proxy_msg}). Falling back to direct connection."))

        cl.set_settings(account.session_json)
        cl.delay_range = [2, 5]

        # Verify session health
        try:
            cl.get_timeline_feed()
            self.stdout.write(self.style.SUCCESS("Session validated successfully."))
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Session validation failed: {exc}"))
            return

        while True:
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
            cycle_leads = 0
            for target in targets:
                try:
                    leads_found = self._scrape_target(cl, account, target, min_comments, max_posts)
                    cycle_leads += leads_found
                except Exception as exc:
                    self.stderr.write(f"Error while scraping target {target['name']}: {exc}")
                time.sleep(random.uniform(6.0, 12.0))

            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== Discovery Cycle Complete ({cycle_leads} leads captured) ==="))

            if not loop_mode:
                break

            self.stdout.write(self.style.NOTICE(f"Sleeping for {interval_minutes} minutes before next cycle..."))
            time.sleep(interval_minutes * 60)

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

        # Scrape loop for target
        for tag in tags_to_scrape[:4]:
            self.stdout.write(self.style.WARNING(f"\n  [#] Hashtag: #{tag}"))
            medias = []
            seen_media_ids = set()

            # 1. Fetch Top / Viral Posts
            try:
                top_medias = cl.hashtag_medias_top(tag, amount=max_posts)
                for m in top_medias:
                    if str(m.id) not in seen_media_ids:
                        seen_media_ids.add(str(m.id))
                        medias.append(m)
                self.stdout.write(f"    Retrieved {len(top_medias)} TOP posts for #{tag}.")
            except Exception as exc:
                self.stderr.write(f"    Notice: hashtag_medias_top warning for #{tag}: {exc}")

            # 2. Fetch Recent Posts
            try:
                recent_medias = cl.hashtag_medias_recent(tag, amount=max_posts)
                for m in recent_medias:
                    if str(m.id) not in seen_media_ids:
                        seen_media_ids.add(str(m.id))
                        medias.append(m)
                self.stdout.write(f"    Retrieved {len(recent_medias)} RECENT posts for #{tag}.")
            except Exception as exc:
                self.stderr.write(f"    Notice: hashtag_medias_recent warning for #{tag}: {exc}")

            for media in medias:
                total_posts_inspected += 1
                media_id = str(media.id)
                code = getattr(media, "code", "")
                source_post_url = f"https://www.instagram.com/p/{code}/" if code else None
                caption_text = getattr(media, "caption_text", "") or ""
                comment_count = getattr(media, "comment_count", 0) or 0

                # Strict Filter: Must have at least min_comments (50)
                if comment_count < min_comments:
                    continue

                total_qualifying_posts += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"    🎯 [QUALIFYING POST] {source_post_url} | Comments: {comment_count} (>= {min_comments})"
                    )
                )

                cta_trigger = get_effective_caption_cta(caption_text)
                fetch_amount = min(comment_count, 100)
                try:
                    comments = cl.media_comments(media_id, amount=fetch_amount)
                except Exception as exc:
                    self.stderr.write(f"    Failed to fetch comments: {exc}")
                    continue

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

                    is_lead, match_reason = is_buyer_comment(comment_text, cta_word=cta_trigger)
                    if is_lead:
                        score = 0.95 if match_reason and "cta_match" in match_reason else 0.90
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
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"      ⭐ Lead Discovered: @{username} (score: {score}) | '{comment_text[:50]}'"
                                )
                            )

                        # Sync directly into unified Opportunity domain
                        try:
                            from apps.discovery.services import sync_instagram_lead_to_opportunity
                            sync_instagram_lead_to_opportunity(lead, business=target.get("business"))
                        except Exception as sync_exc:
                            self.stderr.write(f"Notice: Opportunity sync skipped: {sync_exc}")

                time.sleep(random.uniform(6.0, 14.0))

            time.sleep(random.uniform(15.0, 30.0))

        # Update account action count
        account.daily_action_count += total_qualifying_posts
        account.last_action_at = dj_timezone.now()
        account.save(update_fields=["daily_action_count", "last_action_at"])

        return total_leads_saved

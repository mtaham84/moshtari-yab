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
            default="سنگ ساختمانی",
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
            default=15,
            help="Maximum posts to inspect per hashtag (default: 15).",
        )
        parser.add_argument(
            "--tenant",
            type=str,
            default="tenant_6",
            help="Tenant identifier to tag collected leads with (e.g. 'tenant_6' for stone business).",
        )

    def handle(self, *args, **options):
        query = options["query"]
        explicit_tags = [t.strip().lstrip("#") for t in options["hashtags"].split(",") if t.strip()]
        min_comments = options["min_comments"]
        max_posts = options["max_posts"]
        tenant_id = options["tenant"]

        self.stdout.write(self.style.MIGRATE_HEADING("=== Instagram Lead Discovery & Dataset Collector ==="))
        self.stdout.write(f"Target Query: '{query}'")
        self.stdout.write(f"Min Comments Threshold: >= {min_comments} comments")
        self.stdout.write(f"Tenant Tag: {tenant_id}")

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

        # 3. Determine Hashtags to scrape
        tags_to_scrape = list(explicit_tags)
        if not tags_to_scrape:
            normalized_query_tag = query.strip().replace(" ", "_")
            tags_to_scrape.append(normalized_query_tag)
            try:
                self.stdout.write(f"Searching related hashtags on Instagram for '{query}'...")
                search_results = cl.search_hashtags(query)
                for h in search_results:
                    name = getattr(h, "name", None) or getattr(h, "id", None)
                    if name and name not in tags_to_scrape:
                        tags_to_scrape.append(name)
                    if len(tags_to_scrape) >= 4:
                        break
            except Exception as e:
                self.stdout.write(f"Hashtag search fallback warning: {e}")

        self.stdout.write(f"Hashtags to process: {tags_to_scrape}")

        total_posts_inspected = 0
        total_qualifying_posts = 0
        total_leads_saved = 0

        # 4. Scrape Loop
        for tag in tags_to_scrape:
            self.stdout.write(self.style.WARNING(f"\n--- Scraping Hashtag: #{tag} ---"))
            medias = []
            seen_media_ids = set()

            # 1. Fetch Top / Viral Posts (highest probability of having >= 50 comments)
            try:
                top_medias = cl.hashtag_medias_top(tag, amount=max_posts)
                for m in top_medias:
                    if str(m.id) not in seen_media_ids:
                        seen_media_ids.add(str(m.id))
                        medias.append(m)
                self.stdout.write(f"Retrieved {len(top_medias)} TOP posts for #{tag}.")
            except Exception as exc:
                self.stderr.write(f"Notice: hashtag_medias_top warning for #{tag}: {exc}")

            # 2. Fetch Recent Posts
            try:
                recent_medias = cl.hashtag_medias_recent(tag, amount=max_posts)
                for m in recent_medias:
                    if str(m.id) not in seen_media_ids:
                        seen_media_ids.add(str(m.id))
                        medias.append(m)
                self.stdout.write(f"Retrieved {len(recent_medias)} RECENT posts for #{tag}.")
            except Exception as exc:
                self.stderr.write(f"Notice: hashtag_medias_recent warning for #{tag}: {exc}")

            self.stdout.write(f"Total unique posts to inspect for #{tag}: {len(medias)}")

            for media in medias:
                total_posts_inspected += 1
                media_id = str(media.id)
                code = getattr(media, "code", "")
                source_post_url = f"https://www.instagram.com/p/{code}/" if code else None
                caption_text = getattr(media, "caption_text", "") or ""
                comment_count = getattr(media, "comment_count", 0) or 0

                # Strict Filter: Must have at least `min_comments` (default 50)
                if comment_count < min_comments:
                    self.stdout.write(f"⏩ [Skip] {source_post_url}: {comment_count} comments (< {min_comments})")
                    continue

                total_qualifying_posts += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"🎯 [QUALIFYING POST] {source_post_url} | Comments: {comment_count} (>= {min_comments})"
                    )
                )

                # Step A: Post CTA trigger extraction
                cta_trigger = get_effective_caption_cta(caption_text)
                if cta_trigger:
                    self.stdout.write(f"   Detected CTA trigger in caption: '{cta_trigger}'")

                # Step B: Fetch comments
                fetch_amount = min(comment_count, 100)
                try:
                    comments = cl.media_comments(media_id, amount=fetch_amount)
                except Exception as exc:
                    self.stderr.write(f"   Failed to fetch comments: {exc}")
                    continue

                self.stdout.write(f"   Fetched {len(comments)} comments for evaluation.")

                # Step C: Evaluate buyer intent & save leads
                post_leads_found = 0
                for c in comments:
                    user_obj = getattr(c, "user", None)
                    if not user_obj:
                        continue

                    # ZERO EXTRA API CALL RULE: extract directly from comment
                    user_pk = str(getattr(user_obj, "pk", "") or getattr(user_obj, "id", ""))
                    username = getattr(user_obj, "username", "")
                    full_name = getattr(user_obj, "full_name", "") or username
                    comment_text = getattr(c, "text", "")

                    if not user_pk or not username or not comment_text:
                        continue

                    # Evaluate buyer intent
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
                            post_leads_found += 1
                            total_leads_saved += 1
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"   ⭐ Lead Discovered: @{username} (score: {score}) | '{comment_text[:60]}'"
                                )
                            )

                        # Sync directly into unified Opportunity domain
                        try:
                            from apps.discovery.services import sync_instagram_lead_to_opportunity
                            sync_instagram_lead_to_opportunity(lead)
                        except Exception as sync_exc:
                            self.stderr.write(f"Notice: Opportunity sync skipped: {sync_exc}")

                self.stdout.write(f"   Total qualified leads extracted from this post: {post_leads_found}")

                # Anti-ban sleep between qualifying posts
                sleep_sec = random.uniform(8.0, 18.0)
                time.sleep(sleep_sec)

            # Anti-ban sleep between hashtags
            sleep_tag = random.uniform(20.0, 45.0)
            self.stdout.write(f"Pacing delay before next hashtag: {sleep_tag:.1f}s...")
            time.sleep(sleep_tag)

        # Update account action count
        account.daily_action_count += total_qualifying_posts
        account.last_action_at = dj_timezone.now()
        account.save(update_fields=["daily_action_count", "last_action_at"])

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== Discovery Summary ==="))
        self.stdout.write(f"Posts Inspected: {total_posts_inspected}")
        self.stdout.write(f"Qualifying Posts (>= {min_comments} comments): {total_qualifying_posts}")
        self.stdout.write(self.style.SUCCESS(f"New Qualified Leads Saved to Dataset: {total_leads_saved}"))
        self.stdout.write("Dataset is accessible in /manage/ or exportable as CSV/JSON.")

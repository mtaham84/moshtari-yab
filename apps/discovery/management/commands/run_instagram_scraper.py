import random
import os
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
from apps.discovery.ig_session import (
    ErrorKind, Policy, account_lock, classify, load_file, redact, save_atomic, session_path,
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
            help="Primary search query used in one-off manual mode.",
        )
        parser.add_argument(
            "--hashtags",
            type=str,
            default="",
            help="Comma-separated list of explicit hashtags to scrape in one-off manual mode.",
        )
        parser.add_argument(
            "--min-comments",
            type=int,
            default=int(os.getenv("INSTAGRAM_MIN_COMMENTS", "20")),
            help="Minimum comments required on a post (configurable through INSTAGRAM_MIN_COMMENTS).",
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
            help="Tenant identifier to tag collected leads with in one-off manual mode (e.g. 'tenant_3').",
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
            scraper_accounts = self._available_scraper_accounts()

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
                from apps.discovery.instagram_families import families_for_business, ensure_family_plan, discover_sources, sample_sources
                targets = []
                by_tenant = []
                for b in Business.objects.order_by("id"):
                    family_rows = {}
                    tenant_sources = []
                    for family in families_for_business(b):
                        try:
                            plan, built = ensure_family_plan(b, family)
                        except Exception as plan_exc:
                            self.stderr.write(f"Plan failed for {b.pk}/{family.product_id}: {plan_exc}")
                            continue
                        if plan is None:
                            continue
                        self.stdout.write(f"Instagram family plan {family.product_id}: {plan.__dict__}")
                        sources = discover_sources(b, family, plan)
                        family_rows[family.product_id] = (family, plan, sources)
                        tenant_sources.extend(sources)
                    selected = sample_sources(
                        b, tenant_sources,
                        min(4, int(os.getenv("INSTAGRAM_MAX_SOURCES_PER_TENANT_CYCLE", "4"))),
                    )
                    selected_by_family = {}
                    for source in selected:
                        selected_by_family.setdefault(source.family_id, []).append(source)
                    targets_for_business = []
                    for family_id, sources in selected_by_family.items():
                        family, plan, _ = family_rows[family_id]
                        targets_for_business.append({
                            "tenant_id": f"tenant_{b.id}", "business": b, "name": b.name,
                            "family": family, "plan": plan, "sources": sources,
                            "hashtags": [], "terms": [], "query": family.category_path or family.title,
                        })
                    by_tenant.append(targets_for_business)
                # Tenants take turns first; each tenant then advances one family before the next tenant round.
                targets = [group[offset] for offset in range(max((len(group) for group in by_tenant), default=0))
                           for group in by_tenant if offset < len(group)]
            else:
                default_tenant = tenant_id or "tenant_default"
                targets = [{
                    "tenant_id": default_tenant,
                    "business": None,
                    "name": default_tenant,
                    "hashtags": explicit_tags,
                    "terms": [query] if query else [],
                    "query": query or "محصولات",
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
            for target in targets:
                # Sticky account assignment per business: keeps account steady without switching
                b_obj = target.get("business")
                b_id = getattr(b_obj, "id", 0) if b_obj else 0
                account = scraper_accounts[b_id % len(scraper_accounts)]
                if not self._is_usable(account):
                    healthy = [a for a in scraper_accounts if self._is_usable(a)]
                    if not healthy:
                        self.stderr.write(self.style.WARNING(
                            f"No healthy scraper account left this cycle; skipping target {target['name']}."
                        ))
                        continue
                    account = healthy[b_id % len(healthy)]

                self.stdout.write(self.style.SUCCESS(f"\n[Assigning Scraper]: @{account.username} (ID: {account.id}) for target: {target['name']}"))
                cl = self._get_persistent_client(account)
                try:
                    if target.get("family"):
                        leads_found = self._scrape_family_target(cl, account, target, min_comments, max_posts)
                    else:
                        leads_found = self._scrape_target(cl, account, target, min_comments, max_posts)
                    cycle_leads += leads_found
                    self._persist_session(cl, account)
                except Exception as exc:
                    self._note_account_error(account, str(exc), cl, exc)
                    self.stderr.write(f"Error while scraping target {target['name']}: {exc}")
                time.sleep(random.uniform(4.0, 8.0))

            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== Discovery Cycle Complete ({cycle_leads} leads captured) ==="))

            if not loop_mode:
                break

            self.stdout.write(self.style.NOTICE(f"Sleeping for {interval_minutes} minutes before next cycle..."))
            time.sleep(interval_minutes * 60)

    def _get_persistent_client(self, account):
        """
        Maintains a persistent, warm in-memory Client pool per account.
        Never rebuilds the client from scratch if one already exists,
        preventing repeated login/logout handshakes and connection churn.
        """
        if not hasattr(self, "_client_pool"):
            self._client_pool = {}

        if account.id in self._client_pool:
            return self._client_pool[account.id]

        cl = self._build_client(account)
        self._client_pool[account.id] = cl
        return cl

    def _build_client(self, account):
        from instagrapi import Client
        import re

        cl = Client()
        cl.delay_range = [2, 5]

        # Apply realistic unique device emulation profile (Pixel 8 Pro, S24 Ultra, Xiaomi 14, etc.)
        try:
            from apps.discovery.device_emulator import apply_device_profile_to_client
            apply_device_profile_to_client(cl, account)
        except Exception as dev_err:
            if account.device_settings:
                try:
                    cl.set_device(account.device_settings, reset=False)
                except Exception:
                    pass

        self._restore_from_mirror(account)
        if account.session_json:
            try:
                cl.set_settings(account.session_json)
                cl.username = account.username
                cookies = account.session_json.get("cookies", {})
                for k, v in cookies.items():
                    try:
                        cl.public.cookies.set(k, v, domain=".instagram.com")
                        cl.private.cookies.set(k, v, domain=".instagram.com")
                    except Exception:
                        pass
                sess_id = cookies.get("sessionid")
                if sess_id:
                    m = re.search(r"^\d+", sess_id)
                    if m:
                        uid = m.group()
                        cl.authorization_data = {
                            "ds_user_id": uid,
                            "sessionid": sess_id,
                            "should_use_header_over_cookies": True,
                        }
            except Exception as e:
                self.stderr.write(f"Warning restoring session settings for @{account.username}: {e}")

        if account.proxy_url:
            from apps.billing.manage_views import _test_proxy_connection
            proxy_ok, proxy_msg = _test_proxy_connection(account.proxy_url, timeout=2.5)
            if proxy_ok:
                cl.set_proxy(account.proxy_url)
            else:
                self.stdout.write(self.style.WARNING(f"Proxy '{account.proxy_url}' unreachable ({proxy_msg}). Falling back to direct connection."))

        return cl

    # Error kind -> (account status, rest hours). LOGIN_REQUIRED/NETWORK/OTHER are handled separately.
    _KIND_POLICY = {
        ErrorKind.CHALLENGE: ("CHALLENGED", None),
        ErrorKind.BAD_CREDENTIALS: ("CHALLENGED", None),
        ErrorKind.RATE_LIMIT: ("COOLDOWN", 2.0),
    }
    RENEW_COOLDOWN_HOURS = 6.0  # after a failed automatic re-login, wait this long before the next attempt

    @property
    def _policy(self):
        if not hasattr(self, "_policy_cache"):
            self._policy_cache = Policy.from_env()
        return self._policy_cache

    def _set_state(self, account, status: str, message: str = "", hours: float | None = None, extra=()) -> None:
        from datetime import timedelta

        account.status = status
        account.status_message = message[:250]
        account.cooldown_until = dj_timezone.now() + timedelta(hours=hours) if hours else None
        account.save(update_fields=["status", "status_message", "cooldown_until", *extra])

    def _restore_from_mirror(self, account) -> None:
        """If the DB lost the session, recover it from the atomic file mirror (never overwrites an existing DB session)."""
        if account.session_json:
            return
        data, state = load_file(session_path(account.id, self._policy))
        if data:
            account.session_json = data
            account.save(update_fields=["session_json"])
            self.stdout.write(f"    session of @{account.username} restored from file mirror")
        elif state == "corrupt":
            self.stderr.write(f"    session_storage_error: mirror file for account {account.id} is corrupt; left untouched")

    def _persist_session(self, cl, account) -> None:
        """Stores rotated cookies/tokens (only when changed) in the DB and the 0600 file mirror."""
        try:
            import json as _json
            fresh = cl.get_settings()
            if _json.dumps(fresh, sort_keys=True, default=str) != _json.dumps(account.session_json, sort_keys=True, default=str):
                account.session_json = fresh
                account.last_action_at = dj_timezone.now()
                account.save(update_fields=["session_json", "last_action_at"])
                if not save_atomic(session_path(account.id, self._policy), fresh):
                    self.stderr.write(f"    session_save_failed (file mirror) for account {account.id}")
        except Exception as exc:
            self.stderr.write(f"Notice: could not persist session for account {account.id}: {redact(exc)}")

    def _maybe_human_like(self, cl, m_id, account) -> None:
        """
        Periodically performs human-like warm-up interactions (e.g. liking a post)
        with controlled probability (~15%), natural jitter delay (3-8s), and strict daily limits (<= 15/day).
        """
        import random
        if random.random() > 0.15:
            return

        if getattr(account, "daily_action_count", 0) >= 15:
            return

        try:
            time.sleep(random.uniform(3.0, 7.5))
            cl.media_like(m_id)
            account.daily_action_count = getattr(account, "daily_action_count", 0) + 1
            account.save(update_fields=["daily_action_count"])
            self.stdout.write(self.style.SUCCESS(f"    ❤️ [HUMAN-LIKE INTERACTION] @{account.username} liked post {m_id} (Daily likes: {account.daily_action_count})"))
        except Exception as e:
            self.stdout.write(self.style.NOTICE(f"    Notice: human-like interaction skipped: {e}"))

    def _auto_renew_session(self, cl, account) -> bool:
        """
        One careful credential re-login for a session Instagram rejected, reusing the saved device/UUIDs.
        Runs only if INSTAGRAM_SESSION_AUTO_REAUTH_ENABLED is on, at most INSTAGRAM_SESSION_MAX_RECOVERY_ATTEMPTS per
        incident, under a cross-container account lock. Failure cools the account down (persisted in cooldown_until).
        """
        from datetime import timedelta

        if cl is None or not account.password:
            return False
        attempts = getattr(self, "_renew_attempts", {}).get(account.id, 0)
        if not self._policy.may_recover(ErrorKind.LOGIN_REQUIRED, attempts):
            return False
        try:
            from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired, BadPassword
        except ImportError:
            return False
        self._renew_attempts = {**getattr(self, "_renew_attempts", {}), account.id: attempts + 1}

        with account_lock(account.id, self._policy) as got:
            if not got:
                self.stdout.write(f"    recovery_skipped: another worker holds the lock for account {account.id}")
                return False
            old = account.session_json or {}
            if old.get("uuids"):
                cl.set_uuids(old["uuids"])

            def _no_challenge(username, choice=None):
                raise ChallengeRequired(f"Challenge code required via {choice}")
            cl.challenge_code_handler = _no_challenge
            try:
                self.stdout.write(self.style.WARNING(f"    recovery_started: account {account.id}"))
                time.sleep(random.uniform(8.0, 20.0))
                cl.login(account.username, account.password)
            except (ChallengeRequired, TwoFactorRequired) as exc:
                # Instagram wants a code (2FA / email / SMS): hand over to the dashboard, keep the pending challenge state.
                try:
                    account.device_settings = {"settings": cl.get_settings(), "last_json": getattr(cl, "last_json", None),
                                               "challenge_hint": redact(exc)}
                except Exception:
                    pass
                self._set_state(account, "CHALLENGED",
                                "اینستاگرام کد تایید خواست (ایمیل/پیامک/۲FA). کد را در داشبورد /manage/ وارد کنید.",
                                extra=["device_settings"])
                self.stderr.write(f"    recovery_failed: account {account.id} needs a verification code")
                return False
            except BadPassword as exc:
                self._set_state(account, "CHALLENGED", "لاگین ناموفق بود: رمز عبور رد شد. رمز را بررسی کنید.")
                self.stderr.write(f"    recovery_failed: account {account.id} bad credentials")
                return False
            except Exception as exc:
                # One try only: no further automatic logins until the operator acts.
                self._set_state(account, "CHALLENGED", f"لاگین مجدد با شکست مواجه شد: {redact(exc, 180)}")
                self.stderr.write(f"    recovery_failed: account {account.id}: {redact(exc)}")
                return False
            fresh = cl.get_settings()
            account.session_json = fresh
            self._set_state(account, "ACTIVE", "", extra=["session_json"])
            save_atomic(session_path(account.id, self._policy), fresh)
            self._renew_attempts[account.id] = 0
            self.stdout.write(self.style.SUCCESS(f"    recovery_succeeded: account {account.id}; session stored"))
            return True

    def _available_scraper_accounts(self):
        """Accounts with a stored session whose cooldown has expired; expired cooldowns are lifted."""
        now = dj_timezone.now()
        candidates = (
            InstagramEngineAccount.objects.filter(role="SCRAPER", status__in=["ACTIVE", "COOLDOWN"])
            .exclude(session_json__isnull=True)
            .order_by("last_action_at")
        )
        usable = []
        for acc in candidates:
            if acc.cooldown_until and acc.cooldown_until > now:
                continue
            if acc.status == "COOLDOWN":
                acc.status = "ACTIVE"
                acc.cooldown_until = None
                acc.save(update_fields=["status", "cooldown_until"])
            usable.append(acc)
        return usable

    def _rest_account(self, account, hours: float) -> bool:
        from datetime import timedelta

        account.status = "COOLDOWN"
        account.cooldown_until = dj_timezone.now() + timedelta(hours=hours)
        account.save(update_fields=["status", "cooldown_until"])
        return True

    def _is_usable(self, account) -> bool:
        account.refresh_from_db(fields=["status", "cooldown_until"])
        return account.status == "ACTIVE" and not (
            account.cooldown_until and account.cooldown_until > dj_timezone.now()
        )

    def _note_account_error(self, account, err_text: str, cl=None, exc=None) -> bool:
        """
        Classify the failure and react conservatively. A login is only ever considered for a rejected session
        (LOGIN_REQUIRED) and only when auto-reauth is enabled; challenges, restrictions, network and unknown errors
        never log in. Returns True when the account was taken out of rotation.
        """
        from datetime import timedelta

        kind = classify(exc if exc is not None else err_text)
        if kind is ErrorKind.LOGIN_REQUIRED:
            if self._auto_renew_session(cl, account):
                return False
            if account.status != "ACTIVE":
                return True
            if self._policy.auto_reauth:  # enabled but already tried this incident: stop, do not loop
                self._set_state(account, "CHALLENGED", "لاگین مجدد انجام شد یا ممکن نبود و سشن همچنان رد می‌شود؛ بررسی دستی لازم است.")
                return True
            self._set_state(account, "CHALLENGED", "سشن توسط اینستاگرام رد شد و لاگین خودکار خاموش است؛ sessionid جدید ثبت کنید.")
            self.stdout.write(self.style.WARNING(
                f"    session_rejected: account {account.id} paused; set a fresh sessionid (auto re-login is off)."))
            return True
        if kind in self._KIND_POLICY:
            status, hours = self._KIND_POLICY[kind]
            msgs = {ErrorKind.CHALLENGE: "اینستاگرام چالش امنیتی/تایید هویت خواست؛ کد را در داشبورد وارد کنید.",
                    ErrorKind.BAD_CREDENTIALS: "لاگین ناموفق: رمز عبور رد شد.",
                    ErrorKind.RATE_LIMIT: "محدودیت موقت اینستاگرام (۲ ساعت استراحت)."}
            self._set_state(account, status, msgs[kind], hours)
            self.stdout.write(self.style.WARNING(
                f"    {kind.value}: account {account.id} -> {status}{f' ({hours:g}h)' if hours else ''}; no login attempted."))
            return True
        return False  # network / unclassified: no state change, never a login

    def _inspect_and_process_medias(
        self,
        cl,
        account,
        target,
        medias,
        min_comments,
        seen_media_ids,
        query_label: str = "",
        check_domain_relevance: bool = True,
    ) -> tuple:
        tenant_id = target["tenant_id"]
        business_name = target["name"]
        posts_inspected = 0
        qualifying_posts = 0
        leads_saved = 0

        from datetime import timedelta
        import hashlib
        from apps.discovery.models import InstagramCrawledPost, InstagramEngineLead
        from instagram_engine.analyzer import get_effective_caption_cta, evaluate_comment_smart, detect_repeated_comment_triggers
        from instagram_engine.post_filters import is_irrelevant_post_for_business

        thirty_days_ago = dj_timezone.now() - timedelta(days=30)

        for media in medias:
            m_id = str(getattr(media, "id", "") or getattr(media, "pk", ""))
            if not m_id:
                continue
            if m_id in seen_media_ids:
                continue
            seen_media_ids.add(m_id)

            posts_inspected += 1
            code = getattr(media, "code", "")
            source_post_url = f"https://www.instagram.com/p/{code}/" if code else None
            caption_text = getattr(media, "caption_text", "") or ""
            comment_count = getattr(media, "comment_count", 0) or 0

            # 1. Age Pre-Filter: Strictly reject posts published more than 30 days ago
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
            post_hash = hashlib.sha256(f"ig_post:{m_id}".encode("utf-8")).hexdigest()
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

            # Domain Relevance Filter: Skip posts outside the business domain
            if check_domain_relevance:
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
                    media_id=m_id,
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

            qualifying_posts += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"    🎯 [QUALIFYING POST for {query_label}] {source_post_url} | Comments: {comment_count} (>= {min_comments}) | Hash: {post_hash[:10]}"
                )
            )

            # Human-like interaction (organic like telemetry)
            self._maybe_human_like(cl, m_id, account)

            cta_trigger = get_effective_caption_cta(caption_text)
            fetch_amount = min(comment_count, 100)
            comments = []
            try:
                comments = cl.media_comments(m_id, amount=fetch_amount)
            except Exception as exc:
                try:
                    comments = cl.media_comments_gql(m_id, amount=fetch_amount)
                except Exception:
                    self.stderr.write(f"    Failed to fetch comments for {source_post_url}: {exc}")
                    continue

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

                eval_res = evaluate_comment_smart(
                    comment_text=comment_text,
                    caption=caption_text,
                    cta_word=cta_trigger,
                    product_context=business_name,
                    repeated_triggers=repeated_triggers,
                )

                if eval_res.get("is_emoji"):
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
                    leads_saved += 1
                    lead_type_label = "⚡ مستقیم (Zero Cost)" if eval_res.get("is_direct_cta") else "🤖 هوش مصنوعی (Luna GPT-6)"
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"      ⭐ Lead Discovered ({lead_type_label}): @{username} (score: {score}) | '{comment_text[:50]}'"
                        )
                    )

                try:
                    from apps.discovery.services import sync_instagram_lead_to_opportunity
                    sync_instagram_lead_to_opportunity(lead, business=target.get("business"), ai_meta=eval_res)
                except Exception as sync_exc:
                    self.stderr.write(f"Notice: Opportunity sync skipped: {sync_exc}")

            time.sleep(random.uniform(4.0, 7.0))

        return posts_inspected, qualifying_posts, leads_saved

    def _request_budget_available(self, account) -> bool:
        from django.db.models import Sum
        from apps.discovery.instagram_families import INSTAGRAM_DAILY_REQUEST_BUDGET
        from apps.discovery.models import InstagramCrawlExecution
        used = InstagramCrawlExecution.objects.filter(account=account, crawled_at__date=dj_timezone.localdate()).aggregate(
            total=Sum("requests"))['total'] or 0
        used += int(getattr(self, "_current_api_requests", 0))
        return used < INSTAGRAM_DAILY_REQUEST_BUDGET

    def _instagram_api_call(self, account, method, *args, **kwargs):
        if not self._request_budget_available(account):
            raise RuntimeError("سقف درخواست روزانهٔ اکانت اینستاگرام تکمیل شد")
        self._current_api_requests = int(getattr(self, "_current_api_requests", 0)) + 1
        return method(*args, **kwargs)

    def _discover_candidate_pages(self, cl, account, business, family, plan, source):
        """Search real public accounts, score their recent posts, and cache at most six family candidates."""
        from datetime import timedelta
        from apps.discovery.instagram_families import INSTAGRAM_PAGE_MIN_COMMENTS
        from apps.discovery.models import InstagramPage

        term = source.label.removeprefix("search:").strip()
        if not term or not hasattr(cl, "search_users"):
            return []
        last_search = business.instagram_crawl_executions.filter(target_type="PAGE", query_text=f"search:{term}",
                                                                  crawled_at__gte=dj_timezone.now() - timedelta(days=7)).exists()
        if last_search:
            return []
        try:
            candidates = self._instagram_api_call(account, cl.search_users, term) or []
        except Exception as exc:
            self._page_search_error = str(exc)
            self.stderr.write(f"Page search failed for {term}: {exc}")
            return []
        terms = ["فروش", "فروشگاه", "عمده", "تولیدی", "کلاس", "آموزش", "آموزشگاه", "کلینیک", "سالن", "مشاوره", "shop", "store", "academy", "clinic"]
        checked, passing = set(), []
        for user in candidates[:8]:
            handle = str(getattr(user, "username", "") or "").strip().lstrip("@")
            uid = str(getattr(user, "pk", "") or getattr(user, "id", ""))
            if not handle or not uid or handle.casefold() in checked:
                continue
            checked.add(handle.casefold())
            if getattr(user, "is_private", None) is not False:
                continue
            bio = str(getattr(user, "biography", "") or "")
            account_name = f"{handle} {getattr(user, 'full_name', '')} {bio}".casefold()
            business_like = bool(getattr(user, "is_business", False)) or any(t in account_name for t in terms)
            if not business_like:
                continue
            try:
                media = self._instagram_api_call(account, cl.user_medias, uid, amount=6) or []
            except Exception:
                continue
            recent_comments = []
            cutoff = dj_timezone.now() - timedelta(days=14)
            for post in media:
                posted = getattr(post, "taken_at", None)
                if posted and dj_timezone.is_naive(posted):
                    posted = dj_timezone.make_aware(posted, dj_timezone.utc)
                if posted and posted < cutoff:
                    continue
                recent_comments.append(int(getattr(post, "comment_count", 0) or 0))
            if len(recent_comments) < 2:
                continue
            average = sum(recent_comments) / len(recent_comments) if recent_comments else 0
            if average < INSTAGRAM_PAGE_MIN_COMMENTS:
                continue
            passing.append((average, handle, bio))
        passing.sort(reverse=True)
        created = []
        for rank, (average, handle, bio) in enumerate(passing[:6]):
            page, was_created = InstagramPage.objects.get_or_create(
                business=business, handle=handle,
                defaults={"kind": "watched", "is_active": rank < 2, "last_status": "candidate",
                          "discovered_family_id": family.product_id},
            )
            if was_created:
                created.append(page)
            elif not page.is_active and page.discovered_family_id == family.product_id and page.last_status in {"", "candidate"}:
                page.last_status = "candidate"
                page.save(update_fields=["last_status"])
        return created

    def _inspect_family_medias(self, cl, account, target, source, medias, min_comments: int):
        """Cache each post/comments once globally, then score the cached comments for this tenant/family."""
        import hashlib
        from datetime import timedelta
        from apps.discovery.models import InstagramCrawledPost, InstagramEngineLead
        from instagram_engine.analyzer import (extract_caption_cta, evaluate_comment_smart,
                                                llm_evaluate_family_batch)
        from apps.discovery.instagram_families import INSTAGRAM_PAGE_MIN_COMMENTS, lead_quality_score

        business, family, plan = target["business"], target["family"], target["plan"]
        page_source = source.source_type == "page"
        age_days = 14 if page_source else 7
        cutoff = dj_timezone.now() - timedelta(days=age_days)
        posts_inspected = qualifying_posts = leads_saved = 0
        seen = set()
        buyer_signals, negatives = list(plan.buyer_signals or []), list(plan.negative_signals or [])
        family_context = {"category_path": family.category_path, "product_titles": family.titles[:10],
                          "seller_notes": family.notes[:1000], "business_domain": business.business_domain}
        for media in medias or []:
            media_id = str(getattr(media, "id", "") or getattr(media, "pk", ""))
            if not media_id or media_id in seen:
                continue
            seen.add(media_id)
            taken_at = getattr(media, "taken_at", None)
            if taken_at and dj_timezone.is_naive(taken_at):
                taken_at = dj_timezone.make_aware(taken_at, dj_timezone.utc)
            if not taken_at or taken_at < cutoff:
                continue
            comment_count = int(getattr(media, "comment_count", 0) or 0)
            effective_min = INSTAGRAM_PAGE_MIN_COMMENTS if page_source else min_comments
            if comment_count < effective_min:
                continue
            posts_inspected += 1
            digest = hashlib.sha256(f"ig_post:{media_id}".encode()).hexdigest()
            code = str(getattr(media, "code", "") or "")
            cached = InstagramCrawledPost.objects.filter(post_hash=digest).first()
            caption = str(getattr(media, "caption_text", "") or (cached.caption if cached else ""))
            if cached and cached.comments_cached:
                raw_comments = list(cached.comments)
            else:
                try:
                    raw = self._instagram_api_call(account, cl.media_comments, media_id, amount=min(comment_count, 100))
                except Exception:
                    try:
                        raw = self._instagram_api_call(account, cl.media_comments_gql, media_id, amount=min(comment_count, 100))
                    except Exception as exc:
                        self.stderr.write(f"Comment fetch failed for {code or media_id}: {exc}")
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
                    raw_comments.append({
                        "id": str(getattr(comment, "pk", "") or getattr(comment, "id", "") or user_pk),
                        "user_pk": user_pk, "username": username,
                        "full_name": str(getattr(user, "full_name", "") or username), "text": text[:1500],
                        "is_business": getattr(user, "is_business", None),
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
                if cached and not cached.comments_cached:
                    cached.caption, cached.comments, cached.comment_count, cached.comments_cached = caption, raw_comments, comment_count, True
                    cached.save(update_fields=["caption", "comments", "comment_count", "comments_cached"])
            if not raw_comments:
                continue
            qualifying_posts += 1
            post_url = f"https://www.instagram.com/p/{code}/" if code else ""
            cta = extract_caption_cta(caption)
            evaluations, ambiguous = {}, []
            for row in raw_comments:
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
                    self.stdout.write(self.style.WARNING(
                        f"Skipping Instagram LLM evaluation for tenant {business.pk}: اعتبار تمام شده"
                    ))
                    break
                ai.update(llm_evaluate_family_batch(ambiguous[offset:offset + 25], business_id=business.pk,
                                                    family_context=family_context, buyer_signals=buyer_signals))
            evaluations.update(ai)
            for row in raw_comments:
                result = evaluations.get(row["id"], {})
                if not result.get("is_lead"):
                    continue
                leads_saved += 1
                score = lead_quality_score(result.get("intent_score", .8), is_business=row.get("is_business"),
                                           follower_count=row.get("follower_count"), bio=row.get("bio", ""),
                                           seller_notes=family.notes, family_path=family.category_path,
                                           product_titles=family.titles)
                lead, created = InstagramEngineLead.objects.get_or_create(
                    tenant_id=target["tenant_id"], ig_user_pk=row["user_pk"],
                    defaults={"username": row["username"], "full_name": row["full_name"],
                              "profile_url": f"https://www.instagram.com/{row['username']}/",
                              "comment_text": row["text"], "source_post_url": post_url,
                              "post_caption_snippet": caption[:600], "matched_cta": result.get("matched_cta", ""),
                              "intent_score": score, "status": "NEW",
                              "profile_bio": row.get("bio", ""), "commenter_is_business": row.get("is_business"),
                              "follower_count": row.get("follower_count")},
                )
                from apps.discovery.services import sync_instagram_lead_to_opportunity
                sync_instagram_lead_to_opportunity(lead, business=business, ai_meta=result)
        return posts_inspected, qualifying_posts, leads_saved

    def _scrape_family_target(self, cl, account, target, min_comments, max_posts):
        from apps.discovery.models import InstagramCrawlExecution, InstagramPage, InstagramSourceStat
        from apps.discovery.instagram_families import (DiscoverySource, INSTAGRAM_DAILY_REQUEST_BUDGET,
                                                         record_source_result)

        total_leads = 0
        from apps.billing.services import is_blocked
        try:
            if is_blocked(target["business"]):
                self.stdout.write(self.style.WARNING(
                    f"Skipping Instagram family for tenant {target['business'].pk}: اعتبار تمام شده"
                ))
                return 0
        except Exception as exc:
            self.stderr.write(f"Skipping Instagram family: wallet status unavailable ({type(exc).__name__})")
            return 0
        page_searches = 0
        selected_report = []
        for source in target["sources"]:
            stat = InstagramSourceStat.objects.filter(business=target["business"], family_id=source.family_id,
                                                       source_type=source.source_type, label=source.label[:100]).first()
            selected_report.append({"type": source.source_type, "label": source.label,
                                    "requests": stat.requests if stat else 0, "leads": stat.leads if stat else 0})
        self.stdout.write(f"Selected sources for family {target['family'].product_id}: {selected_report}")
        for source in target["sources"]:
            self._current_api_requests = 0
            self._page_search_error = ""
            if is_blocked(target["business"]):
                self.stdout.write(self.style.WARNING(
                    f"Stopping Instagram sources for tenant {target['business'].pk}: اعتبار تمام شده"
                ))
                break
            if account.status != "ACTIVE" or not self._request_budget_available(account):
                break
            error = ""
            medias = []
            actual_source = source
            try:
                if source.source_type == "page" and source.label.startswith("search:"):
                    page_searches += 1
                    if page_searches > max(1, int(os.getenv("INSTAGRAM_PAGE_SEARCH_TERMS_PER_CYCLE", "1"))):
                        continue
                    created = self._discover_candidate_pages(cl, account, target["business"], target["family"],
                                                             target["plan"], source)
                    execution_type, query = "PAGE", source.label
                    leads = 0
                    search_status = "FAILED" if self._page_search_error else "SUCCESS"
                    record_source_result(target["business"], source, leads, search_status)
                    InstagramCrawlExecution.objects.create(
                        business=target["business"], tenant_id=target["tenant_id"], query_text=query,
                        target_type=execution_type, account_username=account.username, account=account,
                        status=search_status, posts_inspected=len(created), requests=self._current_api_requests,
                        message=f"صفحه‌های واقعی بررسی‌شده: {len(created)}",
                    )
                    continue
                if source.source_type == "keyword":
                    medias = self._instagram_api_call(account, cl.media_search, source.label, amount=max_posts) or []
                    execution_type, query = "KEYWORD", source.label
                elif source.source_type == "hashtag":
                    tag = source.label.lstrip("#")
                    medias = list(self._instagram_api_call(account, cl.hashtag_medias_top, tag, amount=max_posts) or [])
                    if self._request_budget_available(account):
                        medias += list(self._instagram_api_call(account, cl.hashtag_medias_recent, tag, amount=max_posts) or [])
                    execution_type, query = "HASHTAG", f"#{tag}"
                else:
                    page = source.page or InstagramPage.objects.filter(business=target["business"], handle__iexact=source.label).first()
                    if page is None:
                        continue
                    uid = self._instagram_api_call(account, cl.user_id_from_username, page.handle)
                    medias = self._instagram_api_call(account, cl.user_medias, uid, amount=max_posts) or []
                    page.last_run_at, page.last_status, page.consecutive_failures = dj_timezone.now(), "ok", 0
                    page.save(update_fields=["last_run_at", "last_status", "consecutive_failures"])
                    execution_type, query = "PAGE", f"@{page.handle}"
            except Exception as exc:
                error = str(exc)
                self._note_account_error(account, error, cl, exc)
                execution_type = {"keyword": "KEYWORD", "hashtag": "HASHTAG", "page": "PAGE"}[source.source_type]
                query = source.label
            inspected, qualifying, leads = self._inspect_family_medias(cl, account, target, source, medias, min_comments)
            total_leads += leads
            status = "FAILED" if error and not medias else ("SUCCESS" if qualifying else "NO_POSTS")
            record_source_result(target["business"], source, leads, status)
            InstagramCrawlExecution.objects.create(
                business=target["business"], tenant_id=target["tenant_id"], query_text=query,
                target_type=execution_type, account_username=account.username, account=account,
                status=status, posts_inspected=inspected, qualifying_posts=qualifying, leads_found=leads,
                requests=self._current_api_requests,
                message=f"خانواده {target['family'].product_id}: بررسی {inspected} پست؛ {leads} سرنخ. {error[:180]}",
            )
            time.sleep(random.uniform(3.0, 7.0))
        return total_leads

    def _scrape_target(self, cl, account, target, min_comments, max_posts) -> int:
        tenant_id = target["tenant_id"]
        business_name = target["name"]
        tags_to_scrape = list(target.get("hashtags") or [])
        terms_to_scrape = list(target.get("terms") or [])
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

        total_posts_inspected = 0
        total_qualifying_posts = 0
        total_leads_saved = 0
        seen_media_ids = set()

        from shared.mmr import select_by_mmr
        from apps.discovery.models import InstagramCrawlExecution

        # ==========================================
        # 1. KEYWORD SEARCH (Media Search SERP - No Hashtag)
        # ==========================================
        if terms_to_scrape:
            from apps.discovery.ig_query_scoring import select_scored
            selected_terms = select_scored(
                terms_to_scrape, 3, "KEYWORD",
                lambda items, k: select_by_mmr(items, lambda t: t, reference_context=f"{business_name} {query}",
                                               top_k=k, lambda_param=0.65),
            )
        else:
            selected_terms = [query] if query else []

        self.stdout.write(f"Keywords to search: {terms_to_scrape}")
        self.stdout.write(f"History-scored keywords for this cycle: {selected_terms}")

        for term in selected_terms:
            if account.status != "ACTIVE":
                break
            self.stdout.write(self.style.WARNING(f"\n  [🔍] Keyword Search: '{term}'"))
            term_err = ""
            term_medias = []
            try:
                term_medias = cl.media_search(term, amount=max_posts)
                if term_medias:
                    self.stdout.write(f"    Retrieved {len(term_medias)} posts for keyword '{term}'.")
            except Exception as exc:
                term_err = str(exc)
                if classify(exc) is ErrorKind.LOGIN_REQUIRED and account.status == "ACTIVE" and not self._note_account_error(account, term_err, cl, exc):
                    try:   # read-only search: retry exactly once after a successful recovery
                        term_medias = cl.media_search(term, amount=max_posts)
                        term_err = ""
                    except Exception as retry_exc:
                        term_err = str(retry_exc)
                        self._note_account_error(account, term_err, cl, retry_exc) if classify(retry_exc) is not ErrorKind.LOGIN_REQUIRED else None
                else:
                    self._note_account_error(account, term_err, cl, exc)
                if term_err:
                    self.stderr.write(f"    Notice: media_search error for '{term}': {term_err}")

            p_insp, p_qual, l_saved = self._inspect_and_process_medias(
                cl=cl,
                account=account,
                target=target,
                medias=term_medias,
                min_comments=min_comments,
                seen_media_ids=seen_media_ids,
                query_label=f"keyword '{term}'",
                check_domain_relevance=True,
            )
            total_posts_inspected += p_insp
            total_qualifying_posts += p_qual
            total_leads_saved += l_saved

            exec_status = "SUCCESS" if p_qual > 0 else ("NO_POSTS" if p_insp == 0 else "SUCCESS")
            if term_err and not term_medias:
                exec_status = "FAILED"
            msg_val = (
                f"جستجوی متنی '{term}': بررسی {p_insp} پست، {p_qual} پست حائز شرایط (کامنت >= {min_comments})، {l_saved} لید جدید کشف شد."
                if not (term_err and not term_medias)
                else f"خطا در جستجوی کلیدواژه '{term}': {term_err}"
            )
            try:
                InstagramCrawlExecution.objects.create(
                    business=target.get("business"),
                    tenant_id=tenant_id,
                    query_text=term,
                    target_type="KEYWORD",
                    account_username=account.username,
                    account=account,
                    status=exec_status,
                    posts_inspected=p_insp,
                    qualifying_posts=p_qual,
                    leads_found=l_saved,
                    message=msg_val,
                    crawled_at=dj_timezone.now(),
                )
                self.stdout.write(self.style.SUCCESS(f"    📝 [Crawl Log Saved]: Keyword '{term}' by @{account.username} ({exec_status})"))
            except Exception as log_exc:
                self.stderr.write(f"Notice: Failed to save InstagramCrawlExecution for keyword '{term}': {log_exc}")

            time.sleep(random.uniform(3.0, 6.0))

        # ==========================================
        # 2. HASHTAG SEARCH (Top & Recent Posts)
        # ==========================================
        self.stdout.write(f"Hashtags to inspect: {tags_to_scrape}")
        from apps.discovery.ig_query_scoring import select_scored
        selected_tags = select_scored(
            tags_to_scrape, 4, "HASHTAG",
            lambda items, k: select_by_mmr(items, lambda t: t.replace("_", " "), reference_context=f"{business_name} {query}",
                                           top_k=k, lambda_param=0.65),
            label_of=lambda t: t if t.startswith("#") else f"#{t}",
        )
        self.stdout.write(f"History-scored hashtags for this cycle: {selected_tags}")

        for tag in selected_tags:
            if account.status != "ACTIVE":
                break
            self.stdout.write(self.style.WARNING(f"\n  [#] Hashtag: #{tag}"))
            tag_err = ""
            tag_medias = []

            # 1. Fetch Top / Viral Posts
            try:
                top_medias = cl.hashtag_medias_top(tag, amount=max_posts)
            except Exception as top_exc:
                try:
                    top_medias = cl.hashtag_medias_top_v1(tag, amount=max_posts)
                except Exception as top_err:
                    self._note_account_error(account, str(top_err), cl, top_err)
                    top_medias = []
            for m in top_medias:
                if str(getattr(m, "id", "") or getattr(m, "pk", "")) not in seen_media_ids:
                    tag_medias.append(m)
            if top_medias:
                self.stdout.write(f"    Retrieved {len(top_medias)} TOP posts for #{tag}.")

            # 2. Fetch Recent Posts
            try:
                recent_medias = cl.hashtag_medias_recent(tag, amount=max_posts)
            except Exception:
                try:
                    recent_medias = cl.hashtag_medias_recent_v1(tag, amount=max_posts)
                except Exception as recent_err:
                    self._note_account_error(account, str(recent_err), cl, recent_err)
                    recent_medias = []
            for m in recent_medias:
                if str(getattr(m, "id", "") or getattr(m, "pk", "")) not in seen_media_ids:
                    tag_medias.append(m)
            if recent_medias:
                self.stdout.write(f"    Retrieved {len(recent_medias)} RECENT posts for #{tag}.")

            # 3. Fallback to Web GraphQL if private mobile API returns no posts
            if not tag_medias:
                try:
                    gql_medias, _ = cl.hashtag_medias_paginated_gql(tag, amount=max_posts)
                    for m in gql_medias:
                        if str(getattr(m, "id", "") or getattr(m, "pk", "")) not in seen_media_ids:
                            tag_medias.append(m)
                    if gql_medias:
                        self.stdout.write(f"    Retrieved {len(gql_medias)} GQL posts for #{tag}.")
                except Exception as gql_exc:
                    self.stderr.write(f"    Notice: hashtag_medias_paginated_gql warning for #{tag}: {gql_exc}")

            p_insp, p_qual, l_saved = self._inspect_and_process_medias(
                cl=cl,
                account=account,
                target=target,
                medias=tag_medias,
                min_comments=min_comments,
                seen_media_ids=seen_media_ids,
                query_label=f"hashtag #{tag}",
                check_domain_relevance=True,
            )
            total_posts_inspected += p_insp
            total_qualifying_posts += p_qual
            total_leads_saved += l_saved

            exec_status = "SUCCESS" if p_qual > 0 else ("NO_POSTS" if p_insp == 0 else "SUCCESS")
            if tag_err and not tag_medias:
                exec_status = "FAILED"
            msg_val = (
                f"بررسی {p_insp} پست، {p_qual} پست حائز شرایط (کامنت >= {min_comments})، {l_saved} لید جدید کشف شد."
                if not (tag_err and not tag_medias)
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
                    posts_inspected=p_insp,
                    qualifying_posts=p_qual,
                    leads_found=l_saved,
                    message=msg_val,
                    crawled_at=dj_timezone.now(),
                )
                self.stdout.write(self.style.SUCCESS(f"    📝 [Crawl Log Saved]: #{tag} by @{account.username} ({exec_status})"))
            except Exception as log_exc:
                self.stderr.write(f"Notice: Failed to save InstagramCrawlExecution for #{tag}: {log_exc}")

            time.sleep(random.uniform(3.0, 6.0))

        # ==========================================
        # 3. COMPETITOR & TARGET PAGES
        # ==========================================
        if target.get("business"):
            from apps.discovery.models import InstagramPage
            comp_pages = InstagramPage.objects.filter(business=target["business"], is_active=True)
            for page in comp_pages:
                if account.status != "ACTIVE":
                    break
                handle = page.handle.lstrip("@").strip()
                self.stdout.write(self.style.WARNING(f"\n  [👤] Scraper @{account.username} inspecting competitor page: @{handle}"))
                page_err = ""
                page_medias = []
                try:
                    uid = cl.user_id_from_username(handle)
                    page_medias = cl.user_medias(uid, amount=max_posts)
                except Exception as exc:
                    page_err = str(exc)
                    self._note_account_error(account, page_err, cl, exc)
                    self.stderr.write(f"    Notice: Error fetching medias from @{handle}: {exc}")

                p_insp, p_qual, l_saved = self._inspect_and_process_medias(
                    cl=cl,
                    account=account,
                    target=target,
                    medias=page_medias,
                    min_comments=min_comments,
                    seen_media_ids=seen_media_ids,
                    query_label=f"competitor page @{handle}",
                    check_domain_relevance=False,
                )
                total_posts_inspected += p_insp
                total_qualifying_posts += p_qual
                total_leads_saved += l_saved

                page_status = "SUCCESS" if p_qual > 0 else ("NO_POSTS" if p_insp == 0 else "SUCCESS")
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
                        posts_inspected=p_insp,
                        qualifying_posts=p_qual,
                        leads_found=l_saved,
                        message=f"بررسی {p_insp} پست از صفحه @{handle}، {p_qual} پست حائز شرایط، {l_saved} سرنخ ثبت شد." if not (page_err and not page_medias) else f"خطای دسترسی: {page_err}",
                        crawled_at=dj_timezone.now(),
                    )
                    self.stdout.write(self.style.SUCCESS(f"    📝 [Crawl Log Saved]: @{handle} by @{account.username} ({page_status})"))
                except Exception as log_exc:
                    self.stderr.write(f"Notice: Failed to log InstagramCrawlExecution for @{handle}: {log_exc}")

                time.sleep(random.uniform(3.0, 6.0))

        # Update account action count
        account.daily_action_count += total_qualifying_posts
        account.last_action_at = dj_timezone.now()
        account.save(update_fields=["daily_action_count", "last_action_at"])

        return total_leads_saved

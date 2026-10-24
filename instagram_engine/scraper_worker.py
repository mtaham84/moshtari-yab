import random
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional

try:
    from instagrapi import Client
except ImportError:
    Client = Any  # type: ignore

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from instagram_engine.models import (
    IGAccount,
    AccountRole,
    AccountStatus,
    Lead,
    LeadStatus,
    SessionLocal,
)
from instagram_engine.config import settings
from instagram_engine.session_manager import SessionManager
from instagram_engine.circuit_breaker import handle_instagram_exceptions, CircuitBreakerTriggered
from instagram_engine.analyzer import (
    get_effective_caption_cta,
    is_buyer_comment,
    llm_evaluate_batch,
)

logger = logging.getLogger("instagram_engine.scraper_worker")


class ScraperWorker:
    """
    Dedicated worker for Scraper Pool (Burner Accounts).
    Scrapes hashtags, posts, and comments without ever calling user_info to avoid rate limits.
    """

    def __init__(self, tenant_id: Optional[str] = None):
        self.tenant_id = tenant_id or settings.default_tenant_id
        self.account: Optional[IGAccount] = None
        self.client: Optional[Client] = None

    def acquire_scraper_account(self) -> bool:
        """Fetches an active, available SCRAPER account from the database."""
        db: Session = SessionLocal()
        try:
            now_utc = datetime.now(timezone.utc)
            # Find scraper account with active status and no active cooldown
            account = (
                db.query(IGAccount)
                .filter(
                    IGAccount.tenant_id == self.tenant_id,
                    IGAccount.role == AccountRole.SCRAPER,
                    IGAccount.status == AccountStatus.ACTIVE,
                    (IGAccount.cooldown_until == None) | (IGAccount.cooldown_until <= now_utc),
                )
                .order_by(IGAccount.last_action_at.asc().nullsfirst())
                .first()
            )

            if not account:
                logger.warning("No available SCRAPER account found for tenant '%s'!", self.tenant_id)
                return False

            self.account = account
            # Check daily quota
            if self.account.daily_action_count >= settings.max_scraper_post_comments_per_day:
                logger.warning(
                    "Scraper @%s has reached daily comment quota (%d/%d). Putting in short cooldown.",
                    self.account.username,
                    self.account.daily_action_count,
                    settings.max_scraper_post_comments_per_day,
                )
                self.account.cooldown_until = now_utc + timedelta(hours=4)
                db.commit()
                return False

            # Initialize Client
            mgr = SessionManager(account)
            self.client = mgr.get_client()
            return True
        except Exception as exc:
            logger.error("Error acquiring scraper account: %s", exc)
            return False
        finally:
            db.close()

    def expand_query_to_hashtags(self, query: str) -> List[str]:
        """
        Takes a raw Persian search query (e.g., 'لباس مجلسی'), normalizes spaces to underscores,
        and optionally queries Instagram hashtag search to pick the top active hashtags.
        """
        normalized_direct = query.strip().replace(" ", "_")
        hashtags = [normalized_direct]

        if not self.client:
            return hashtags

        try:
            logger.info("Searching related hashtags for query: '%s'...", query)
            search_results = self.client.search_hashtags(query)
            for h in search_results:
                name = getattr(h, "name", None) or getattr(h, "id", None)
                if name and name not in hashtags:
                    hashtags.append(name)
                if len(hashtags) >= 3:
                    break
        except Exception as exc:
            logger.warning("Hashtag search API failed for '%s' (%s). Using direct normalized hashtag.", query, exc)

        return hashtags

    @handle_instagram_exceptions
    def fetch_recent_medias(self, hashtag: str, amount: int = 15):
        """Fetches recent medias for a hashtag."""
        logger.info("Fetching top %d recent medias for hashtag #%s...", amount, hashtag)
        return self.client.hashtag_medias_recent(hashtag, amount=amount)

    @handle_instagram_exceptions
    def fetch_media_comments(self, media_id: str, amount: int = 50):
        """
        Fetches up to `amount` comments for a media post.
        Note: Adheres strictly to Zero Extra API Call Rule.
        """
        return self.client.media_comments(media_id, amount=amount)

    def process_query(self, search_query: str) -> int:
        """
        Executes a complete scraping cycle for a search query across hashtags,
        evaluates comments using multi-tier analyzer, and saves qualified leads.
        """
        if not self.account or not self.client:
            if not self.acquire_scraper_account():
                logger.error("Cannot process query: no active scraper client available.")
                return 0

        hashtags = self.expand_query_to_hashtags(search_query)
        logger.info("Target hashtags to scrape: %s", hashtags)

        total_new_leads = 0
        hashtag_count = 0

        for tag in hashtags:
            # Check hard hourly/daily cap
            if hashtag_count >= settings.max_scraper_hashtags_per_hour:
                logger.info("Reached hourly hashtag limit (%d). Pausing hashtag loop.", settings.max_scraper_hashtags_per_hour)
                break

            try:
                medias = self.fetch_recent_medias(tag, amount=15)
                hashtag_count += 1
            except CircuitBreakerTriggered:
                logger.error("Circuit breaker tripped during hashtag fetch. Switching scraper account.")
                return total_new_leads
            except Exception as exc:
                logger.error("Failed to fetch medias for #%s: %s", tag, exc)
                continue

            for media in medias:
                media_id = str(media.id)
                caption_text = media.caption_text if hasattr(media, "caption_text") else ""
                source_post_url = f"https://www.instagram.com/p/{media.code}/" if hasattr(media, "code") else None

                # Step 1: Extract post CTA trigger word (Tier 1A / Tier 1B)
                cta_trigger = get_effective_caption_cta(caption_text)
                if cta_trigger:
                    logger.info("Found CTA trigger word in post %s: '%s'", source_post_url, cta_trigger)

                # Step 2: Fetch comments
                try:
                    comments = self.fetch_media_comments(media_id, amount=50)
                except CircuitBreakerTriggered:
                    logger.error("Circuit breaker tripped during comment fetch.")
                    return total_new_leads
                except Exception as exc:
                    logger.warning("Failed to fetch comments for media %s: %s", media_id, exc)
                    continue

                # Step 3: Analyze comments and extract leads (Zero Extra API Call Rule)
                unmatched_comments_for_llm = []

                for comment in comments:
                    user_obj = getattr(comment, "user", None)
                    if not user_obj:
                        continue

                    # STRICT ZERO EXTRA API CALL RULE:
                    # Extract pk, username, and full_name directly from comment.user object.
                    user_pk = str(getattr(user_obj, "pk", "") or getattr(user_obj, "id", ""))
                    username = getattr(user_obj, "username", "")
                    full_name = getattr(user_obj, "full_name", "") or username
                    comment_text = getattr(comment, "text", "")

                    if not user_pk or not username or not comment_text:
                        continue

                    # Tier 2 intent filter
                    is_lead, match_reason = is_buyer_comment(comment_text, cta_word=cta_trigger)

                    if is_lead:
                        score = 0.95 if match_reason and "cta_match" in match_reason else 0.90
                        saved = self._save_lead(
                            ig_user_pk=user_pk,
                            username=username,
                            full_name=full_name,
                            comment_text=comment_text,
                            source_post_url=source_post_url,
                            post_caption_snippet=caption_text[:300] if caption_text else None,
                            matched_cta=match_reason,
                            intent_score=score,
                        )
                        if saved:
                            total_new_leads += 1
                    else:
                        unmatched_comments_for_llm.append({
                            "id": str(getattr(comment, "pk", "")),
                            "text": comment_text,
                            "user_pk": user_pk,
                            "username": username,
                            "full_name": full_name,
                        })

                # Tier 3: LLM Batch Evaluation for non-keyword comments (e.g. pain points/questions)
                if unmatched_comments_for_llm and settings.llm_api_key:
                    evaluated_leads = llm_evaluate_batch(caption_text, unmatched_comments_for_llm)
                    for item in evaluated_leads:
                        saved = self._save_lead(
                            ig_user_pk=item["user_pk"],
                            username=item["username"],
                            full_name=item["full_name"],
                            comment_text=item["text"],
                            source_post_url=source_post_url,
                            post_caption_snippet=caption_text[:300] if caption_text else None,
                            matched_cta="tier3_llm_qualified",
                            intent_score=0.80,
                        )
                        if saved:
                            total_new_leads += 1

                # Update daily counter and timestamp on account
                self._record_scraper_action()

                # Pacing between posts: 10 to 25 seconds
                post_sleep = random.uniform(settings.scraper_post_sleep_min, settings.scraper_post_sleep_max)
                logger.debug("Sleeping %.1f seconds between post comment fetches...", post_sleep)
                time.sleep(post_sleep)

            # Pacing between hashtags: 45 to 120 seconds
            tag_sleep = random.uniform(settings.scraper_hashtag_sleep_min, settings.scraper_hashtag_sleep_max)
            logger.info("Finished hashtag #%s. Sleeping %.1f seconds before next hashtag...", tag, tag_sleep)
            time.sleep(tag_sleep)

        logger.info("Scraper cycle complete. Discovered and stored %d new qualified leads.", total_new_leads)
        return total_new_leads

    def _save_lead(
        self,
        ig_user_pk: str,
        username: str,
        full_name: str,
        comment_text: str,
        source_post_url: Optional[str],
        post_caption_snippet: Optional[str],
        matched_cta: Optional[str],
        intent_score: float = 0.85,
    ) -> bool:
        """
        Inserts lead into database using ON CONFLICT DO NOTHING to strictly ensure
        the unique constraint (tenant_id, ig_user_pk) is enforced without exceptions.
        """
        db: Session = SessionLocal()
        try:
            profile_url = f"https://www.instagram.com/{username}/"
            stmt = (
                insert(Lead)
                .values(
                    tenant_id=self.tenant_id,
                    ig_user_pk=ig_user_pk,
                    username=username,
                    full_name=full_name,
                    profile_url=profile_url,
                    comment_text=comment_text,
                    source_post_url=source_post_url,
                    post_caption_snippet=post_caption_snippet,
                    matched_cta=matched_cta,
                    intent_score=intent_score,
                    status=LeadStatus.NEW,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
                .on_conflict_do_nothing(index_elements=["tenant_id", "ig_user_pk"])
            )
            result = db.execute(stmt)
            db.commit()
            if result.rowcount > 0:
                logger.info("🎯 [NEW LEAD DISCOVERED] @%s (PK: %s) via %s: '%s'", username, ig_user_pk, matched_cta, comment_text[:50])
                return True
            else:
                logger.debug("Lead @%s already exists for tenant '%s'. Skipped deduplication.", username, self.tenant_id)
                return False
        except Exception as exc:
            db.rollback()
            logger.error("Failed to save lead @%s: %s", username, exc)
            return False
        finally:
            db.close()

    def _record_scraper_action(self) -> None:
        """Increments action count and updates timestamp."""
        if not self.account:
            return
        db: Session = SessionLocal()
        try:
            acc = db.query(IGAccount).filter(IGAccount.id == self.account.id).first()
            if acc:
                acc.daily_action_count += 1
                acc.last_action_at = datetime.now(timezone.utc)
                db.commit()
                self.account.daily_action_count = acc.daily_action_count
        except Exception as exc:
            db.rollback()
            logger.error("Failed to record scraper action in DB: %s", exc)
        finally:
            db.close()

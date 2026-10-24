import re
import random
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
import pytz

try:
    from instagrapi import Client
except ImportError:
    Client = Any  # type: ignore

from sqlalchemy.orm import Session

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
    _call_llm_completion,
    validate_and_sanitize_dm,
    URL_REGEX,
    MENTION_REGEX,
    PHONE_REGEX,
)

logger = logging.getLogger("instagram_engine.outreach_worker")



def generate_personalized_dm(
    full_name: str,
    comment_text: str,
    post_caption_snippet: Optional[str] = None,
    business_context: str = "پشتیبانی و مشاوره خرید",
) -> str:
    """
    Generates a natural, conversational Persian Cold DM addressing the user's specific comment.
    Goal: Elicit a natural reply from the lead to move thread out of Instagram Message Requests.
    """
    clean_name = full_name.split()[0] if full_name else "عزیز"

    prompt = (
        f"شما دستیار حرفه‌ای ارتباط با مشتری در اینستاگرام هستید ({business_context}).\n"
        f"کاربر با نام «{clean_name}» زیر پست کامنت زیر را گذاشته است:\n"
        f"متن کامنت خریدار: «{comment_text}»\n"
        f"موضوع پست: «{post_caption_snippet or 'محصولات فروشگاه'}»\n\n"
        "یک پیام خصوصی (دایرکت) بسیار کوتاه، کاملاً صمیمی، محترمانه و خودمانی به زبان فارسی بنویس که مستقیماً به کامنت او اشاره کند.\n"
        "قوانین بسیار حیاتی و غیرقابل شکست:\n"
        "۱. هیچ لینکی (URL)، هیچ آیدی با @ و هیچ شماره تماسی در پیام نیاور.\n"
        "۲. هدف پیام فقط باز کردن گفتگو و پرسیدن یک سوال باز و دوستانه است تا کاربر پاسخ دهد (مثلاً راهنمایی در مورد سایز، مدل مدنظرش، یا شرایط).\n"
        "۳. طول پیام حداکثر ۲ تا ۳ جمله باشد.\n"
        "۴. فقط و فقط متن پیام دایرکت را بدون هیچ مقدمه یا توضیح اضافی بنویس."
    )

    if settings.llm_api_key:
        try:
            payload = {
                "model": settings.llm_model,
                "messages": [
                    {"role": "system", "content": "You write ultra-personalized, natural Persian Instagram direct messages. Never include links, mentions, or phone numbers."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
                "max_tokens": 120,
            }
            raw = _call_llm_completion(payload, timeout=20.0)
            if raw:
                generated = raw.strip()
                # Clean surrounding quotes
                generated = re.sub(r'^[«"\'\s]+|[»"\'\s]+$', '', generated)
                is_valid, validation_msg = validate_and_sanitize_dm(generated)
                if is_valid:
                    return generated
                else:
                    logger.warning("LLM generated DM failed guardrails (%s). Using safe conversational fallback.", validation_msg)
        except Exception as exc:
            logger.error("LLM DM generation failed: %s", exc)

    # Safe deterministic conversational fallback
    topic_preview = comment_text.replace("\n", " ")[:35]
    fallback = (
        f"سلام {clean_name} عزیز، وقتتون بخیر 🌱\n"
        f"کامنتتون رو در مورد «{topic_preview}» دیدم؛ در رابطه با سفارشتون می‌تونم کمکتون کنم؟"
    )
    return fallback


class OutreachWorker:
    """
    Dedicated worker for Sender Pool (Outreach Accounts).
    Implements Circadian rhythm, human warm-up interactions, typing delays, and rate-limited DMs.
    """

    def __init__(self, tenant_id: Optional[str] = None):
        self.tenant_id = tenant_id or settings.default_tenant_id
        self.tz = pytz.timezone(settings.timezone)
        self.account: Optional[IGAccount] = None
        self.client: Optional[Client] = None

    def is_within_operating_hours(self) -> bool:
        """
        Circadian Rhythm Check: Only execute outreach during human daytime hours
        (e.g., 09:00 to 23:00 in configured timezone).
        """
        now_local = datetime.now(self.tz)
        current_hour = now_local.hour
        return settings.outreach_start_hour <= current_hour < settings.outreach_end_hour

    def get_account_daily_dm_cap(self, account: IGAccount) -> int:
        """
        Tier-based DM Quotas:
        - Warming accounts (< 30 days old): max 8 DMs/day.
        - Established accounts (>= 30 days old): max 20 DMs/day.
        """
        now_utc = datetime.now(timezone.utc)
        created_at = account.created_at or now_utc
        age_days = (now_utc - created_at).days
        if age_days < 30:
            return settings.sender_warming_max_dms_per_day
        return settings.sender_established_max_dms_per_day

    def acquire_sender_account(self) -> bool:
        """
        Round-robin rotation across available SENDER accounts that have not
        exceeded their tier-specific daily DM cap.
        """
        db: Session = SessionLocal()
        try:
            now_utc = datetime.now(timezone.utc)
            accounts = (
                db.query(IGAccount)
                .filter(
                    IGAccount.tenant_id == self.tenant_id,
                    IGAccount.role == AccountRole.SENDER,
                    IGAccount.status == AccountStatus.ACTIVE,
                    (IGAccount.cooldown_until == None) | (IGAccount.cooldown_until <= now_utc),
                )
                .order_by(IGAccount.last_action_at.asc().nullsfirst())
                .all()
            )

            for acc in accounts:
                cap = self.get_account_daily_dm_cap(acc)
                if acc.daily_action_count < cap:
                    self.account = acc
                    mgr = SessionManager(acc)
                    self.client = mgr.get_client()
                    logger.info("Acquired SENDER account @%s (Quota: %d/%d DMs today)", acc.username, acc.daily_action_count, cap)
                    return True
                else:
                    logger.debug("Sender @%s reached daily DM cap (%d/%d). Skipping.", acc.username, acc.daily_action_count, cap)

            logger.warning("No available SENDER accounts with remaining DM quota for tenant '%s'!", self.tenant_id)
            return False
        except Exception as exc:
            logger.error("Error acquiring sender account: %s", exc)
            return False
        finally:
            db.close()

    @handle_instagram_exceptions
    def simulate_profile_visit(self, username: str) -> None:
        """Step 1 of warm-up: Simulates human user opening lead profile in app."""
        logger.info("👀 [WARM-UP] Simulating profile visit for @%s...", username)
        self.client.user_info_by_username(username)

    @handle_instagram_exceptions
    def warm_up_like_post(self, user_pk: str) -> None:
        """Step 2 of warm-up (Optional): Likes user's latest post if public."""
        if not settings.like_before_dm:
            return
        try:
            medias = self.client.user_medias(int(user_pk), amount=1)
            if medias and len(medias) > 0:
                media_id = medias[0].id
                logger.info("❤️ [WARM-UP] Liking latest public post (%s) of user %s...", media_id, user_pk)
                self.client.media_like(media_id)
                time.sleep(random.uniform(3, 7))
        except Exception as exc:
            logger.debug("Warm-up like skipped for user %s (%s). Proceeding to DM.", user_pk, exc)

    @handle_instagram_exceptions
    def send_direct_message(self, user_pk: str, text: str) -> Any:
        """Step 4: Sends Direct Message via instagrapi."""
        logger.info("✉️ [SENDING DM] Sending Cold DM to user PK %s...", user_pk)
        return self.client.direct_send(text=text, user_ids=[int(user_pk)])

    def process_pending_leads(self, max_batch: int = 10) -> int:
        """
        Executes outbound DM sequence for pending leads with strict human emulation:
        circadian hours, warm-up interactions, typing delays, and long random pauses.
        """
        # Circadian Rhythm check
        if not self.is_within_operating_hours():
            now_local = datetime.now(self.tz).strftime("%H:%M")
            logger.info("💤 [CIRCADIAN PAUSE] Current local time is %s. Outreach is paused outside 09:00 - 23:00.", now_local)
            return 0

        db: Session = SessionLocal()
        leads: List[Lead] = []
        try:
            leads = (
                db.query(Lead)
                .filter(
                    Lead.tenant_id == self.tenant_id,
                    Lead.status == LeadStatus.NEW,
                )
                .order_by(Lead.created_at.asc())
                .limit(max_batch)
                .all()
            )
        finally:
            db.close()

        if not leads:
            logger.info("No NEW leads pending outreach for tenant '%s'.", self.tenant_id)
            return 0

        logger.info("Found %d pending leads for outreach. Starting sender sequence...", len(leads))
        sent_count = 0

        for lead in leads:
            # Re-check circadian window before each DM
            if not self.is_within_operating_hours():
                logger.info("Circadian window closed during batch. Stopping outreach loop.")
                break

            # Acquire or rotate sender account
            if not self.acquire_sender_account():
                logger.warning("No eligible sender account available. Halting outreach batch.")
                break

            # 1. Generate personalized DM with Anti-Spam Guardrails
            dm_text = generate_personalized_dm(
                full_name=lead.full_name or lead.username,
                comment_text=lead.comment_text,
                post_caption_snippet=lead.post_caption_snippet,
            )

            is_valid, validation_msg = validate_and_sanitize_dm(dm_text)
            if not is_valid:
                logger.error("Generated DM rejected by guardrails: %s. Skipping lead ID %d.", validation_msg, lead.id)
                self._update_lead_status(lead.id, LeadStatus.SKIPPED)
                continue

            try:
                # 2. Warm-Up Step A: Profile Visit
                self.simulate_profile_visit(lead.username)

                # 3. Warm-Up Step B: Like Latest Post (if public)
                self.warm_up_like_post(lead.ig_user_pk)

                # 4. Warm-Up Step C: Human Typing Simulation
                typing_delay = random.uniform(15.0, 35.0)
                logger.info("⌨️ [TYPING DELAY] Simulating human typing for %.1f seconds...", typing_delay)
                time.sleep(typing_delay)

                # 5. Send DM
                self.send_direct_message(lead.ig_user_pk, dm_text)

                # 6. Update Lead and Sender Quotas
                self._update_lead_status(lead.id, LeadStatus.DM_SENT)
                self._record_sender_action()
                sent_count += 1
                logger.info("✅ [DM SENT SUCCESSFULLY] Sent personalized DM to @%s (PK: %s)", lead.username, lead.ig_user_pk)

                # 7. Long Random Pacing: 12 to 25 minutes between DMs on this sender
                sleep_sec = random.uniform(settings.sender_dm_sleep_min, settings.sender_dm_sleep_max)
                logger.info(
                    "⏳ [INTER-DM SLEEP] Sleeping %.1f minutes (%.0f seconds) before next outreach action...",
                    sleep_sec / 60.0,
                    sleep_sec,
                )
                time.sleep(sleep_sec)

            except CircuitBreakerTriggered:
                logger.error("Circuit breaker tripped for sender @%s. Rotating to next sender account.", self.account.username if self.account else "unknown")
                continue
            except Exception as exc:
                logger.exception("Failed to send outreach DM to lead @%s: %s", lead.username, exc)
                self._update_lead_status(lead.id, LeadStatus.FAILED)
                continue

        logger.info("Outreach sequence finished. Successfully sent %d DMs.", sent_count)
        return sent_count

    def _update_lead_status(self, lead_id: int, status: LeadStatus) -> None:
        """Updates lead status in DB."""
        db: Session = SessionLocal()
        try:
            item = db.query(Lead).filter(Lead.id == lead_id).first()
            if item:
                item.status = status
                item.updated_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as exc:
            db.rollback()
            logger.error("Failed to update lead %d status: %s", lead_id, exc)
        finally:
            db.close()

    def _record_sender_action(self) -> None:
        """Increments daily DM count and updates last_action_at on account."""
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
            logger.error("Failed to record sender action in DB: %s", exc)
        finally:
            db.close()

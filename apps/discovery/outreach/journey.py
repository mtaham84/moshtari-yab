"""Human-like activity around DMs: organic warmup and the in-app journey before a message."""
import logging
import random
import time
from typing import Dict, Any, Optional

logger = logging.getLogger("instagram_outreach")


def perform_organic_warmup(cl: Any, target_pk: int, username: str, *, story_chance: float = 1.0,
                           like_chance: float = 1.0, medias: Optional[list] = None) -> Dict[str, Any]:
    """
    Organic warm-up before a DM, each part with its own chance (1.0 = always, the default for direct calls):
    1. Views up to 2 active stories (our account shows up in the recipient's story viewers).
    2. Likes the user's latest post (`medias` already loaded by a profile view are reused, no second request).
    """
    actions = {"liked_post": False, "viewed_story": False}

    # 1. View active stories (if any)
    try:
        if random.random() < story_chance and hasattr(cl, "user_stories") and hasattr(cl, "story_seen"):
            stories = cl.user_stories(str(target_pk))
            if stories:
                story_pks = [int(s.pk) for s in stories[:2]]
                cl.story_seen(story_pks)
                actions["viewed_story"] = True
                logger.info("Organic story view executed for @%s (pks: %s)", username, story_pks)
                try:
                    from apps.discovery.live_logger import report_live_activity
                    report_live_activity(
                        account_username=getattr(cl, "username", "outreach"),
                        action_type="WATCH_STORY",
                        title=f"تماشای استوری @{username}",
                        description=f"مشاهده استوری لید @{username} جهت ثبت بازدید در بخش Viewers مخاطب",
                        media_type="STORY",
                        duration_seconds=3.0,
                        update_worker="outreach",
                        worker_active=True,
                    )
                except Exception:
                    pass
                time.sleep(random.uniform(2.0, 4.5))
    except Exception as story_err:
        logger.debug("Organic story view skipped for @%s: %s", username, story_err)

    # 2. Like recent post
    try:
        if random.random() < like_chance:
            if medias is None:
                medias = cl.user_medias(target_pk, amount=1)
            if medias:
                latest_media = medias[0]
                cl.media_like(latest_media.id)
                actions["liked_post"] = True
                time.sleep(random.uniform(1.5, 3.0))
    except Exception as like_err:
        logger.debug(f"Organic like warmup skipped for @{username}: {like_err}")

    return actions


# Chance of each step before a cold DM. The same feed -> search -> profile -> story -> like chain before every single
# DM is itself a bot signature (and every like is one more action on a new account); people often open the profile
# straight from the comment and only sometimes watch a story or like a post before writing.
ORGANIC_JOURNEY_ODDS = {"feed": 0.5, "feed_like": 0.1, "search": 0.6, "profile": 0.75, "story": 0.5, "like": 0.2}


def _journey_step(step: str) -> bool:
    return random.random() < ORGANIC_JOURNEY_ODDS.get(step, 0.0)


def execute_organic_journey(
    cl: Any,
    target_username: str,
    target_pk: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Lightweight pre-send dispatch routine:
    1. Resolves numeric target_pk if missing (via user_id_from_username or search_users).
    2. Applies a short human typing delay (6-10 seconds) instead of blocking the send path
       with heavy feed scrolling, story viewing, and post likes (which run in background warmup).
    """
    actions_taken = {
        "user_searched": False,
        "typing_delay_sec": 0.0,
    }

    resolved_pk = target_pk
    if not resolved_pk:
        # Search for user in search bar (/users/search/)
        if hasattr(cl, "search_users"):
            try:
                search_results = cl.search_users(target_username)
                actions_taken["user_searched"] = True
                for u in (search_results or []):
                    u_name = getattr(u, "username", "") or (u.get("username", "") if isinstance(u, dict) else "")
                    u_pk = getattr(u, "pk", None) or (u.get("pk") if isinstance(u, dict) else None)
                    if str(u_name).lower() == str(target_username).lower() and u_pk:
                        resolved_pk = int(u_pk)
                        break
            except Exception as search_err:
                logger.debug("search_users fallback failed for @%s: %s", target_username, search_err)

        if not resolved_pk and hasattr(cl, "user_id_from_username"):
            try:
                resolved_pk = int(cl.user_id_from_username(target_username))
            except Exception as uid_err:
                logger.debug("user_id_from_username skipped for @%s: %s", target_username, uid_err)

    # Short randomized typing jitter (6 to 10 seconds)
    typing_sec = random.uniform(6.0, 10.0)
    actions_taken["typing_delay_sec"] = round(typing_sec, 2)
    time.sleep(typing_sec)

    return {
        "target_pk": resolved_pk,
        "actions": actions_taken,
    }


def execute_background_organic_warmup(
    cl: Any,
    target_username: Optional[str] = None,
    target_pk: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Independent organic warmup session for sender accounts:
    1. Scrolls the home timeline feed and occasionally likes a feed post.
    2. If target user provided, optionally views profile, active stories, and likes latest post.
    Runs asynchronously and decoupled from the active DM dispatch path.
    """
    actions_taken = {
        "feed_scrolled": False,
        "feed_post_liked": False,
        "profile_viewed": False,
        "story_viewed": False,
        "target_post_liked": False,
    }

    # Step 1: Open Timeline Feed & simulate casual scrolling
    try:
        if _journey_step("feed") and hasattr(cl, "get_timeline_feed"):
            timeline = cl.get_timeline_feed()
            actions_taken["feed_scrolled"] = True
            time.sleep(random.uniform(3.0, 6.0))

            feed_items = getattr(timeline, "feed_items", []) or (timeline.get("feed_items", []) if isinstance(timeline, dict) else [])
            if feed_items and _journey_step("feed_like"):
                first_item = feed_items[0]
                m_id = None
                if isinstance(first_item, dict):
                    m_id = (first_item.get("media_or_ad") or {}).get("id") or first_item.get("id")
                elif hasattr(first_item, "id"):
                    m_id = first_item.id
                if m_id and hasattr(cl, "media_like"):
                    cl.media_like(str(m_id))
                    actions_taken["feed_post_liked"] = True
                    logger.info("Organic feed post like executed during casual scroll (media: %s)", m_id)
                    time.sleep(random.uniform(1.5, 3.0))
    except Exception as feed_err:
        logger.debug("Organic timeline feed scroll skipped: %s", feed_err)

    # Step 2: Browse target user profile and warmup if target specified
    if target_pk:
        medias = None
        try:
            if _journey_step("profile") and hasattr(cl, "user_medias"):
                time.sleep(random.uniform(2.0, 4.0))
                medias = cl.user_medias(target_pk, amount=random.randint(3, 6))
                actions_taken["profile_viewed"] = True
        except Exception as profile_err:
            logger.debug("Organic profile inspection skipped for @%s: %s", target_username, profile_err)

        warmup_res = perform_organic_warmup(
            cl, target_pk, target_username or "",
            story_chance=ORGANIC_JOURNEY_ODDS["story"],
            like_chance=ORGANIC_JOURNEY_ODDS["like"] if medias else 0.0,
            medias=medias or [],
        )
        actions_taken["story_viewed"] = warmup_res.get("viewed_story", False)
        actions_taken["target_post_liked"] = warmup_res.get("liked_post", False)

    return actions_taken

"""Instagram cockpit: a web client that drives one account (threads, feed, search, DMs) from the panel."""
import logging
from typing import Any

from django.core.serializers.json import DjangoJSONEncoder
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST


from apps.discovery.models import (
    InstagramEngineAccount
)
from apps.discovery.device_emulator import connect_saved_session

from apps.billing.manage.access import actor_name, manage_access_required

logger = logging.getLogger("management")


class SafeCockpitJSONEncoder(DjangoJSONEncoder):
    """Custom JSON encoder converting Pydantic HttpUrl and other rich objects to plain strings."""
    def default(self, obj):
        if hasattr(obj, "__str__"):
            return str(obj)
        return super().default(obj)


_cockpit_client_cache: dict[int, tuple[float, Any, str]] = {}


def _get_cockpit_client(account: InstagramEngineAccount, force_fresh: bool = False):
    """
    Returns an authenticated instagrapi.Client with device emulation and proxy applied.
    Caches client instance in memory for up to 12 minutes to provide instant UI responsiveness,
    but automatically invalidates immediately if the account's proxy changes.
    """
    import time
    from apps.discovery.ig_client import new_client

    now_ts = time.time()
    current_proxy = account.clean_proxy or ""

    if not force_fresh and account.id in _cockpit_client_cache:
        cached_ts, cached_cl, cached_proxy = _cockpit_client_cache[account.id]
        if cached_proxy == current_proxy and now_ts - cached_ts < 720:  # 12 minutes & same proxy
            return cached_cl, None
        else:
            _cockpit_client_cache.pop(account.id, None)

    cookies = (account.session_json or {}).get("cookies", {})
    sessionid = cookies.get("sessionid")
    if not sessionid:
        return None, f"اکانت @{account.username} کوکی سشن معتبر ندارد."

    # its own proxy / system config, else the shared pool (never the server's IP)
    cl = new_client(account, delay_range=[0.3, 0.8])

    try:
        connect_saved_session(cl, account)  # same phone + session as the workers, no fresh login per page view
        _cockpit_client_cache[account.id] = (now_ts, cl, current_proxy)
        return cl, None
    except Exception as exc:
        logger.warning(f"Failed to initialize cockpit client for @{account.username}: {exc}", exc_info=True)
        return None, str(exc)


@manage_access_required
def instagram_account_cockpit(request, pk: int):
    """
    Dedicated cockpit page for a single Instagram account.
    Provides direct chat, timeline browsing, organic search, and simulator controls.
    """
    from django.shortcuts import get_object_or_404
    from apps.discovery.device_emulator import ensure_account_device_profile
    from apps.businesses.models import Business

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    ensure_account_device_profile(account)

    business = None
    if account.tenant_id and str(account.tenant_id).startswith("tenant_"):
        b_id = str(account.tenant_id).replace("tenant_", "")
        business = Business.objects.filter(id=b_id).first()

    device_settings = account.device_settings or {}
    dev_specs = device_settings.get("device_settings", {})

    return render(request, "ops/instagram_account_cockpit.html", {
        "account": account,
        "business": business,
        "device_settings": device_settings,
        "dev_specs": dev_specs,
        "status_display": account.get_status_display(),
    })


@manage_access_required
def instagram_cockpit_threads(request, pk: int):
    """AJAX endpoint: Fetch recent Direct Message threads for the account."""
    from django.shortcuts import get_object_or_404
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        threads, _ = cl.direct_threads_chunk(thread_message_limit=5)
        formatted_threads = []
        for th in threads[:25]:
            users = [
                {
                    "pk": str(getattr(u, "pk", "") or ""),
                    "username": str(getattr(u, "username", "") or ""),
                    "full_name": str(getattr(u, "full_name", "") or ""),
                    "profile_pic_url": str(getattr(u, "profile_pic_url", "") or ""),
                }
                for u in getattr(th, "users", [])
            ]
            last_msg = ""
            last_msg_time = ""
            is_outgoing = False
            msgs = getattr(th, "messages", [])
            if msgs:
                m = msgs[0]
                last_msg = getattr(m, "text", "") or ""
                if hasattr(m, "timestamp") and m.timestamp:
                    try:
                        last_msg_time = m.timestamp.strftime("%H:%M")
                    except Exception:
                        logger.warning("instagram_cockpit_threads failed", exc_info=True)
                        last_msg_time = str(m.timestamp)[:5]
                is_outgoing = str(getattr(m, "user_id", "")) == str(cl.user_id)

            thread_title = getattr(th, "thread_title", "") or (users[0]["username"] if users else f"گفتگو {th.id}")
            formatted_threads.append({
                "id": str(th.id),
                "title": str(thread_title),
                "users": users,
                "last_message": str(last_msg),
                "last_message_time": str(last_msg_time),
                "is_outgoing": is_outgoing,
                "unread": bool(getattr(th, "read_state", 1) == 1),
            })

        return JsonResponse({"success": True, "threads": formatted_threads}, encoder=SafeCockpitJSONEncoder)
    except Exception as exc:
        logger.exception("Error loading direct threads for cockpit @%s: %s", account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
def instagram_cockpit_thread_detail(request, pk: int, thread_id: str):
    """AJAX endpoint: Fetch detailed messages in a specific Direct thread."""
    from django.shortcuts import get_object_or_404
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        th = cl.direct_thread(thread_id, amount=25)
        raw_msgs = getattr(th, "messages", []) or []
        msgs = []
        current_uid = str(cl.user_id)
        for m in raw_msgs:
            m_text = getattr(m, "text", "") or ""
            t_str = ""
            if hasattr(m, "timestamp") and m.timestamp:
                try:
                    t_str = m.timestamp.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    logger.warning("instagram_cockpit_thread_detail failed", exc_info=True)
                    t_str = str(m.timestamp)

            msgs.append({
                "id": str(getattr(m, "id", "")),
                "user_id": str(getattr(m, "user_id", "")),
                "is_outgoing": str(getattr(m, "user_id", "")) == current_uid,
                "text": m_text,
                "timestamp": t_str,
            })

        # Oldest first so chat flows downward naturally
        msgs.reverse()
        return JsonResponse({"success": True, "thread_id": thread_id, "messages": msgs})
    except Exception as exc:
        logger.exception("Error loading thread detail %s for cockpit @%s: %s", thread_id, account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
@require_POST
def instagram_cockpit_send_direct(request, pk: int):
    """AJAX endpoint: Send direct message from cockpit (same limits as automated outreach)."""
    from django.shortcuts import get_object_or_404
    from apps.discovery.outreach.limits import manual_dm_block_reason, record_manual_dm
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    thread_id = request.POST.get("thread_id", "").strip()
    user_id = request.POST.get("user_id", "").strip()
    text = request.POST.get("text", "").strip()

    if not text:
        return JsonResponse({"success": False, "error": "متن پیام نمی‌تواند خالی باشد."}, status=400)
    if user_id and not user_id.isdigit():
        return JsonResponse({"success": False, "error": "شناسهٔ کاربر نامعتبر است."}, status=400)
    if not thread_id and not user_id:
        return JsonResponse({"success": False, "error": "مخاطب پیام مشخص نیست."}, status=400)

    blocked = manual_dm_block_reason(account)
    if blocked:
        return JsonResponse({"success": False, "error": blocked, "blocked": True}, status=429)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        if thread_id:
            cl.direct_send(text, thread_ids=[thread_id])
        else:
            cl.direct_send(text, user_ids=[int(user_id)])

        record_manual_dm(account, user_pk=user_id or None, text=text, actor=actor_name(request))

        return JsonResponse({
            "success": True,
            "message": "پیام با موفقیت ارسال شد.",
            "daily_count": account.daily_action_count,
        })
    except Exception as exc:
        logger.exception("Error sending direct from cockpit @%s: %s", account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
def instagram_cockpit_feed(request, pk: int):
    """AJAX endpoint: Fetch recent timeline posts for account."""
    from django.shortcuts import get_object_or_404
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        timeline = cl.get_timeline_feed()
        feed_items = getattr(timeline, "feed_items", []) or (timeline.get("feed_items", []) if isinstance(timeline, dict) else [])

        items = []
        for fi in feed_items[:12]:
            m = fi
            if isinstance(fi, dict) and "media_or_ad" in fi:
                m = fi["media_or_ad"]
            elif hasattr(fi, "media_or_ad") and fi.media_or_ad:
                m = fi.media_or_ad

            caption = ""
            user_info = {}
            img_url = ""
            like_count = 0
            m_id = getattr(m, "id", "") or (m.get("id") if isinstance(m, dict) else "")

            # Caption
            cap_obj = getattr(m, "caption", None) or (m.get("caption") if isinstance(m, dict) else None)
            if cap_obj:
                caption = getattr(cap_obj, "text", "") or (cap_obj.get("text") if isinstance(cap_obj, dict) else "")

            # User
            u_obj = getattr(m, "user", None) or (m.get("user") if isinstance(m, dict) else None)
            if u_obj:
                user_info = {
                    "username": str(getattr(u_obj, "username", "") or (u_obj.get("username") if isinstance(u_obj, dict) else "")),
                    "full_name": str(getattr(u_obj, "full_name", "") or (u_obj.get("full_name") if isinstance(u_obj, dict) else "")),
                    "profile_pic_url": str(getattr(u_obj, "profile_pic_url", "") or (u_obj.get("profile_pic_url") if isinstance(u_obj, dict) else "") or ""),
                }

            # Thumbnail
            img_url = str(getattr(m, "thumbnail_url", "") or "")
            if not img_url and hasattr(m, "image_versions2"):
                cands = getattr(m.image_versions2, "candidates", []) or []
                if cands:
                    img_url = str(getattr(cands[0], "url", "") or "")
            elif isinstance(m, dict) and not img_url:
                cands = (m.get("image_versions2") or {}).get("candidates") or []
                if cands:
                    img_url = str(cands[0].get("url", "") or "")

            like_count = getattr(m, "like_count", 0) or (m.get("like_count") if isinstance(m, dict) else 0)

            items.append({
                "id": str(m_id),
                "caption": str(caption[:150] + ("..." if len(caption) > 150 else "")),
                "user": user_info,
                "image_url": str(img_url),
                "like_count": int(like_count),
            })

        return JsonResponse({"success": True, "feed": items}, encoder=SafeCockpitJSONEncoder)
    except Exception as exc:
        logger.exception("Error loading timeline feed for cockpit @%s: %s", account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
@require_POST
def instagram_cockpit_scroll_feed(request, pk: int):
    """AJAX endpoint: Simulate natural human scroll and warm-up browsing on timeline feed."""
    import time
    import random
    from django.shortcuts import get_object_or_404
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        timeline = cl.get_timeline_feed()
        delay_sec = round(random.uniform(3.5, 6.5), 1)
        time.sleep(delay_sec)

        was_liked = False
        feed_items = getattr(timeline, "feed_items", []) or (timeline.get("feed_items", []) if isinstance(timeline, dict) else [])
        if feed_items and random.random() < 0.25:
            first_m = feed_items[0]
            m_id = None
            if isinstance(first_m, dict):
                m_id = (first_m.get("media_or_ad") or {}).get("id") or first_m.get("id")
            elif hasattr(first_m, "id"):
                m_id = first_m.id
            if m_id and hasattr(cl, "media_like"):
                cl.media_like(str(m_id))
                was_liked = True

        return JsonResponse({
            "success": True,
            "message": f"اسکرول فید شبیه‌سازی شد ({delay_sec} ثانیه گشت‌وگذار).",
            "liked_post": was_liked,
            "delay_sec": delay_sec,
        })
    except Exception as exc:
        logger.exception("Error during simulated scroll for cockpit @%s: %s", account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
def instagram_cockpit_search(request, pk: int):
    """AJAX endpoint: Search users in Instagram search bar."""
    from django.shortcuts import get_object_or_404
    account = get_object_or_404(InstagramEngineAccount, pk=pk)

    q = request.GET.get("q", "").strip().lstrip("@")
    if not q:
        return JsonResponse({"success": True, "results": []})

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        results = cl.search_users(q) or []
        items = []
        for u in results[:10]:
            items.append({
                "pk": str(getattr(u, "pk", "") or ""),
                "username": str(getattr(u, "username", "") or ""),
                "full_name": str(getattr(u, "full_name", "") or ""),
                "is_private": bool(getattr(u, "is_private", False)),
                "is_verified": bool(getattr(u, "is_verified", False)),
                "profile_pic_url": str(getattr(u, "profile_pic_url", "") or ""),
            })
        return JsonResponse({"success": True, "results": items}, encoder=SafeCockpitJSONEncoder)
    except Exception as exc:
        logger.exception("Error searching users in cockpit @%s: %s", account.username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@manage_access_required
@require_POST
def instagram_cockpit_organic_dm(request, pk: int):
    """AJAX endpoint: Execute full organic journey to target user and dispatch DM."""
    from django.shortcuts import get_object_or_404
    from apps.discovery.outreach import journey, limits

    account = get_object_or_404(InstagramEngineAccount, pk=pk)
    target_username = request.POST.get("target_username", "").strip().lstrip("@")
    message_text = request.POST.get("message_text", "").strip()

    if not target_username or not message_text:
        return JsonResponse({"success": False, "error": "نام کاربری مقصد و متن پیام الزامی است."}, status=400)

    blocked = limits.manual_dm_block_reason(account)
    if blocked:
        return JsonResponse({"success": False, "error": blocked, "blocked": True}, status=429)

    cl, err = _get_cockpit_client(account)
    if not cl:
        return JsonResponse({"success": False, "error": err or "اتصال برقرار نشد."}, status=400)

    try:
        journey_res = journey.execute_organic_journey(cl, target_username)
        target_pk = journey_res.get("target_pk")
        if not target_pk:
            target_pk = int(cl.user_id_from_username(target_username))

        cl.direct_send(message_text, user_ids=[target_pk])

        limits.record_manual_dm(account, user_pk=target_pk, text=message_text, actor=actor_name(request))

        return JsonResponse({
            "success": True,
            "message": f"پیام ارگانیک با موفقیت به @{target_username} ارسال شد.",
            "journey": journey_res,
            "daily_count": account.daily_action_count,
        })
    except Exception as exc:
        logger.exception("Error executing organic DM from cockpit @%s to @%s: %s", account.username, target_username, exc)
        return JsonResponse({"success": False, "error": str(exc)}, status=500)

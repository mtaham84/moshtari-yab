"""Small HTTP helpers shared by the JSON/AJAX endpoints."""
from django.core.cache import cache
from django.http import JsonResponse


def allow_user_rate(prefix: str, user_id, limit: int, window_seconds: int = 3600) -> bool:
    """Fixed-window per-user rate limiter backed by Django's cache."""
    key = f"{prefix}:{user_id}"
    try:
        count = cache.get(key, 0)
        if count >= limit:
            return False
        if count == 0:
            if not cache.add(key, 1, timeout=window_seconds):
                cache.incr(key)
        else:
            cache.incr(key)
        return True
    except (ValueError, TypeError):
        cache.set(key, 1, timeout=window_seconds)
        return True


def wants_json(request) -> bool:
    """True for fetch()/XHR calls, JSON bodies, Accept: application/json or ?format=json."""
    return (
        request.headers.get("x-requested-with") == "XMLHttpRequest"
        or request.content_type == "application/json"
        or "application/json" in request.headers.get("accept", "")
        or request.GET.get("format") == "json"
    )


def json_ok(message: str = "", **data) -> JsonResponse:
    return JsonResponse({"success": True, "message": message, **data})


def json_error(message: str, status: int = 400, **data) -> JsonResponse:
    return JsonResponse({"success": False, "error": message, **data}, status=status)

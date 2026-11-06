"""Small HTTP helpers shared by the JSON/AJAX endpoints."""
from django.http import JsonResponse


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

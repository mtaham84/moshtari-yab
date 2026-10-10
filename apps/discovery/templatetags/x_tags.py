from django import template
from django.utils import timezone

register = template.Library()


@register.filter
def ago(value) -> str:
    """Persian «… پیش» regardless of the active locale (e.g. «۵ دقیقه پیش»)."""
    if not value:
        return ""
    seconds = max(0.0, (timezone.now() - value).total_seconds())
    if seconds < 60:
        return "همین الان"
    if seconds < 3600:
        return f"{int(seconds // 60)} دقیقه پیش"
    if seconds < 86400:
        return f"{int(seconds // 3600)} ساعت پیش"
    return f"{int(seconds // 86400)} روز پیش"

from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _num(v):
    try:
        return Decimal(str(v if v not in (None, "") else 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


@register.filter
def toman(v):
    """12345.6 → «۱۲٬۳۴۶» style grouping (Latin digits keep tables aligned)."""
    n = _num(v)
    if abs(n) < 10 and n != n.to_integral_value():
        return f"{n:,.1f}"
    return f"{n:,.0f}"


@register.filter
def usd(v):
    n = _num(v)
    return f"${n:,.4f}" if abs(n) < 100 else f"${n:,.2f}"


@register.filter
def num(v):
    return f"{_num(v):,.0f}"


@register.filter
def time_ago_fa(dt):
    """
    Returns natural Persian relative time with pure Persian digits and clean RTL flow.
    e.g. «همین الان»، «۴ دقیقه پیش»، «۲ ساعت پیش»، «۳ روز پیش»
    """
    if not dt:
        return "—"
    try:
        from django.utils import timezone
        from apps.core.jalali import to_persian_digits, format_jalali_date
        now = timezone.now()
        diff = now - dt
        total_seconds = int(diff.total_seconds())
        if total_seconds < 45:
            return "همین الان"
        minutes = total_seconds // 60
        if minutes < 60:
            return f"{to_persian_digits(max(1, minutes))} دقیقه پیش"
        hours = minutes // 60
        if hours < 24:
            return f"{to_persian_digits(hours)} ساعت پیش"
        days = hours // 24
        if days < 30:
            return f"{to_persian_digits(days)} روز پیش"
        return format_jalali_date(dt)
    except Exception:
        return "—"


@register.filter
def fa_digits(val):
    if val is None:
        return ""
    from apps.core.jalali import to_persian_digits
    return to_persian_digits(val)


@register.filter
def jalali_dt(dt):
    """Formats datetime to Persian Jalali string with Tehran time in Persian digits."""
    if not dt:
        return "—"
    try:
        from zoneinfo import ZoneInfo
        from django.utils import timezone
        from apps.core.jalali import format_jalali_date, to_persian_digits
        tehran_tz = ZoneInfo("Asia/Tehran")
        dt_tehran = dt.astimezone(tehran_tz) if timezone.is_aware(dt) else dt
        d_str = format_jalali_date(dt_tehran.date(), persian_digits=True)
        t_str = to_persian_digits(f"{dt_tehran.hour:02d}:{dt_tehran.minute:02d}:{dt_tehran.second:02d}")
        return f"{d_str} ساعت {t_str}"
    except Exception:
        return str(dt)

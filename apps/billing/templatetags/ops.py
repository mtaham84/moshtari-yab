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

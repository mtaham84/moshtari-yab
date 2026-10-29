from django.conf import settings


def flags(request):
    """Template flags. The login page displays the test seller accounts."""
    return {"SHOW_DEMO_ACCOUNTS": getattr(settings, "SHOW_DEMO_ACCOUNTS", False)}


def appearance(request):
    """CSS variables for the admin-editable background (falls back to the bundled photo)."""
    try:
        from .models import SiteAppearance
        row = SiteAppearance.current()
    except Exception:   # table not migrated yet
        row = None
    url = ""
    if row and row.background_image:
        try:
            url = f"{row.background_image.url}?v={int(row.updated_at.timestamp())}"
        except Exception:
            url = ""
    overlay = (row.overlay_percent if row else 20) / 100
    return {"ui_bg_url": url, "ui_overlay": overlay}

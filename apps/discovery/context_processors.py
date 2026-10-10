from django.conf import settings


def x_panel(request):
    return {"X_PANEL_ENABLED": getattr(settings, "X_PANEL_ENABLED", False), "X_SELLER_UI": getattr(settings, "X_SELLER_UI", True)}

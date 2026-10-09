from django.conf import settings


def flags(request):
    """Template flags. The login page shows the demo accounts only in development (DJANGO_DEBUG=True)."""
    return {"SHOW_DEMO_ACCOUNTS": settings.DEBUG}

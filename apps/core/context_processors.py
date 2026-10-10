from django.conf import settings


def flags(request):
    """Template flags. The login page displays the test seller accounts."""
    return {"SHOW_DEMO_ACCOUNTS": getattr(settings, "SHOW_DEMO_ACCOUNTS", True)}

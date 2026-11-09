"""Who may open the /manage/ pages."""
from apps.core.permissions import operator_required

# Kept as a name because ig_accounts_views and many decorators use it.
manage_access_required = operator_required


def actor_name(request) -> str:
    return request.user.get_username()

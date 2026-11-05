"""Business helpers shared by the seller views."""
from __future__ import annotations

from .models import Business


def ensure_business(user, *, business_domain: str = "عمومی") -> Business:
    """The user's Business, created with defaults when missing (signup normally creates it; staff/admin-made users
    may not have one). Every view that needs a business goes through here instead of its own Business.objects.create.
    """
    business = getattr(user, "business", None)
    if business is not None:
        return business
    business, _ = Business.objects.get_or_create(
        user=user,
        defaults={
            "name": f"کسب‌وکار {user.first_name or user.username}".strip(),
            "business_type": "PHYSICAL",
            "business_domain": business_domain,
        },
    )
    return business

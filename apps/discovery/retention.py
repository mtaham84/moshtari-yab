"""Shared customer-preservation rules used by source-data retention paths."""
from django.db.models import Q

from .models import Customer, Opportunity


def customer_ids_to_anonymize(opportunities):
    """Customers with no retained opportunity or sales record can be anonymized."""
    affected = opportunities.values_list("customer_id", flat=True).distinct()
    retained = Opportunity.objects.filter(trace_metadata__retention_scrubbed__isnull=True).exclude(
        pk__in=opportunities.values("pk")
    ).exclude(status="EXPIRED").values("customer_id")
    sold = Opportunity.objects.filter(pk__in=opportunities.values("pk")).filter(
        Q(orders__isnull=False) | Q(status__in=("CONVERTED", "CONTACTED"))
    ).values("customer_id")
    return Customer.objects.filter(pk__in=affected).exclude(pk__in=retained).exclude(pk__in=sold)

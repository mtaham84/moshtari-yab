"""Final X delivery eligibility; called inside the bridge's import transaction."""
from apps.billing.models import BillingSettings, Wallet
from apps.billing.services import blocked_wallets_q


def eligible_x_delivery_sellers(payload: dict, products, allowed: set[int] | None) -> set[int] | None:
    source = payload.get('source') or {}
    is_x = str(source.get('platform') or '').casefold() == 'x' or str(source.get('chat_id') or '').casefold().startswith('x:')
    if not is_x:
        return allowed
    owners = {product.business_id for product in products}
    if allowed is not None:
        owners &= allowed
    if not owners:
        return set()
    config = BillingSettings.objects.order_by('pk').first() or BillingSettings()
    # Hold eligible wallets until the panel write commits, so a concurrent debit cannot invalidate the check.
    return set(Wallet.objects.select_for_update().filter(business_id__in=owners)
               .exclude(blocked_wallets_q(config)).order_by('business_id').values_list('business_id', flat=True))

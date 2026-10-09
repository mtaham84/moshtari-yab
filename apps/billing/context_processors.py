def wallet(request):
    """Seller's balance for the panel header (cheap: one query, only for logged-in sellers)."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    business = getattr(user, "business", None)
    if business is None:
        return {"is_ops": user.is_staff}
    from .models import BillingSettings, Wallet

    w = Wallet.objects.filter(business=business).only("balance_toman").first()
    s = BillingSettings.get()
    balance = w.balance_toman if w else 0
    return {"wallet_balance": balance, "is_ops": user.is_staff,
            "wallet_blocked": s.enforce_balance and balance <= s.min_balance_toman}

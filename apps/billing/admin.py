from django.contrib import admin

from .models import AIModel, BillingSettings, Provider, Wallet, WalletTransaction


class AIModelInline(admin.TabularInline):
    model = AIModel
    extra = 0
    fields = ("name", "input_price_usd", "output_price_usd", "use_extract", "use_verify", "use_reply", "priority", "is_active")


@admin.register(Provider)
class ProviderAdmin(admin.ModelAdmin):
    list_display = ("name", "base_url", "is_active")
    inlines = [AIModelInline]


@admin.register(AIModel)
class AIModelAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "input_price_usd", "output_price_usd", "roles_display", "priority", "is_active")
    list_filter = ("provider", "is_active")


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ("business", "balance_toman", "updated_at")
    readonly_fields = ("balance_toman",)   # change balances through transactions (panel /ops/wallets/)


@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ("wallet", "kind", "amount_toman", "balance_after", "calls", "created_at")
    list_filter = ("kind",)

    def has_change_permission(self, request, obj=None):
        return False


admin.site.register(BillingSettings)

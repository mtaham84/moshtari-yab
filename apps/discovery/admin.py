from django.contrib import admin

from .models import MonitoredCommunity


@admin.register(MonitoredCommunity)
class MonitoredCommunityAdmin(admin.ModelAdmin):
    """Global sources (business empty) are managed here; sellers manage their private ones in the panel."""

    list_display = ("display_name", "handle_or_link", "scope", "business", "is_active", "sync_status",
                    "members_count", "messages_scanned_count", "last_scanned_at")
    list_filter = ("scope", "is_active", "sync_status")
    search_fields = ("name", "handle_or_link", "business__name")
    readonly_fields = ("scope", "telegram_chat_id", "sync_status", "sync_error", "members_count",
                       "messages_scanned_count", "leads_discovered_count", "last_scanned_at", "created_at")
    fields = ("handle_or_link", "name", "description", "business", "is_active") + readonly_fields
    actions = ("activate", "deactivate")

    @admin.action(description="فعال‌سازی پایش")
    def activate(self, request, queryset):
        queryset.update(is_active=True)

    @admin.action(description="توقف پایش")
    def deactivate(self, request, queryset):
        queryset.update(is_active=False)

from django.contrib import admin

from .models import SiteAppearance


@admin.register(SiteAppearance)
class SiteAppearanceAdmin(admin.ModelAdmin):
    list_display = ("__str__", "overlay_percent", "updated_at")
    fields = ("background_image", "overlay_percent")

    def has_add_permission(self, request):
        return not SiteAppearance.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

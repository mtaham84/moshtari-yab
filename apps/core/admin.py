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


from .models import LLMPromptConfig


@admin.register(LLMPromptConfig)
class LLMPromptConfigAdmin(admin.ModelAdmin):
    list_display = ("title", "key", "category", "is_customized", "version", "updated_at")
    list_filter = ("category", "is_customized")
    search_fields = ("title", "key", "description", "prompt_text")
    readonly_fields = ("version", "updated_at", "default_prompt")
    fieldsets = (
        ("مشخصات پرامپت", {
            "fields": ("title", "key", "category", "module_path", "description")
        }),
        ("متن پرامپت فعال", {
            "fields": ("prompt_text", "is_customized", "version", "updated_at")
        }),
        ("نسخه پیش‌فرض کارخانه", {
            "fields": ("default_prompt",),
            "classes": ("collapse",)
        }),
    )

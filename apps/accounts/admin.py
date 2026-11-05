from django.contrib import admin, messages
from django.utils.html import format_html

from apps.accounts.models import ActivationRequest, EmailVerification
from apps.accounts.services import activate_account_from_request, send_telegram_alert
from apps.core.jalali import format_jalali_datetime


@admin.register(ActivationRequest)
class ActivationRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "full_name",
        "company_name",
        "phone_number",
        "email",
        "team_size",
        "status_badge",
        "created_at_jalali",
        "created_user_link",
    )
    list_display_links = ("id", "full_name")
    list_filter = ("status", "created_at", "team_size")
    search_fields = ("full_name", "company_name", "phone_number", "email", "problem_statement")
    readonly_fields = ("created_at", "updated_at", "created_user", "created_business")
    actions = ["action_activate_and_create_account", "action_mark_approved", "action_mark_rejected"]

    fieldsets = (
        ("اطلاعات متقاضی", {
            "fields": ("full_name", "company_name", "phone_number", "email", "team_size")
        }),
        ("نیازمندی‌ها و اهداف", {
            "fields": ("problem_statement",)
        }),
        ("وضعیت و حساب کاربری متناظر", {
            "fields": ("status", "created_user", "created_business", "admin_notes")
        }),
        ("اطلاعات زمانی", {
            "fields": ("created_at", "updated_at")
        }),
    )

    @admin.display(description="وضعیت درخواست")
    def status_badge(self, obj):
        colors = {
            ActivationRequest.STATUS_PENDING: "#f59e0b",
            ActivationRequest.STATUS_APPROVED: "#3b82f6",
            ActivationRequest.STATUS_ACTIVATED: "#10b981",
            ActivationRequest.STATUS_REJECTED: "#ef4444",
        }
        color = colors.get(obj.status, "#6b7280")
        return format_html(
            '<span style="background-color: {}; color: #fff; padding: 3px 9px; border-radius: 9999px; font-weight: 600; font-size: 11px;">{}</span>',
            color,
            obj.get_status_display()
        )

    @admin.display(description="زمان ثبت")
    def created_at_jalali(self, obj):
        if not obj.created_at:
            return "—"
        try:
            return format_jalali_datetime(obj.created_at)
        except Exception:
            return str(obj.created_at)[:16]

    @admin.display(description="کاربر متناظر")
    def created_user_link(self, obj):
        if not obj.created_user:
            return "—"
        return format_html(
            '<a href="/admin/auth/user/{}/change/">{}</a>',
            obj.created_user.id,
            obj.created_user.username
        )

    @admin.action(description="فعال‌سازی درخواست و ساخت خودکار حساب کاربری و کسب‌وکار")
    def action_activate_and_create_account(self, request, queryset):
        success_count = 0
        details = []

        for req in queryset:
            try:
                res = activate_account_from_request(req)
                success_count += 1
                details.append(
                    f"نام کاربری: {res['username']} | رمز عبور: {res['raw_password']} | متقاضی: {req.full_name}"
                )
                tg_text = (
                    "🎉 <b>حساب کاربری جدید در پنل ادمین فعال شد!</b>\n\n"
                    f"👤 <b>متقاضی:</b> {req.full_name}\n"
                    f"🏢 <b>کسب‌وکار:</b> {req.company_name}\n"
                    f"🔑 <b>نام کاربری:</b> <code>{res['username']}</code>\n"
                    f"🔒 <b>رمز عبور موقت:</b> <code>{res['raw_password']}</code>\n"
                    f"🌐 <b>لینک ورود:</b> <code>https://peyda.store/login/</code>"
                )
                send_telegram_alert(tg_text)
            except Exception as e:
                self.message_user(request, f"خطا در فعال‌سازی درخواست #{req.id}: {e}", level=messages.ERROR)

        if success_count:
            summary = "؛ ".join(details)
            self.message_user(
                request,
                f"تعداد {success_count} حساب کاربری با موفقیت فعال و ایجاد شدند. ({summary})",
                level=messages.SUCCESS
            )

    @admin.action(description="تغییر وضعیت به تایید شده")
    def action_mark_approved(self, request, queryset):
        updated = queryset.update(status=ActivationRequest.STATUS_APPROVED)
        self.message_user(request, f"وضعیت {updated} درخواست به تایید شده تغییر یافت.", level=messages.INFO)

    @admin.action(description="تغییر وضعیت به رد شده")
    def action_mark_rejected(self, request, queryset):
        updated = queryset.update(status=ActivationRequest.STATUS_REJECTED)
        self.message_user(request, f"وضعیت {updated} درخواست به رد شده تغییر یافت.", level=messages.WARNING)


@admin.register(EmailVerification)
class EmailVerificationAdmin(admin.ModelAdmin):
    list_display = ("email", "code", "created_at", "expires_at", "attempts", "is_verified")
    list_filter = ("is_verified", "created_at")
    search_fields = ("email", "code")

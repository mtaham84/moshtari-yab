"""Admin panel (/ops/): costs, providers & models with prices, wallets, Telegram sources, service status."""
from decimal import Decimal
from functools import wraps

from django.contrib import messages
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.businesses.models import Business
from apps.discovery.models import MonitoredCommunity
from apps.discovery.sources import GLOBAL, normalize_link

from . import services
from .forms import AIModelForm, BillingSettingsForm, GlobalSourceForm, ProviderForm, WalletTransactionForm
from .models import AIModel, BillingSettings, Provider, Wallet, WalletTransaction

PERIODS = {"1": "امروز (۲۴ ساعت)", "7": "۷ روز", "30": "۳۰ روز", "365": "یک سال"}


def staff_required(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not request.user.is_staff:
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapper


def _next(request, default: str):
    nxt = request.POST.get("next", "")
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        return redirect(nxt)
    return redirect(default)


def _days(request) -> str:
    d = request.GET.get("days", "30")
    return d if d in PERIODS else "30"


@staff_required
def dashboard(request):
    days = _days(request)
    totals = services.cost_totals(float(days))
    wallets = Wallet.objects.aggregate(total=Sum("balance_toman"), n=Count("id"))
    s = BillingSettings.get()
    blocked = Wallet.objects.filter(balance_toman__lte=s.min_balance_toman).count() if s.enforce_balance else 0
    rev = services.revenue(float(days))
    waiting = services.waiting_payment().values()
    held = {k: sum(w[k] for w in waiting) for k in ("messages", "needs", "products")}
    return render(request, "ops/dashboard.html", {
        "nav": "dashboard", "days": days, "periods": PERIODS, "totals": totals, "revenue": rev,
        "profit": rev - Decimal(str(round(totals["toman"], 2))),
        "wallet_total": wallets["total"] or 0, "wallet_count": wallets["n"], "blocked": blocked, "held": held,
        "by_model": services.cost_breakdown(float(days), "model"),
        "by_stage": services.cost_breakdown(float(days), "stage"),
        "daily": services.cost_daily(14), "status": services.service_status(),
        "recent": WalletTransaction.objects.select_related("wallet__business")[:10], "settings": s,
    })


@staff_required
def costs(request):
    days = _days(request)
    f = {k: request.GET.get(k, "").strip() for k in ("business", "model", "stage")}
    page = max(int(request.GET.get("page", "1") or 1) if str(request.GET.get("page", "1")).isdigit() else 1, 1)
    rows = services.cost_ledger(limit=100, offset=(page - 1) * 100, **f)
    return render(request, "ops/costs.html", {
        "nav": "costs", "days": days, "periods": PERIODS, "filters": f, "rows": rows, "page": page,
        "has_next": len(rows) == 100, "by_business": services.cost_breakdown(float(days), "business"),
        "businesses": Business.objects.order_by("name").values_list("pk", "name"),
        "models": AIModel.objects.values_list("name", flat=True).distinct(),
    })


# ── providers & models ─────────────────────────────────────────────────────────
@staff_required
def providers(request):
    return render(request, "ops/providers.html", {
        "nav": "providers", "providers": Provider.objects.prefetch_related("models"),
        "orphan_hint": not Provider.objects.exists(),
    })


@staff_required
def provider_edit(request, pk=None):
    obj = get_object_or_404(Provider, pk=pk) if pk else None
    form = ProviderForm(request.POST or None, instance=obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "سرویس‌دهنده ذخیره شد.")
        return redirect("ops:providers")
    return render(request, "ops/form.html", {"nav": "providers", "form": form, "back": "ops:providers",
                                              "title": f"ویرایش {obj.name}" if obj else "سرویس‌دهنده‌ی جدید"})


@staff_required
@require_POST
def provider_delete(request, pk):
    obj = get_object_or_404(Provider, pk=pk)
    obj.delete()
    messages.success(request, f"«{obj.name}» و مدل‌هایش حذف شدند.")
    return redirect("ops:providers")


@staff_required
@require_POST
def provider_test(request, pk):
    ok, msg = services.test_provider(get_object_or_404(Provider, pk=pk))
    (messages.success if ok else messages.error)(request, msg)
    return redirect("ops:providers")


@staff_required
@require_POST
def provider_import_env(request):
    provider, n = services.import_from_env()
    messages.success(request, f"«{provider.name}» با {n} مدل از تنظیمات فعلی ساخته شد. قیمت‌ها را بررسی کنید.")
    return redirect("ops:providers")


@staff_required
def model_edit(request, pk=None):
    obj = get_object_or_404(AIModel, pk=pk) if pk else None
    initial = {"provider": request.GET.get("provider")} if not obj else None
    form = AIModelForm(request.POST or None, instance=obj, initial=initial)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "مدل ذخیره شد؛ موتور حداکثر تا یک دقیقه‌ی دیگر از آن استفاده می‌کند.")
        return redirect("ops:providers")
    return render(request, "ops/form.html", {"nav": "providers", "form": form, "back": "ops:providers",
                                              "title": f"ویرایش مدل {obj.name}" if obj else "مدل جدید"})


@staff_required
@require_POST
def model_delete(request, pk):
    obj = get_object_or_404(AIModel, pk=pk)
    obj.delete()
    messages.success(request, f"مدل «{obj.name}» حذف شد.")
    return redirect("ops:providers")


@staff_required
@require_POST
def model_toggle(request, pk):
    obj = get_object_or_404(AIModel, pk=pk)
    obj.is_active = not obj.is_active
    obj.save(update_fields=["is_active", "updated_at"])
    return redirect("ops:providers")


# ── wallets ────────────────────────────────────────────────────────────────────
@staff_required
def wallets(request):
    q = request.GET.get("q", "").strip()
    for b in Business.objects.filter(wallet__isnull=True):
        services.ensure_wallet(b)
    items = Wallet.objects.select_related("business", "business__user").annotate(
        used=Sum("transactions__amount_toman", filter=Q(transactions__kind=WalletTransaction.USAGE)),
        paid=Sum("transactions__amount_toman", filter=Q(transactions__kind=WalletTransaction.TOPUP)),
    ).order_by("balance_toman")
    if q:
        items = items.filter(Q(business__name__icontains=q) | Q(business__user__email__icontains=q))
    s = BillingSettings.get()
    waiting = services.waiting_payment()
    items = list(items)
    for w in items:
        w.waiting = waiting.get(str(w.business_id))
    return render(request, "ops/wallets.html", {"nav": "wallets", "wallets": items, "q": q, "settings": s})


@staff_required
def wallet_detail(request, business_id):
    business = get_object_or_404(Business, pk=business_id)
    wallet = services.ensure_wallet(business)
    form = WalletTransactionForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        services.apply_transaction(business, form.cleaned_data["amount_toman"], form.cleaned_data["kind"],
                                   form.cleaned_data["description"] or "ثبت توسط مدیر", user=request.user)
        messages.success(request, "تراکنش ثبت شد.")
        return redirect("ops:wallet_detail", business_id=business.pk)
    return render(request, "ops/wallet_detail.html", {
        "nav": "wallets", "business": business, "wallet": wallet, "form": form,
        "transactions": wallet.transactions.select_related("created_by")[:200],
        "blocked": services.is_blocked(business),
        "waiting": services.waiting_payment().get(str(business.pk)),
    })


@staff_required
@require_POST
def charge_now(request):
    r = services.charge_pending_usage(grace_seconds=0)
    messages.success(request, f"{r['rows']} ردیف هزینه پردازش شد؛ {r['businesses']} فروشنده، {r['toman']:,.0f} تومان.")
    return _next(request, "ops:wallets")


# ── Telegram sources ───────────────────────────────────────────────────────────
@staff_required
def communities(request):
    form = GlobalSourceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        link = form.cleaned_data["handle_or_link"]
        if MonitoredCommunity.objects.filter(business__isnull=True, normalized_link=normalize_link(link)).exists():
            messages.error(request, "این گروه قبلاً به‌عنوان منبع عمومی ثبت شده است.")
        else:
            MonitoredCommunity.objects.create(handle_or_link=link, name=form.cleaned_data["name"])
            messages.success(request, "منبع عمومی اضافه شد؛ کراولر حداکثر تا ۳۰ ثانیه‌ی دیگر به آن وصل می‌شود.")
        return redirect("ops:communities")
    status = request.GET.get("status", "")
    scope = request.GET.get("scope", "")
    groups = services.community_groups(status, scope)
    every = services.community_groups() if (status or scope) else groups
    summary = {
        "groups": len(every), "orphans": sum(g["orphan"] for g in every),
        "shared": sum(len(g["private"]) > 1 or bool(g["global"] and g["private"]) for g in every),
        "toman": sum(g["stats"].get("toman", 0) for g in every), "billed": sum(g["billed_toman"] for g in every),
        "opportunities": sum(g["stats"].get("seller_opportunities", 0) for g in every),
    }
    return render(request, "ops/communities.html", {
        "nav": "communities", "groups": groups, "form": form, "status": status, "scope": scope, "summary": summary,
        "statuses": MonitoredCommunity.SYNC_STATUS_CHOICES,
    })


@staff_required
@require_POST
def community_adopt(request, chat_id):
    """A group the crawler watches without a panel source (e.g. the groups file) → global source, so it is analysed."""
    try:
        chat_id = int(chat_id)
    except ValueError:
        raise Http404
    link = request.POST.get("link", "").strip()
    if not normalize_link(link):
        messages.error(request, "این گروه لینک یا آیدی عمومی ندارد؛ آن را با لینک دعوت از فرم بالا اضافه کنید.")
        return redirect("ops:communities")
    if MonitoredCommunity.objects.filter(scope=GLOBAL, telegram_chat_id=chat_id).exists():
        messages.info(request, "این گروه قبلاً منبع عمومی است.")
        return redirect("ops:communities")
    MonitoredCommunity.objects.create(handle_or_link=link, name=request.POST.get("title", "")[:255], telegram_chat_id=chat_id)
    messages.success(request, "به منابع عمومی اضافه شد؛ از این به بعد پیام‌هایش برای همه‌ی فروشنده‌ها تحلیل می‌شود.")
    return redirect("ops:communities")


@staff_required
@require_POST
def community_toggle(request, pk):
    c = get_object_or_404(MonitoredCommunity, pk=pk)
    c.is_active = not c.is_active
    if c.is_active and c.sync_status in ("PAUSED", "ERROR"):
        c.sync_status, c.sync_error = "PENDING", ""
    c.save(update_fields=["is_active", "sync_status", "sync_error"])
    return _next(request, "ops:communities")


@staff_required
@require_POST
def community_delete(request, pk):
    c = get_object_or_404(MonitoredCommunity, pk=pk)
    c.delete()
    messages.success(request, f"«{c.display_name}» حذف شد.")
    return redirect("ops:communities")


# ── settings ───────────────────────────────────────────────────────────────────
@staff_required
def billing_settings(request):
    form = BillingSettingsForm(request.POST or None, instance=BillingSettings.get())
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "تنظیمات ذخیره شد.")
        return redirect("ops:settings")
    return render(request, "ops/form.html", {"nav": "settings", "form": form, "back": "ops:dashboard",
                                              "title": "تنظیمات پرداخت به‌ازای مصرف",
                                              "intro": "مبلغ کسرشده از فروشنده = هزینه‌ی واقعی دلاری × نرخ دلار × (۱ + درصد سود)"})

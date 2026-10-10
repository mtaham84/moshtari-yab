from django import forms

from apps.discovery.sources import normalize_link

from .models import AIModel, BillingSettings, Provider, WalletTransaction


class StyledMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for f in self.fields.values():
            if not isinstance(f.widget, forms.CheckboxInput):
                f.widget.attrs.setdefault("class", "ops-input")


class ProviderForm(StyledMixin, forms.ModelForm):
    api_key = forms.CharField(label="API Key", required=False, widget=forms.PasswordInput(render_value=False),
                              help_text="برای ویرایش، خالی بگذارید تا کلید فعلی حفظ شود")

    class Meta:
        model = Provider
        fields = ["name", "base_url", "api_key", "is_active", "notes"]

    def clean_api_key(self):
        key = (self.cleaned_data.get("api_key") or "").strip()
        if not key and self.instance.pk:
            return self.instance.api_key
        return key

    def clean_base_url(self):
        return self.cleaned_data["base_url"].strip().rstrip("/")


class AIModelForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = AIModel
        fields = ["provider", "name", "label", "input_price_usd", "output_price_usd", "use_extract", "use_verify",
                  "use_reply", "priority", "rpm", "tpm", "rpd", "is_active"]


class BillingSettingsForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = BillingSettings
        fields = ["usd_to_toman", "markup_percent", "charge_cached", "enforce_balance", "min_balance_toman",
                  "signup_credit_toman"]


class WalletTransactionForm(StyledMixin, forms.Form):
    KINDS = [(k, v) for k, v in WalletTransaction.KIND_CHOICES if k != WalletTransaction.USAGE]
    kind = forms.ChoiceField(label="نوع", choices=KINDS, initial=WalletTransaction.TOPUP)
    amount_toman = forms.DecimalField(label="مبلغ (تومان)", max_digits=16, decimal_places=0,
                                      help_text="برای کسر دستی، عدد منفی با نوع «اصلاح دستی»")
    description = forms.CharField(label="توضیح", max_length=255, required=False)

    def clean(self):
        data = super().clean()
        amount, kind = data.get("amount_toman"), data.get("kind")
        if amount is not None:
            if amount == 0:
                self.add_error("amount_toman", "مبلغ نمی‌تواند صفر باشد.")
            elif amount < 0 and kind != WalletTransaction.ADJUST:
                self.add_error("amount_toman", "مبلغ منفی فقط با نوع «اصلاح دستی» مجاز است.")
        return data


class GlobalSourceForm(StyledMixin, forms.Form):
    handle_or_link = forms.CharField(label="آیدی یا لینک گروه/کانال", max_length=255,
                                     widget=forms.TextInput(attrs={"placeholder": "@group یا https://t.me/+invite"}))
    name = forms.CharField(label="نام (اختیاری)", max_length=255, required=False,
                           widget=forms.TextInput(attrs={"placeholder": "نام (اختیاری)"}))

    def clean_handle_or_link(self):
        link = self.cleaned_data["handle_or_link"].strip()
        if not normalize_link(link):
            raise forms.ValidationError("لینک یا آیدی معتبر نیست.")
        return link

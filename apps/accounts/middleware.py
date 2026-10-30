from django.shortcuts import redirect

# URL prefixes a seller may open before the guided setup is finished
_ALLOWED = ("/accounts/onboarding/", "/accounts/categories/", "/accounts/logout/", "/accounts/login/", "/static/", "/media/",
            "/admin/", "/manage/", "/ops/", "/shop/", "/p/", "/r/")
_GUARDED = ("/accounts/dashboard/", "/accounts/settings/", "/products/", "/discovery/")


class OnboardingRequiredMiddleware:
    """New sellers must choose their business type and categories before using the panel."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        path = request.path
        if (user is not None and user.is_authenticated and not (user.is_staff or user.is_superuser)
                and path.startswith(_GUARDED) and not path.startswith(_ALLOWED)):
            business = getattr(user, "business", None)
            if business is not None and not business.onboarding_done:
                return redirect("accounts:onboarding")
        return self.get_response(request)

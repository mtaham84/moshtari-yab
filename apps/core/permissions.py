"""Who counts as an operator (staff or superuser) — one definition for every view."""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied


def is_operator(user) -> bool:
    """Staff and superusers may use the operator panels (/manage/, /ops/)."""
    return bool(user is not None and user.is_authenticated and (user.is_staff or user.is_superuser))


def operator_required(view):
    """Anonymous -> login page; signed-in non-operator -> 403."""
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        if not is_operator(request.user):
            raise PermissionDenied("دسترسی به بخش مدیریت برای حساب کاربری شما مجاز نمی‌باشد.")
        return view(request, *args, **kwargs)
    return wrapper

from django.urls import path
from . import manage_views

app_name = "manage"

urlpatterns = [
    path("", manage_views.manage_dashboard, name="dashboard"),
    path("accounts/add/", manage_views.manage_account_add, name="account_add"),
    path("accounts/<int:pk>/delete/", manage_views.manage_account_delete, name="account_delete"),
    path("accounts/<int:pk>/test/", manage_views.manage_account_test, name="account_test"),
    path("accounts/<int:pk>/code/", manage_views.manage_account_submit_code, name="account_submit_code"),
]

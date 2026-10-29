from django.urls import path
from . import manage_views

app_name = "manage"

urlpatterns = [
    path("", manage_views.manage_dashboard, name="dashboard"),
    path("accounts/add/", manage_views.manage_account_add, name="account_add"),
    path("accounts/<int:pk>/delete/", manage_views.manage_account_delete, name="account_delete"),
    path("accounts/<int:pk>/test/", manage_views.manage_account_test, name="account_test"),
    path("accounts/<int:pk>/session/", manage_views.manage_account_set_session, name="account_set_session"),
    path("accounts/<int:pk>/code/", manage_views.manage_account_submit_code, name="account_submit_code"),
    path("twitter-accounts/add/", manage_views.manage_twitter_account_add, name="twitter_account_add"),
    path("twitter-accounts/<int:pk>/delete/", manage_views.manage_twitter_account_delete, name="twitter_account_delete"),
    path("twitter-accounts/<int:pk>/test/", manage_views.manage_twitter_account_test, name="twitter_account_test"),
    path("twitter-accounts/<int:pk>/session/", manage_views.manage_twitter_account_set_session, name="twitter_account_set_session"),
    path("twitter-accounts/<int:pk>/toggle/", manage_views.manage_twitter_account_toggle, name="twitter_account_toggle"),
    path("leads/export/csv/", manage_views.manage_leads_export_csv, name="leads_export_csv"),
    path("leads/export/json/", manage_views.manage_leads_export_json, name="leads_export_json"),
    path("settings/exchange-rate/", manage_views.manage_exchange_rate_update, name="exchange_rate_update"),
]

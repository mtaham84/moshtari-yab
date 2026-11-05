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
    path("senders/<int:pk>/edit/", manage_views.manage_sender_account_edit, name="sender_account_edit"),
    path("senders/<int:pk>/clear-cooldown/", manage_views.manage_sender_clear_cooldown, name="sender_clear_cooldown"),
    path("twitter-accounts/add/", manage_views.manage_twitter_account_add, name="twitter_account_add"),
    path("twitter-accounts/<int:pk>/delete/", manage_views.manage_twitter_account_delete, name="twitter_account_delete"),
    path("twitter-accounts/<int:pk>/test/", manage_views.manage_twitter_account_test, name="twitter_account_test"),
    path("twitter-accounts/<int:pk>/session/", manage_views.manage_twitter_account_set_session, name="twitter_account_set_session"),
    path("twitter-accounts/<int:pk>/toggle/", manage_views.manage_twitter_account_toggle, name="twitter_account_toggle"),
    path("leads/export/csv/", manage_views.manage_leads_export_csv, name="leads_export_csv"),
    path("leads/export/json/", manage_views.manage_leads_export_json, name="leads_export_json"),
    path("settings/exchange-rate/", manage_views.manage_exchange_rate_update, name="exchange_rate_update"),
    path("proxies/test/", manage_views.manage_proxy_test, name="proxy_test"),
    path("proxies/<int:pk>/test/", manage_views.manage_proxy_test, name="proxy_single_test"),
    path("proxies/channel-sync/", manage_views.manage_channel_proxy_sync, name="proxy_channel_sync"),
    # Instagram Cockpit & Web-Client Simulator
    path("instagram/accounts/<int:pk>/", manage_views.instagram_account_cockpit, name="instagram_account_cockpit"),
    path("instagram/accounts/<int:pk>/api/threads/", manage_views.instagram_cockpit_threads, name="instagram_cockpit_threads"),
    path("instagram/accounts/<int:pk>/api/threads/<str:thread_id>/", manage_views.instagram_cockpit_thread_detail, name="instagram_cockpit_thread_detail"),
    path("instagram/accounts/<int:pk>/api/direct/send/", manage_views.instagram_cockpit_send_direct, name="instagram_cockpit_send_direct"),
    path("instagram/accounts/<int:pk>/api/feed/", manage_views.instagram_cockpit_feed, name="instagram_cockpit_feed"),
    path("instagram/accounts/<int:pk>/api/scroll-feed/", manage_views.instagram_cockpit_scroll_feed, name="instagram_cockpit_scroll_feed"),
    path("instagram/accounts/<int:pk>/api/search/", manage_views.instagram_cockpit_search, name="instagram_cockpit_search"),
    path("instagram/accounts/<int:pk>/api/organic-dm/", manage_views.instagram_cockpit_organic_dm, name="instagram_cockpit_organic_dm"),
]

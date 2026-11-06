from django.urls import path
from . import ig_accounts_views as igv
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
    path("proxies/channels/add/", manage_views.manage_proxy_channel_add, name="proxy_channel_add"),
    path("proxies/channels/<int:pk>/delete/", manage_views.manage_proxy_channel_delete, name="proxy_channel_delete"),
    path("proxies/channels/<int:pk>/toggle/", manage_views.manage_proxy_channel_toggle, name="proxy_channel_toggle"),
    path("proxies/channels/<int:pk>/sync/", manage_views.manage_proxy_channel_sync, name="proxy_channel_sync_single"),
    path("proxies/channels/sync-all/", manage_views.manage_proxy_channels_sync_all, name="proxy_channels_sync_all"),
    # Instagram accounts: the one place to add / log in / connect / export / pause them
    path("instagram/accounts/", igv.ig_accounts_list, name="ig_accounts"),
    path("instagram/accounts/add/", igv.ig_account_add, name="ig_account_add"),
    path("instagram/accounts/heal/", igv.ig_accounts_heal, name="ig_accounts_heal"),
    path("instagram/accounts/configs.txt", igv.ig_accounts_configs_txt, name="ig_accounts_configs_txt"),
    path("instagram/accounts/<int:pk>/settings/", igv.ig_account_detail, name="ig_account_detail"),
    path("instagram/accounts/<int:pk>/login-state/", igv.ig_account_login_state, name="ig_account_login_state"),
    path("instagram/accounts/<int:pk>/config/", igv.ig_account_config, name="ig_account_config"),
    path("instagram/accounts/<int:pk>/config.txt", igv.ig_account_config_txt, name="ig_account_config_txt"),
    path("instagram/accounts/<int:pk>/do/<slug:action>/", igv.ig_account_action, name="ig_account_action"),
    path("instagram/tenants/", igv.ig_tenants, name="ig_tenants"),
    # Instagram Cockpit & Web-Client Simulator
    path("instagram/accounts/<int:pk>/", manage_views.instagram_account_cockpit, name="instagram_account_cockpit"),
    path("instagram/accounts/<int:pk>/api/threads/", manage_views.instagram_cockpit_threads, name="instagram_cockpit_threads"),
    path("instagram/accounts/<int:pk>/api/threads/<str:thread_id>/", manage_views.instagram_cockpit_thread_detail, name="instagram_cockpit_thread_detail"),
    path("instagram/accounts/<int:pk>/api/direct/send/", manage_views.instagram_cockpit_send_direct, name="instagram_cockpit_send_direct"),
    path("instagram/accounts/<int:pk>/api/feed/", manage_views.instagram_cockpit_feed, name="instagram_cockpit_feed"),
    path("instagram/accounts/<int:pk>/api/scroll-feed/", manage_views.instagram_cockpit_scroll_feed, name="instagram_cockpit_scroll_feed"),
    path("instagram/accounts/<int:pk>/api/search/", manage_views.instagram_cockpit_search, name="instagram_cockpit_search"),
    path("instagram/accounts/<int:pk>/api/organic-dm/", manage_views.instagram_cockpit_organic_dm, name="instagram_cockpit_organic_dm"),
    # Instagram Real-Time Live Logs & Activity Radar
    path("instagram/live-logs/", manage_views.instagram_live_logs_api, name="instagram_live_logs_api"),
    path("instagram/live-logs/test-pulse/", manage_views.instagram_live_test_pulse_api, name="instagram_live_test_pulse_api"),
    path("instagram/live-logs/clear/", manage_views.instagram_live_clear_logs_api, name="instagram_live_clear_logs_api"),
]

from django.urls import path
from . import onboarding, shop, views

app_name = "accounts"

urlpatterns = [
    path("signup/", views.signup_view, name="signup"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    path("settings/", views.settings_view, name="settings"),
    path("onboarding/", onboarding.onboarding_view, name="onboarding"),
    path("categories/", onboarding.onboarding_view, name="categories"),
    path("onboarding/api/types/select/", onboarding.api_select_types, name="ob_select_types"),
    path("onboarding/api/tree/<slug:key>/", onboarding.api_type_tree, name="ob_type_tree"),
    path("onboarding/api/adopt/", onboarding.api_adopt, name="ob_adopt"),
    path("onboarding/api/own-tree/", onboarding.api_own_tree, name="ob_own_tree"),
    path("onboarding/api/node/<slug:action>/", onboarding.api_node, name="ob_node"),
    path("onboarding/api/finish/", onboarding.api_finish, name="ob_finish"),
    path("shop/edit/", shop.shop_edit_view, name="shop_edit"),
    path("shop/<str:slug>/", shop.shop_view, name="shop"),
    path("outreach/", views.outreach_view, name="outreach"),
    path("outreach/save-pitch/", views.save_pitch_view, name="save_pitch"),
    path("outreach/add-account/", views.add_outreach_account_view, name="add_outreach_account"),
    path("outreach/generate-pitches/", views.generate_pitches_ajax_view, name="generate_pitches_ajax"),
    path("outreach/sync-replies/", views.sync_replies_view, name="sync_replies"),
    path("outreach/send-followup/", views.send_followup_view, name="send_followup"),
    path("settings/style-sample/", views.style_sample_view, name="style_sample"),
]

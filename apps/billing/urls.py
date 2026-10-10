from django.urls import path

from . import views

app_name = "ops"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("costs/", views.costs, name="costs"),
    path("providers/", views.providers, name="providers"),
    path("providers/new/", views.provider_edit, name="provider_new"),
    path("providers/import-env/", views.provider_import_env, name="provider_import_env"),
    path("providers/<int:pk>/", views.provider_edit, name="provider_edit"),
    path("providers/<int:pk>/delete/", views.provider_delete, name="provider_delete"),
    path("providers/<int:pk>/test/", views.provider_test, name="provider_test"),
    path("models/new/", views.model_edit, name="model_new"),
    path("models/<int:pk>/", views.model_edit, name="model_edit"),
    path("models/<int:pk>/delete/", views.model_delete, name="model_delete"),
    path("models/<int:pk>/toggle/", views.model_toggle, name="model_toggle"),
    path("wallets/", views.wallets, name="wallets"),
    path("wallets/charge/", views.charge_now, name="charge_now"),
    path("wallets/<int:business_id>/", views.wallet_detail, name="wallet_detail"),
    path("communities/", views.communities, name="communities"),
    path("communities/adopt/<str:chat_id>/", views.community_adopt, name="community_adopt"),
    path("communities/<int:pk>/toggle/", views.community_toggle, name="community_toggle"),
    path("communities/<int:pk>/delete/", views.community_delete, name="community_delete"),
    path("settings/", views.billing_settings, name="settings"),
]

from django.urls import path
from . import views

app_name = "discovery"

urlpatterns = [
    path("leads/", views.leads_list_view, name="leads_list"),
    path("scan/trigger/", views.trigger_scan_view, name="trigger_scan"),
    path("products/<int:product_id>/toggle-discovery/", views.toggle_product_discovery_view, name="toggle_discovery"),
    path("leads/<int:lead_id>/outreach/", views.send_outreach_view, name="send_outreach"),
    path("leads/<int:lead_id>/status/", views.update_lead_status_view, name="update_lead_status"),
]

from django.urls import path
from . import views

app_name = "discovery"

urlpatterns = [
    # Opportunities (Unified AI Contract Layer & Panel)
    path("opportunities/", views.opportunity_list_view, name="opportunity_list"),
    path("opportunities/<int:pk>/", views.opportunity_detail_view, name="opportunity_detail"),
    path("opportunities/<int:pk>/status/", views.opportunity_status_update_view, name="opportunity_status_update"),
    path("api/opportunities/process/", views.api_opportunity_process_view, name="opportunity_api_process"),
    path("api/candidates/", views.api_opportunity_process_view, name="api_candidate_intake"),

    # Monitored Online Communities (Telegram)
    path("communities/", views.communities_list_view, name="communities_list"),
    path("communities/<int:community_id>/toggle/", views.community_toggle_status_view, name="community_toggle"),
    path("communities/<int:community_id>/delete/", views.community_delete_view, name="community_delete"),

    # Legacy / Crawler Endpoints
    path("leads/", views.leads_list_view, name="leads_list"),
    path("scan/trigger/", views.trigger_scan_view, name="trigger_scan"),
    path("products/<int:product_id>/toggle-discovery/", views.toggle_product_discovery_view, name="toggle_discovery"),
    path("leads/<int:lead_id>/outreach/", views.send_outreach_view, name="send_outreach"),
    path("leads/<int:lead_id>/status/", views.update_lead_status_view, name="update_lead_status"),
    path("api/agent/feed/", views.api_agent_discovery_feed_view, name="api_agent_feed"),
    path("api/leads/submit/", views.api_submit_lead_view, name="api_submit_lead"),
    path("api/analytics/", views.api_analytics_report_view, name="api_analytics"),
]

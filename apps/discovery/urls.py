from django.urls import path

from . import views

app_name = "discovery"

urlpatterns = [
    path("opportunities/", views.opportunity_list_view, name="opportunity_list"),
    path("opportunities/export/", views.opportunity_export_view, name="opportunity_export"),
    path("opportunities/<int:pk>/", views.opportunity_detail_view, name="opportunity_detail"),
    path("opportunities/new-count/", views.opportunity_new_count_view, name="opportunity_new_count"),
    path("opportunities/<int:pk>/rewrite/", views.opportunity_rewrite_view, name="opportunity_rewrite"),
    path("opportunities/<int:pk>/status/", views.opportunity_status_update_view, name="opportunity_status_update"),
    path("opportunities/<int:pk>/feedback/", views.opportunity_feedback_view, name="opportunity_feedback"),
    path("communities/", views.communities_list_view, name="communities_list"),
    path("x-management/", views.x_management_view, name="x_management"),
    path("x/", views.x_search_view, name="x_search"),
    path("x/<int:pk>/", views.x_product_view, name="x_product"),
    path("communities/<int:community_id>/toggle/", views.community_toggle_status_view, name="community_toggle"),
    path("communities/<int:community_id>/delete/", views.community_delete_view, name="community_delete"),
    path("api/analytics/", views.api_analytics_report_view, name="api_analytics"),
]

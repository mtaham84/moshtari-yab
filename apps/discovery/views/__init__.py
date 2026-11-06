"""Seller-panel discovery views (urls.py imports them from here).

Only the public views are re-exported; helpers live in (and are patched on) their own modules,
e.g. ``apps.discovery.views.instagram._instagram_change_allowed``."""

from .analytics import (
    api_analytics_report_view,
    api_leaf_category_stats_view,
)
from .community import (
    communities_list_view,
    community_delete_view,
    community_toggle_status_view,
)
from .instagram import (
    instagram_comment_detail_view,
    instagram_comment_report_api_view,
    instagram_pages_view,
    instagram_post_report_api_view,
    instagram_post_review_view,
)
from .opportunities import (
    opportunity_detail_view,
    opportunity_export_view,
    opportunity_feedback_view,
    opportunity_list_view,
    opportunity_new_count_view,
    opportunity_reply_suggestions_view,
    opportunity_rewrite_view,
    opportunity_status_update_view,
)
from .x_twitter import (
    x_management_view,
    x_product_view,
    x_search_view,
)

__all__ = [
    "api_analytics_report_view",
    "api_leaf_category_stats_view",
    "communities_list_view",
    "community_delete_view",
    "community_toggle_status_view",
    "instagram_comment_detail_view",
    "instagram_comment_report_api_view",
    "instagram_pages_view",
    "instagram_post_report_api_view",
    "instagram_post_review_view",
    "opportunity_detail_view",
    "opportunity_export_view",
    "opportunity_feedback_view",
    "opportunity_list_view",
    "opportunity_new_count_view",
    "opportunity_reply_suggestions_view",
    "opportunity_rewrite_view",
    "opportunity_status_update_view",
    "x_management_view",
    "x_product_view",
    "x_search_view",
]

import logging

from apps.core.agent import drafts_for, rewrite_reply

from .analytics import (
    api_analytics_report_view,
    api_leaf_category_stats_view,
)
from .community import (
    _validate_private_source,
    communities_list_view,
    community_delete_view,
    community_toggle_status_view,
)
from .instagram import (
    INSTAGRAM_PAGE_CHANGE_LIMIT,
    _bounded_post_int,
    _instagram_change_allowed,
    _instagram_list,
    _instagram_post_review_context,
    _page_ref_error,
    instagram_comment_detail_view,
    instagram_comment_report_api_view,
    instagram_pages_view,
    instagram_post_report_api_view,
    instagram_post_review_view,
)
from .opportunities import (
    _PLATFORM_ID_PREFIXES,
    _filter_opportunities_queryset,
    _safe_csv_cell,
    _strip_platform_prefix,
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
    _x_seller_business,
    _x_wallet_empty,
    x_management_view,
    x_product_view,
    x_search_view,
)

logger = logging.getLogger(__name__)

__all__ = [
    # Opportunities views & helpers
    "_strip_platform_prefix",
    "_safe_csv_cell",
    "_filter_opportunities_queryset",
    "opportunity_list_view",
    "opportunity_export_view",
    "opportunity_detail_view",
    "opportunity_rewrite_view",
    "opportunity_reply_suggestions_view",
    "opportunity_new_count_view",
    "opportunity_status_update_view",
    "opportunity_feedback_view",
    # Instagram views & helpers
    "_instagram_list",
    "_bounded_post_int",
    "_instagram_change_allowed",
    "_page_ref_error",
    "instagram_pages_view",
    "instagram_comment_report_api_view",
    "_instagram_post_review_context",
    "instagram_post_review_view",
    "instagram_post_report_api_view",
    "instagram_comment_detail_view",
    "INSTAGRAM_PAGE_CHANGE_LIMIT",
    # X / Twitter views & helpers
    "x_management_view",
    "_x_seller_business",
    "_x_wallet_empty",
    "x_search_view",
    "x_product_view",
    # Community views & helpers
    "_validate_private_source",
    "communities_list_view",
    "community_toggle_status_view",
    "community_delete_view",
    # Analytics views
    "api_analytics_report_view",
    "api_leaf_category_stats_view",
    # Test patch support & globals
    "drafts_for",
    "rewrite_reply",
    "logger",
    "_PLATFORM_ID_PREFIXES",
]

"""UNVERIFIED HikerAPI contract assumptions; check current provider docs.

References are also listed in docs/IG_COLLECTION_ANALYSIS.md. Keep every endpoint,
parameter, and response-path assumption in this mapping module.
"""

BASE_URL = "https://api.hikerapi.com"
USER_STREAM_ENDPOINT = "/a2/user"
USER_STREAM_PARAMS = {"username": "username"}
USER_MEDIA_PATH = ("graphql", "user", "edge_owner_to_timeline_media", "edges")
USER_MEDIA_NODE_PATH = ("node",)

HASHTAG_POSTS_ENDPOINT = "/v2/hashtag/medias/recent"
HASHTAG_PARAMS = {"tag": "name", "cursor": "page_id"}
HASHTAG_MEDIA_PATH = ("response", "sections")
HASHTAG_MEDIA_CONTAINER_PATH = ("layout_content", "medias")
HASHTAG_MEDIA_NODE_PATH = ("media",)

COMMENTS_ENDPOINT = "/v2/media/comments"
COMMENTS_PARAMS = {"post_id": "id", "cursor": "page_id"}
COMMENTS_PATH = ("response", "comments")
NEXT_PAGE_PATH = ("next_page_id",)

MEDIA_ID_KEYS = ("id", "pk", "media_id")
SHORTCODE_KEYS = ("shortcode", "code")
CAPTION_PATHS = (("caption", "text"), ("edge_media_to_caption", "edges", 0, "node", "text"))
AUTHOR_PATHS = (("user", "username"), ("owner", "username"), ("owner_profile", "username"))
AUTHOR_ID_PATHS = (("user", "pk"), ("user", "id"), ("owner", "pk"), ("owner", "id"))
AUTHOR_NAME_PATHS = (("user", "full_name"), ("owner", "full_name"))
CREATED_AT_KEYS = ("taken_at", "taken_at_ts", "date")
LIKE_COUNT_KEYS = ("like_count", "likes", "play_count")
COMMENT_COUNT_KEYS = ("comment_count", "comments")
MEDIA_TYPE_KEYS = ("product_type", "media_type", "__typename")
HASHTAGS_KEYS = ("caption_hashtags",)

COMMENT_ID_KEYS = ("pk", "id", "comment_id")
COMMENT_TEXT_PATHS = (("text",),)
COMMENT_AUTHOR_PATHS = (("user", "username"), ("owner", "username"))
COMMENT_AUTHOR_ID_PATHS = (("user", "pk"), ("user", "id"), ("owner", "pk"), ("owner", "id"))
COMMENT_AUTHOR_NAME_PATHS = (("user", "full_name"), ("owner", "full_name"))
COMMENT_CREATED_AT_KEYS = ("created_at_utc", "created_at")

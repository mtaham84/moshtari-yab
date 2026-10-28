"""
Standalone moderation bridge for instagram_engine.
"""
from apps.discovery.comment_moderation import is_toxic_or_spam_comment

__all__ = ["is_toxic_or_spam_comment"]

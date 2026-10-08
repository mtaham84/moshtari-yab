"""Convert telegram_crawler.LeadContext objects into the shared SocialMessage format."""

from __future__ import annotations

from analysis.schemas import Author, ContextMessage, SocialMessage


def telegram_uid(group_id: int, message_id: int) -> str:
    return f"telegram:{group_id}:{message_id}"


def telegram_message_url(group, message_id: int) -> str | None:
    """Public groups: t.me/<username>/<id>. Private supergroups: t.me/c/<id>/<msg>
    (members only). Basic (legacy) groups have no message links."""
    if getattr(group, "username", None):
        return f"https://t.me/{group.username}/{message_id}"
    if getattr(group, "is_supergroup", True):
        return f"https://t.me/c/{group.group_id}/{message_id}"
    return None


def _ctx(snippet, relation: str) -> ContextMessage:
    return ContextMessage(
        id=str(snippet.message_id),
        relation=relation,
        author_name=snippet.sender_name,
        text=snippet.text,
        created_at=snippet.date,
    )


def lead_context_to_social_message(lead) -> SocialMessage:
    """``lead`` is a telegram_crawler.models.LeadContext (imported lazily to keep
    the analysis package free of Telethon)."""
    target = lead.target_message
    user = lead.user
    context = [_ctx(m, "parent") for m in lead.reply_thread.parent_messages]
    parent_ids = {c.id for c in context}
    context += [_ctx(m, "previous") for m in lead.previous_messages if str(m.message_id) not in parent_ids]
    context += [_ctx(m, "reply") for m in lead.reply_thread.child_replies]
    return SocialMessage(
        uid=telegram_uid(lead.group.group_id, target.message_id),
        source="telegram",
        channel_id=str(lead.group.group_id),
        channel_title=lead.group.title,
        text=target.text,
        author=Author(
            id=str(user.user_id) if user.user_id else None,
            handle=f"@{user.username}" if user.username else None,
            display_name=user.display_name,
            is_bot=user.is_bot,
        ),
        created_at=target.date,
        url=telegram_message_url(lead.group, target.message_id),
        context=context,
        metadata={"reply_to_msg_id": target.reply_to_msg_id},
    )

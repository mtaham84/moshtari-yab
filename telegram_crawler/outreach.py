"""Consume approved DMs using the crawler's existing authorized Telethon client.

Exactly one crawler instance owns the session. No login, second session, bulk send,
or automatic creation of messages happens here.
"""
import asyncio
import logging
import os
from functools import partial

from asgiref.sync import sync_to_async
from telethon import errors, functions, types

log = logging.getLogger(__name__)


async def resolve_recipient(client, job):
    user_id = job["recipient_user_id"]
    try:
        entity = await client.get_entity(types.PeerUser(user_id))
    except ValueError:
        if not job["recipient_username"]:
            raise
        entity = await client.get_entity(job["recipient_username"])
    # A username may have changed owners. Never send to a different user or a group.
    if (not isinstance(entity, types.User) or entity.id != user_id
            or entity.bot or entity.deleted or entity.is_self):
        raise ValueError("Recipient cannot be verified")
    return entity


async def deliver(client, job, finish):
    try:
        recipient = await resolve_recipient(client, job)
        result = await client(
            functions.messages.SendMessageRequest(
                peer=await client.get_input_entity(recipient), message=job["body"],
                random_id=job["random_id"], no_webpage=True,
            ),
            flood_sleep_threshold=0,
        )
    except ValueError:
        await finish(job["id"], "FAILED", "PEER_UNAVAILABLE")
    except errors.FloodWaitError as exc:
        wait = max(1, exc.seconds) + 1
        await finish(job["id"], "PENDING", "RATE_LIMIT", wait=wait)
        return wait
    except errors.PeerFloodError:
        await finish(job["id"], "FAILED", "ACCOUNT_LIMITED")
        # Pause the shared sender, not just one recipient.
        return 3600
    except (errors.UserPrivacyRestrictedError, errors.UserIsBlockedError,
            errors.YouBlockedUserError, errors.ChatWriteForbiddenError):
        await finish(job["id"], "FAILED", "PRIVACY")
    except errors.RPCError:
        await finish(job["id"], "FAILED", "REJECTED")
    except Exception:
        # The request may already have reached Telegram; never auto-retry ambiguity.
        log.warning("Telegram outbox job %s has an unknown outcome", job["id"])
        await finish(job["id"], "UNKNOWN", "NETWORK_UNKNOWN")
    else:
        message_id = getattr(result, "id", None)
        if message_id is None:
            message_id = next((
                update.id for update in getattr(result, "updates", [])
                if isinstance(update, types.UpdateMessageID) and update.random_id == job["random_id"]
            ), None)
        if message_id is None:
            await finish(job["id"], "UNKNOWN", "NETWORK_UNKNOWN")
        else:
            await finish(job["id"], "SENT", message_id=message_id)
    return 0


async def run_outbox(client):
    import django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    django.setup()
    from django.conf import settings
    from apps.discovery.telegram_outbox import claim_next, finish, worker_db_call

    if not settings.TELEGRAM_OUTREACH_ENABLED:
        return
    claim_async = sync_to_async(partial(worker_db_call, claim_next), thread_sensitive=True)
    finish_async = sync_to_async(partial(worker_db_call, finish), thread_sensitive=True)
    # Spacing is an operational guard, not a promise against Telegram restrictions.
    interval = max(1, settings.TELEGRAM_OUTREACH_INTERVAL)
    log.info("Manual Telegram outbox enabled; only seller-approved messages are sent")
    while True:
        try:
            job = await claim_async()
            if job is None:
                await asyncio.sleep(3)
                continue
            wait = await deliver(client, job, finish_async)
            await asyncio.sleep(max(interval, wait))
        except asyncio.CancelledError:
            raise
        except Exception:
            # Schema may not have migrated yet. Keep archiving running; no message text in logs.
            log.exception("Telegram outbox worker error")
            await asyncio.sleep(30)
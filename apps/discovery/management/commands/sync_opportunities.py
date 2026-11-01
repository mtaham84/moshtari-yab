"""Import opportunities published by need_engine (``need_engine.opportunities``) into the panel.

    python manage.py sync_opportunities              # import new/changed opportunities once
    python manage.py sync_opportunities --follow     # keep importing while the engine runs
    python manage.py sync_opportunities --from-start # re-import everything (idempotent)

Every round also charges sellers' wallets for new LLM costs (pay-as-you-go, apps/billing) unless --no-billing.
"""
import logging
import time

from django.core.management.base import BaseCommand

from apps.discovery.engine_bridge import import_opportunity, published_after, scrub_opportunity
from apps.billing.services import charge_pending_usage, heartbeat
from apps.discovery.models import EngineSyncCursor

log = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Import opportunities published by need_engine into the panel database."

    def add_arguments(self, parser):
        parser.add_argument("--follow", action="store_true")
        parser.add_argument("--interval", type=float, default=10.0)
        parser.add_argument("--from-start", action="store_true")
        parser.add_argument("--no-billing", action="store_true", help="do not charge wallets in this process")
        parser.add_argument(
            "--replay-quarantine",
            action="store_true",
            help="Attempt to replay previously quarantined poison payloads (P08.02 / R41).",
        )

    def replay_quarantined(self) -> int:
        from django.db import transaction
        from django.db.models import F
        from apps.discovery.models import QuarantinedSyncPayload
        from apps.core.typed_errors import sanitize_text

        items = QuarantinedSyncPayload.objects.filter(
            status__in=["QUARANTINED", "RETRYING"],
            retry_count__lt=F("max_retries"),
        ).order_by("seq")

        replayed = 0
        for item in items:
            item.retry_count += 1
            try:
                with transaction.atomic():
                    if isinstance(item.payload, dict) and item.payload.get("retention_scrubbed"):
                        scrub_opportunity(item.payload)
                    else:
                        import_opportunity(item.payload)
                    item.status = "RESOLVED"
                    item.save(update_fields=["status", "retry_count", "updated_at"])
                    replayed += 1
                    self.stdout.write(self.style.SUCCESS(f"Replayed quarantined payload seq={item.seq} successfully."))
            except Exception as exc:
                if item.retry_count >= item.max_retries:
                    item.status = "FAILED_EXHAUSTED"
                else:
                    item.status = "QUARANTINED"
                item.error_reason = sanitize_text(str(exc))
                item.error_type = type(exc).__name__
                item.save(update_fields=["status", "retry_count", "error_reason", "error_type", "updated_at"])
                self.stderr.write(self.style.WARNING(f"Replay failed for seq={item.seq} (attempt {item.retry_count}): {exc}"))
        return replayed

    def handle(self, *args, **opts):
        if opts.get("replay_quarantine"):
            count = self.replay_quarantined()
            self.stdout.write(f"Replayed {count} quarantined payloads.")
            return

        cursor, _ = EngineSyncCursor.objects.get_or_create(name="opportunities")
        if opts["from_start"]:
            cursor.position = 0
        while True:
            n = 0
            while True:
                batch = published_after(cursor.position)
                for seq, payload in batch:
                    try:
                        from django.db import transaction
                        with transaction.atomic():
                            if payload.get("retention_scrubbed"):
                                scrub_opportunity(payload)
                            else:
                                import_opportunity(payload)
                        cursor.position = seq
                        n += 1
                    except Exception as exc:
                        from apps.core.typed_errors import log_sanitized, sanitize_text
                        from apps.discovery.models import QuarantinedSyncPayload

                        opp_id = str(payload.get("opportunity_id", "")) if isinstance(payload, dict) else ""
                        log_sanitized(
                            log,
                            logging.ERROR,
                            "sync_opportunity_quarantined",
                            operation_id=f"sync_seq_{seq}",
                            error=exc,
                            extra={"seq": seq, "opportunity_id": opp_id},
                        )
                        QuarantinedSyncPayload.objects.update_or_create(
                            seq=seq,
                            defaults={
                                "opportunity_id": opp_id,
                                "payload": payload if isinstance(payload, (dict, list)) else {"raw": str(payload)},
                                "error_reason": sanitize_text(str(exc)),
                                "error_type": type(exc).__name__,
                                "status": "QUARANTINED",
                            },
                        )
                        cursor.position = seq
                        self.stderr.write(
                            self.style.WARNING(
                                f"Quarantined poison payload seq={seq} opp={opp_id}: {type(exc).__name__}: {sanitize_text(exc)}"
                            )
                        )
                if batch:
                    cursor.save(update_fields=["position", "updated_at"])
                if len(batch) < 500:
                    break
            if n:
                self.stdout.write(f"imported {n} opportunity updates")
            if not opts["no_billing"]:
                try:
                    charge_pending_usage()
                except Exception as billing_exc:   # billing must never stop the import
                    from apps.core.typed_errors import log_sanitized, ErrorCode
                    log_sanitized(
                        log,
                        logging.ERROR,
                        "sync_opportunities.charging_wallets_failed",
                        error=billing_exc,
                        extra={"operation": "charge_pending_usage", "error_code": ErrorCode.INTERNAL.value},
                    )
            heartbeat("sync")
            if not opts["follow"]:
                break
            time.sleep(opts["interval"])

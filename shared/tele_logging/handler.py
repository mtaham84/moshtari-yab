from __future__ import annotations

import logging
import queue
import threading
from typing import Any

from shared.tele_logging.formatter import format_error_alert
from shared.tele_logging.sender import send_telegram_raw
from shared.tele_logging.throttler import ErrorThrottler

_log = logging.getLogger("shared.tele_logging.handler")


class AsyncTelegramHandler(logging.Handler):
    """Asynchronous, non-blocking logging handler that dispatches error alerts to Telegram.

    Features:
    - Zero performance overhead on the caller thread via non-blocking memory queue.
    - Dedicated daemon worker thread.
    - Anti-loop recursion protection: ignores errors originating from tele_logging or HTTP requests to Telegram.
    - Rate-limiting & deduplication to avoid log storms.
    - Tehran timezone & Jalali timestamp formatting with detailed root-cause diagnosis.
    """

    def __init__(self, cooldown_seconds: float = 900.0, queue_maxsize: int = 1000) -> None:
        super().__init__(level=logging.ERROR)
        self.queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=max(100, queue_maxsize))
        self.throttler = ErrorThrottler(cooldown_seconds=cooldown_seconds)
        self._worker_thread = threading.Thread(
            target=self._worker_loop,
            daemon=True,
            name="AsyncTelegramLogWorker",
        )
        self._worker_thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        """Pushes error record to the background queue without blocking caller."""
        if record.levelno < logging.ERROR:
            return

        # Prevent recursive loops from tele_logging itself
        if record.name and record.name.startswith("shared.tele_logging"):
            return

        # Suppress urllib3/requests logs talking to Telegram API to prevent infinite loop
        if record.name and any(record.name.startswith(p) for p in ("urllib3", "requests")):
            msg_str = str(record.msg or "")
            if "api.telegram.org" in msg_str:
                return

        # Extract HTTP request info safely if present (e.g. from Django request)
        req_info = None
        req = getattr(record, "request", None)
        if req is not None:
            try:
                method = getattr(req, "method", "")
                path = getattr(req, "path", "")
                user = getattr(req, "user", None)
                user_str = str(user) if user and getattr(user, "is_authenticated", False) else "Anonymous"
                req_info = f"{method} {path} (User: {user_str})"
            except Exception:   # never fail a log call over request details
                self.handleError(record)

        # Extract exception details
        exc_type_name = None
        exc_msg = None
        exc_tb = None
        if record.exc_info and len(record.exc_info) == 3:
            etype, evalue, etb = record.exc_info
            if etype:
                exc_type_name = etype.__name__ if hasattr(etype, "__name__") else str(etype)
            if evalue:
                exc_msg = str(evalue)
            exc_tb = etb

        record_dict: dict[str, Any] = {
            "name": record.name,
            "levelname": record.levelname,
            "module": record.module,
            "pathname": record.pathname,
            "lineno": record.lineno,
            "funcName": record.funcName,
            "message": record.getMessage(),
            "exc_type": exc_type_name,
            "exc_msg": exc_msg,
            "exc_tb": exc_tb,
            "request_info": req_info,
        }

        try:
            self.queue.put_nowait(record_dict)
        except queue.Full:
            # Drop record to protect memory if queue is overwhelmed
            pass

    def _worker_loop(self) -> None:
        """Background thread consuming queued log records and delivering to Telegram."""
        while True:
            try:
                item = self.queue.get()
                if item is None:
                    break

                pathname = item.get("pathname", "")
                lineno = item.get("lineno", 0)
                exc_type = item.get("exc_type")
                message = item.get("message")

                fingerprint = ErrorThrottler.compute_fingerprint(
                    pathname=pathname,
                    lineno=lineno,
                    exc_type=exc_type,
                    msg=message,
                )

                decision = self.throttler.evaluate(fingerprint)
                if decision.should_send:
                    alert_text = format_error_alert(
                        record_dict=item,
                        fingerprint=fingerprint,
                        repeat_count=decision.count,
                    )
                    send_telegram_raw(alert_text)

            except Exception:
                # The delivery thread must never die. This logger's records are skipped by emit(), so no loop.
                _log.warning("Telegram error alert could not be delivered", exc_info=True)
            finally:
                try:
                    self.queue.task_done()
                except ValueError:   # task_done() called more often than get()
                    pass

    def close(self) -> None:
        """Gracefully signals the worker thread to exit."""
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass
        super().close()

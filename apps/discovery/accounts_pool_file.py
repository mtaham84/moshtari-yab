"""Git-ignored JSON file with the Instagram + X/Twitter account pools (credentials, cookies, sessions).

``data/accounts_pool.json`` (``ACCOUNTS_POOL_FILE``) is a restorable copy of the DB pool. It is rewritten
atomically with mode 0600 whenever an account changes (volatile counters are ignored), and
``manage.py accounts_pool import`` loads it back. Import only fills missing accounts/empty fields unless --force.
The file is a secret: it is in .gitignore and .dockerignore.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path

from django.conf import settings

from apps.discovery.ig_session import save_atomic

log = logging.getLogger("accounts.pool")

IG_FIELDS = ("username", "password", "role", "tenant_id", "proxy_url", "session_json", "device_settings", "status", "status_message")
X_FIELDS = ("username", "password", "auth_token", "ct0", "proxy_url", "status", "status_message", "is_active")
_last = {"hash": None}


def pool_path() -> Path:
    return Path(os.environ.get("ACCOUNTS_POOL_FILE") or Path(settings.BASE_DIR) / "data" / "accounts_pool.json")


def snapshot() -> dict:
    from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount

    def rows(model, fields):
        return [{f: getattr(a, f) for f in fields} for a in model.objects.order_by("id")]

    return {"version": 1, "instagram": rows(InstagramEngineAccount, IG_FIELDS), "twitter": rows(TwitterEngineAccount, X_FIELDS)}


def export_pool(force: bool = False) -> bool:
    """Writes the file only if the content changed. Never raises."""
    try:
        data = snapshot()
        digest = hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
        if digest == _last["hash"] and not force:
            return False
        if save_atomic(pool_path(), data):
            _last["hash"] = digest
            return True
    except Exception as exc:  # the DB write that triggered us must never fail because of the backup file
        log.warning("accounts pool export failed: %s", exc.__class__.__name__)
    return False


def import_pool(force: bool = False) -> dict:
    from apps.discovery.models import InstagramEngineAccount, TwitterEngineAccount

    path = pool_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    stats = {"created": 0, "filled": 0, "skipped": 0}
    for model, key, fields in ((InstagramEngineAccount, "instagram", IG_FIELDS), (TwitterEngineAccount, "twitter", X_FIELDS)):
        for row in data.get(key, []):
            username = row.get("username")
            if not username:
                continue
            obj = model.objects.filter(username=username).first()
            if obj is None:
                model.objects.create(**{f: row[f] for f in fields if f in row and row[f] is not None})
                stats["created"] += 1
                continue
            changed = []
            for f in fields:
                if f in ("username",) or row.get(f) in (None, ""):
                    continue
                if force or getattr(obj, f) in (None, "", {}, []):
                    setattr(obj, f, row[f])
                    changed.append(f)
            if changed:
                obj.save(update_fields=changed)
                stats["filled"] += 1
            else:
                stats["skipped"] += 1
    return stats

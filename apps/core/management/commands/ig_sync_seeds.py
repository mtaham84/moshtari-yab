import json
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import F, Q

from django.utils import timezone

from apps.discovery.models import InstagramCategoryTarget, InstagramPage
from apps.products.models import Product
from instagram_discovery.targets import HashtagTarget, build_hashtag_seeds
from shared.instagram import parse_page_ref

class Command(BaseCommand):
    help = "Write account and category hashtag targets to the Instagram collector seed file."

    def add_arguments(self, parser):
        parser.add_argument("--apply-status", action="store_true")

    def handle(self, *args, **options):
        if options["apply_status"]:
            _apply_outcomes()
            return
        budget = max(1, int(os.getenv("IG_MAX_HANDLES_PER_CYCLE", "20")))
        pages = list(InstagramPage.objects.filter(is_active=True).order_by(
            F("last_run_at").asc(nulls_first=True), "created_at", "pk"
        ))
        status = _collector_status()
        cooldown_until = status.get("cooldown_until") if isinstance(status, dict) else None
        if cooldown_until and _future(cooldown_until):
            pages = []
        else:
            pages = [page for page in pages if _ready(page) and _due(page)]
        handles = []
        seen = set()
        for page in pages:
            try:
                handle = parse_page_ref(page.handle)
            except ValueError:
                continue
            if handle not in seen and len(handles) < budget:
                handles.append(handle)
                seen.add(handle)
        selected = [page for page in pages if _canonical(page.handle) in seen]
        selected_ids = {page.pk for page in selected}
        hashtag_seeds = _hashtag_seeds()
        payload = {
            "accounts": [{"username": handle, "limit": max(1, int(os.getenv("IG_MAX_POSTS_PER_RUN", "5")))} for handle in handles],
            "hashtags": hashtag_seeds,
            "comments": {"mode": "all", "policy": "seller_post",
                           "read_hashtag_posts": False,
                           "max_posts_per_run": max(1, int(os.getenv("IG_MAX_POSTS_PER_RUN", "5"))),
                           "max_per_post": max(1, int(os.getenv("IG_MAX_COMMENTS_PER_POST", "30"))),
                           "window_days": max(1, int(os.getenv("IG_COMMENT_WINDOW_DAYS", "7")))},
        }
        target = settings.BASE_DIR / "data" / "ig_seeds.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
        for page in pages:
            if page.pk in selected_ids:
                page.queued_at = timezone.now()
                page.save(update_fields=["queued_at"])
        self.stdout.write(
            f"Instagram seeds: {len(handles)} handles, {len(selected)} pages scheduled, "
            f"{len(hashtag_seeds)} hashtags"
        )


def _canonical(value):
    try:
        return parse_page_ref(value)
    except ValueError:
        return None


def _hashtag_seeds() -> list[dict]:
    targets: list[HashtagTarget] = []
    category_rows = InstagramCategoryTarget.objects.filter(
        is_active=True, category__is_active=True,
    ).filter(
        Q(category__business__isnull=True) | Q(category__business_id=F("business_id")),
    ).select_related("category").order_by("category__full_path", "business_id")
    for target in category_rows:
        if not target.hashtags:
            continue
        products = list(Product.objects.filter(
            business=target.business, category_id=target.category_id,
            status="ACTIVE", is_discovery_active=True, instagram_discovery_enabled=True,
        ).order_by("name"))
        product_terms = [term for product in products for term in product.instagram_terms]
        terms = tuple(dict.fromkeys([*target.category.keywords, *target.terms, *product_terms]))
        targets.append(HashtagTarget(
            business_id=target.business_id,
            category_id=target.category_id, category_name=target.category.name,
            category_path=target.category.full_path, product_ids=tuple(product.pk for product in products),
            product_names=tuple(product.name for product in products), terms=terms,
            hashtags=tuple(target.hashtags),
            max_posts=max(1, min(100, target.max_posts)),
            window_days=max(1, min(90, target.window_days)),
        ))
    product_rows = Product.objects.filter(
        instagram_discovery_enabled=True, instagram_hashtags__isnull=False,
        status="ACTIVE", is_discovery_active=True, category__isnull=False, category__is_active=True,
    ).filter(
        Q(category__business__isnull=True) | Q(category__business_id=F("business_id")),
    ).select_related("business", "category").order_by("category__full_path", "business_id", "name")
    for product in product_rows:
        if not product.instagram_hashtags:
            continue
        terms = tuple(dict.fromkeys([*product.category.keywords, *product.instagram_terms]))
        targets.append(HashtagTarget(
            business_id=product.business_id, category_id=product.category_id,
            category_name=product.category.name, category_path=product.category.full_path,
            product_ids=(product.pk,), product_names=(product.name,), terms=terms,
            hashtags=tuple(product.instagram_hashtags),
            max_posts=max(1, min(100, product.instagram_max_posts)),
            window_days=max(1, min(90, product.instagram_window_days)),
        ))
    return build_hashtag_seeds(targets)


def _ready(page):
    if page.last_status != "private":
        return True
    return page.last_run_at is None or page.last_run_at <= timezone.now() - timedelta(hours=24)


def _due(page):
    interval = max(1, int(os.getenv("IG_CYCLE_MINUTES", "60")))
    if page.queued_at:
        return True
    return page.last_run_at is None or page.last_run_at <= timezone.now() - timedelta(minutes=interval)


def _apply_outcomes():
    configured = Path(os.getenv("IG_COLLECT_OUTPUT_DIR", "data/ig_collected"))
    root = configured if configured.is_absolute() else settings.BASE_DIR / configured
    payload = _collector_status(root)
    if not payload:
        return
    outcomes = payload.get("handles", {})
    if not isinstance(outcomes, dict):
        return
    started_at = _timestamp(payload.get("started_at") or payload.get("updated_at"))
    if started_at is None:
        return
    completed_at = _timestamp(payload.get("updated_at")) or started_at
    for handle, result in outcomes.items():
        if not isinstance(result, dict):
            continue
        status = result.get("status")
        try:
            canonical = parse_page_ref(str(handle))
        except ValueError:
            continue
        final_status = status if status in {"ok", "not_found", "private", "rate_limited", "error"} else "error"
        for page in InstagramPage.objects.filter(
            handle__iexact=canonical, is_active=True, queued_at__isnull=False,
        ):
            if started_at < page.queued_at or completed_at < page.queued_at:
                continue
            page.last_status = final_status
            if final_status == "ok":
                page.consecutive_failures = 0
            elif final_status == "not_found":
                page.consecutive_failures += 1
            else:
                page.consecutive_failures = 0
            if final_status == "not_found" and page.consecutive_failures >= 3:
                page.is_active = False
                result["auto_paused"] = True
            page.last_run_at = completed_at
            page.queued_at = None
            page.save(update_fields=["last_status", "consecutive_failures", "last_run_at",
                                     "queued_at", "is_active"])
    _mark_queued_errors(completed_at, started_at)


def _mark_queued_errors(at, started_at):
    for page in InstagramPage.objects.filter(is_active=True, queued_at__isnull=False, queued_at__lte=at):
        if page.queued_at > started_at:
            continue
        page.last_status = "error"
        page.consecutive_failures = 0
        page.last_run_at = at
        page.queued_at = None
        page.save(update_fields=["last_status", "consecutive_failures", "last_run_at", "queued_at"])


def _collector_status(root=None):
    configured = Path(os.getenv("IG_COLLECT_OUTPUT_DIR", "data/ig_collected"))
    root = root or (configured if configured.is_absolute() else settings.BASE_DIR / configured)
    try:
        return json.loads((root / "status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _future(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed > timezone.now()


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed

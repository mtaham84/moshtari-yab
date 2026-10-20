import json
import logging
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import IntegrityError, transaction

from apps.discovery.models import Customer, InstagramPage, Opportunity, OpportunityProductMatch
from apps.products.models import Product
from need_engine.text import tokens
from shared.instagram import PageRefError, parse_page_ref
from shared.records import RecordError, SourceRecord

log = logging.getLogger(__name__)
_INTENTS = {"interest", "price_question", "contact_request"}
_COMMENT_ID = re.compile(r"^ig:([A-Za-z0-9_-]{1,80}):c([A-Za-z0-9_-]{1,80})$")


class Command(BaseCommand):
    help = "Import Instagram seller comments into business opportunities."

    def add_arguments(self, parser):
        parser.add_argument("--path", default="data/ig_collected/seller_leads.jsonl")

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.is_absolute():
            path = settings.BASE_DIR / path
        counts = {"read": 0, "kept": 0, "created": 0, "duplicate": 0, "skipped": {}}
        duplicates_seen = set()
        seen_records = set()
        if path.exists():
            with path.open(encoding="utf-8") as source:
                for line_number, line in enumerate(source, 1):
                    if not line.strip():
                        continue
                    counts["read"] += 1
                    try:
                        raw = json.loads(line)
                        record = SourceRecord.from_dict(raw)
                    except (json.JSONDecodeError, RecordError) as exc:
                        reason = getattr(exc, "reason", "invalid_json")
                        counts["skipped"][reason] = counts["skipped"].get(reason, 0) + 1
                        log.warning("Skipping Instagram line %s (%s)", line_number, reason)
                        continue
                    if record.source != "instagram" or record.kind != "comment":
                        self._skip(counts, "not_comment", line_number)
                        continue
                    if record.id in seen_records:
                        counts["duplicate"] += 1
                        continue
                    seen_records.add(record.id)
                    lead, reason = _lead_details(record)
                    if reason:
                        self._skip(counts, reason, line_number)
                        continue
                    label, seed, author, caption, post_url, cue = lead
                    if label not in _INTENTS:
                        self._skip(counts, "intent_not_lead", line_number)
                        continue
                    try:
                        handle = parse_page_ref(seed.removeprefix("account:"))
                    except PageRefError:
                        self._skip(counts, "invalid_seed", line_number)
                        continue
                    pages = list(InstagramPage.objects.filter(handle__iexact=handle, is_active=True).select_related("business", "default_product"))
                    if not pages:
                        self._skip(counts, "page_not_registered", line_number)
                        continue
                    counts["kept"] += 1
                    for page in pages:
                        if Opportunity.objects.filter(
                            business=page.business, source_platform="instagram", source_message_id=record.id,
                        ).exists():
                            counts["duplicate"] += 1
                            continue
                        if author.casefold() == page.handle.casefold():
                            self._skip(counts, "page_owner_comment", line_number)
                            continue
                        external_id = record.author_id or author
                        product, match_method = self._product(page, caption)
                        duplicate_key = (page.business_id, record.id)
                        if duplicate_key in duplicates_seen:
                            counts["duplicate"] += 1
                            continue
                        duplicates_seen.add(duplicate_key)
                        try:
                            with transaction.atomic():
                                customer, _ = Customer.objects.get_or_create(
                                    business=page.business, source_platform="instagram", external_user_id=external_id,
                                )
                                customer.source_username = author
                                customer.source_profile_url = f"https://www.instagram.com/{author}/" if author else ""
                                customer.save(update_fields=["source_username", "source_profile_url", "updated_at"])
                                opportunity, created = Opportunity.objects.get_or_create(
                                    business=page.business, source_platform="instagram",
                                    source_message_id=record.id,
                                    defaults={
                                        "customer": customer, "source_raw_message": record.text,
                                        "source_posted_at": record.created_at, "source_query": page.handle,
                                        "trace_metadata": {"instagram": {
                                            "post_url": post_url,
                                            "post_caption": caption, "intent_label": label,
                                            "cue": cue, "page_kind": page.kind,
                                            "product_match": match_method,
                                        }},
                                    },
                                )
                                if created and product:
                                    OpportunityProductMatch.objects.create(
                                        opportunity=opportunity, product=product, match_score=1.0 if match_method == "caption_tokens" else 0.0,
                                        recommendation_reason=f"Instagram product match: {match_method}",
                                    )
                        except IntegrityError:
                            counts["duplicate"] += 1
                        else:
                            counts["created" if created else "duplicate"] += 1
        summary = json.dumps(counts, ensure_ascii=False, separators=(",", ":"))
        self.stdout.write(summary)

    @staticmethod
    def _skip(counts, reason, line_number):
        counts["skipped"][reason] = counts["skipped"].get(reason, 0) + 1
        log.warning("Skipping Instagram line %s (%s)", line_number, reason)

    @staticmethod
    def _product(page, caption):
        products = Product.objects.filter(business=page.business, status="ACTIVE", is_discovery_active=True).order_by("name")
        caption_tokens = set(tokens(caption))
        best, best_overlap = None, 0
        for product in products:
            overlap = len(caption_tokens & set(tokens(product.name)))
            if overlap > best_overlap:
                best, best_overlap = product, overlap
        if best is not None:
            return best, "caption_tokens"
        if page.default_product_id:
            return page.default_product, "default_product"
        return None, "none"


def _lead_details(record):
    if len(record.id) > 150 or len(record.author_id or "") > 150:
        return None, "field_too_long"
    metadata = record.meta
    intent = metadata.get("intent")
    if not isinstance(intent, dict):
        return None, "invalid_intent"
    label = intent.get("label")
    if not isinstance(label, str):
        return None, "invalid_intent"
    seed = metadata.get("seed")
    if not isinstance(seed, str):
        return None, "invalid_seed"
    parent = metadata.get("parent_post")
    if not isinstance(parent, dict):
        return None, "invalid_parent_post"
    comment_match = _COMMENT_ID.fullmatch(record.id)
    parent_match = re.fullmatch(r"ig:([A-Za-z0-9_-]{1,80})", str(parent.get("id", "")))
    if not comment_match or not parent_match or comment_match.group(1) != parent_match.group(1):
        return None, "invalid_parent_post"
    caption = parent.get("caption", "")
    cue = intent.get("cue")
    if not isinstance(caption, str) or (cue is not None and not isinstance(cue, str)):
        return None, "invalid_parent_post"
    try:
        author = parse_page_ref(record.author_handle or "")
    except PageRefError:
        return None, "invalid_author"
    post_url = f"https://www.instagram.com/p/{comment_match.group(1)}/"
    return (label, seed, author.casefold(), caption[:160], post_url, cue), None

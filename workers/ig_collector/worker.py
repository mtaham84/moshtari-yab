from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from file_lock import exclusive_file_lock
from shared.collector_kit.jsonl import append_line, write_latest
from shared.collector_kit.limits import CircuitBreaker, Cooldown, DailyCap, retry_delay
from shared.collector_kit.seen import SeenStore
from shared.collector_kit.state_store import create_state_store
from shared.collector_kit.heartbeat import beat_error, beat_ok
from shared.collector_kit.status import write_status
from shared.records import RecordError, SourceRecord, to_json_line
from shared.instagram import PageRefError, parse_page_ref
from .policies import COMMENT_POLICIES, buyer_request
from instagram_discovery.analyzer import analyze_post, knowledge_from_seeds
from instagram_discovery.comment_evaluation import (
    JevCommentEvaluator, InstagramKnowledgeReader, evaluate_with_jev, prefilter_comment,
)
from .post_selection import CommentCandidate, select_comment_candidates, seller_post_priority

from .backends.base import (
    AuthFailed, Fatal, InstagramBackend, InstagramComment, InstagramPost,
    RateLimited, Transient, UnsupportedCapability,
)
from .lexicon import PersianIntentLexicon, normalize_fa

log = logging.getLogger("ig_collector")
RATE_LIMIT_COOLDOWN_SECONDS = 15 * 60
_HANDLE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_SHORTCODE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


def normalize_timestamp(value: Any, *, warn: bool = True) -> str | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.replace(".", "", 1).isdigit()):
            epoch = float(value)
            if epoch > 10_000_000_000:
                epoch /= 1000
            parsed = datetime.fromtimestamp(epoch, tz=timezone.utc)
        elif isinstance(value, datetime):
            parsed = value
        else:
            raw = str(value).strip()
            try:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                parsed = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    except (OverflowError, OSError, TypeError, ValueError):
        if warn:
            log.warning("Unparseable Instagram timestamp; writing null")
        return None


def is_persian_caption(text: str) -> bool:
    """Accept Persian script only; English and Finglish letters are rejected."""
    normalized = normalize_fa(text)
    letters = [ch for ch in normalized if ch.isalpha()]
    if not letters:
        return False
    persian = sum("\u0600" <= ch <= "\u06ff" or "\ufb50" <= ch <= "\ufdff" or "\ufe70" <= ch <= "\ufeff" for ch in letters)
    return persian > 0 and persian == len(letters)


def _safe_handle(value: Any) -> str | None:
    try:
        return parse_page_ref(str(value or ""))
    except PageRefError:
        return None


def normalize_post(post: InstagramPost, seed: str, backend: str, intent: PersianIntentLexicon) -> dict[str, Any] | None:
    shortcode = str(post.shortcode or "").strip()
    handle = _safe_handle(post.username)
    text = str(post.caption or "").strip()
    if not _SHORTCODE.fullmatch(shortcode) or not handle or not text:
        log.warning("Skipping post missing a valid shortcode, author handle, or caption")
        return None
    numeric_opt_in = bool(re.fullmatch(r"[0-9۰-۹٠-٩]+", text))
    if not is_persian_caption(text) and not numeric_opt_in:
        return None
    classification = intent.classify(text)
    if numeric_opt_in:
        classification = type(classification)(score=0, labels=["other"], matched=classification.matched)
    hashtags = [str(tag).strip().lstrip("#") for tag in post.hashtags]
    return SourceRecord.from_dict({
        "id": f"ig:{shortcode}", "source": "instagram", "kind": "post", "text": text,
        "author_handle": f"@{handle}", "author_name": str(post.full_name or "") or None,
        "author_id": str(post.author_id) if post.author_id not in (None, "") else None,
        "created_at": normalize_timestamp(post.created_at), "url": f"https://www.instagram.com/p/{shortcode}/",
        "metadata": {
            "backend": backend, "seed": seed, "post_id": str(post.post_id or shortcode),
            "hashtags": hashtags,
            "like_count": max(0, _int(post.like_count)), "comment_count": max(0, _int(post.comment_count)),
            "media_type": str(post.media_type or ""),
            "intent": {"score": classification.score, "labels": classification.labels},
        },
    }).as_dict()


def _collection_source(seed: dict[str, Any]) -> dict[str, Any]:
    if seed.get("kind") == "hashtag":
        return {
            "kind": "hashtag", "hashtag": seed["value"],
            "business_id": seed.get("business_id"),
            "category_id": seed.get("category_id"),
            "category": seed.get("category"),
        }
    return {"kind": "account", "username": seed["value"]}


def _suggested_target(seed: dict[str, Any]) -> dict[str, Any] | None:
    if seed.get("kind") != "hashtag" or not seed.get("category_id"):
        return None
    return {
        "business_id": seed.get("business_id"),
        "category_id": seed["category_id"], "category": seed.get("category"),
        "category_path": seed.get("category_path"),
        "product_ids": seed.get("product_ids", []), "products": seed.get("products", []),
    }


def _within_window(value: Any, since: datetime) -> bool:
    timestamp = normalize_timestamp(value, warn=False)
    return timestamp is not None and datetime.fromisoformat(timestamp) > since


def normalize_comment(comment: InstagramComment, post: InstagramPost, seed: str, backend: str,
                      intent: PersianIntentLexicon) -> dict[str, Any] | None:
    comment_id = str(comment.comment_id or "").strip()
    handle = _safe_handle(comment.username)
    text = str(comment.text or "").strip()
    if (not _SHORTCODE.fullmatch(str(post.shortcode or "")) or not _SHORTCODE.fullmatch(comment_id)
            or not handle or not text):
        log.warning("Skipping comment missing a stable id, valid author handle, or text")
        return None
    prefilter = prefilter_comment(
        text, commenter_handle=comment.username, post_author_handle=post.username,
    )
    numeric_opt_in = bool(re.fullmatch(r"[0-9۰-۹٠-٩]+", text))
    if not is_persian_caption(text) and not numeric_opt_in and prefilter.status != "set_aside":
        return None
    classification = intent.classify(text)
    if numeric_opt_in:
        classification = type(classification)(score=0, labels=["other"], matched=classification.matched)
    return SourceRecord.from_dict({
        "id": f"ig:{post.shortcode}:c{comment_id}", "source": "instagram", "kind": "comment", "text": text,
        "author_handle": f"@{handle}", "author_name": str(comment.full_name or "") or None,
        "author_id": str(comment.author_id) if comment.author_id not in (None, "") else None,
        "created_at": normalize_timestamp(comment.created_at),
        "url": f"https://www.instagram.com/p/{post.shortcode}/",
        "metadata": {
            "backend": backend, "seed": seed, "post_id": str(post.post_id or post.shortcode),
            "hashtags": [], "like_count": 0, "comment_count": 0, "media_type": "comment",
            "intent": {"score": classification.score, "labels": classification.labels},
            **({"parent_post": post.raw["parent_post"]} if isinstance(post.raw.get("parent_post"), dict) else {}),
        },
    }).as_dict()


def validate_record(record: dict[str, Any]) -> bool:
    try:
        SourceRecord.from_dict(record)
    except RecordError:
        return False
    return (record["source"] == "instagram" and record["kind"] in {"post", "comment"}
            and record["id"].startswith("ig:") and bool(_safe_handle(record["author_handle"]))
            and isinstance(record.get("metadata", {}).get("intent"), dict))


def validate_seeds(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ValueError("seed file root must be a JSON object")
    output: list[dict[str, Any]] = []
    for group in ("accounts", "hashtags"):
        rows = payload.get(group, [])
        if not isinstance(rows, list):
            log.warning("Skipping invalid %s seed list", group)
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                log.warning("Skipping invalid %s seed at index %d", group, index)
                continue
            value = str(row.get("username" if group == "accounts" else "tag") or "").strip().lstrip("#@")
            limit = _int(row.get("limit", 12 if group == "accounts" else 20))
            valid_value = bool(_HANDLE.fullmatch(value)) if group == "accounts" else bool(value) and not any(ch.isspace() or ch == "/" for ch in value)
            if not valid_value or limit < 1:
                log.warning("Skipping invalid %s seed at index %d", group, index)
                continue
            seed = {"kind": "account" if group == "accounts" else "hashtag", "value": value, "limit": min(limit, 500)}
            if group == "hashtags":
                try:
                    window_days = int(row.get("window_days", 7))
                except (TypeError, ValueError):
                    log.warning("Skipping hashtag seed with invalid time window at index %d", index)
                    continue
                if not 1 <= window_days <= 90:
                    log.warning("Skipping hashtag seed with invalid time window at index %d", index)
                    continue
                if "window_days" in row:
                    seed["window_days"] = window_days
                if "business_id" in row or "category_id" in row:
                    try:
                        business_id = int(row["business_id"])
                        category_id = int(row["category_id"])
                    except (KeyError, TypeError, ValueError):
                        log.warning("Skipping hashtag seed without category ownership at index %d", index)
                        continue
                    if business_id < 1 or category_id < 1:
                        log.warning("Skipping hashtag seed without category ownership at index %d", index)
                        continue
                    product_ids = row.get("product_ids", [])
                    terms = row.get("terms", [])
                    products = row.get("products", [])
                    if not isinstance(product_ids, list) or not isinstance(terms, list) or not isinstance(products, list):
                        log.warning("Skipping hashtag seed with invalid category metadata at index %d", index)
                        continue
                    seed.update({
                        "window_days": window_days,
                        "business_id": business_id,
                        "category_id": category_id,
                        "category": str(row.get("category") or ""),
                        "category_path": str(row.get("category_path") or ""),
                        "product_ids": [_int(item) for item in product_ids if _int(item) > 0],
                        "products": [str(item) for item in products],
                        "terms": [str(item) for item in terms],
                    })
            output.append(seed)
    comments = payload.get("comments", {})
    if not isinstance(comments, dict):
        comments = {}
    policy = comments.get("policy", "buyer_request")
    if policy not in COMMENT_POLICIES:
        log.warning("Invalid comments.policy; using buyer_request")
        policy = "buyer_request"
    mode = comments.get("mode", "off")
    if mode not in {"off", "signals", "all"}:
        log.warning("Invalid comments.mode; using off")
        mode = "off"
    if mode == "signals" and policy == "seller_post":
        log.warning("comments.mode=signals does not apply to seller_post; using all")
        mode = "all"
    config = {"mode": mode, "policy": policy,
              "max_posts_per_run": max(0, min(100, _int(comments.get("max_posts_per_run", 0)))),
              "max_per_post": max(0, min(500, _int(comments.get("max_per_post", 0)))),
              "read_hashtag_posts": comments.get("read_hashtag_posts", True) is True,
              "window_days": max(1, min(90, _int(comments.get("window_days", 30))))}
    return output, config


class InstagramCollector:
    def __init__(self, backend: InstagramBackend, output_dir: str | Path | None = None,
                 state_path: str | Path | None = None, *, daily_cap: int | None = None,
                 min_intent_score: int | None = None, sleep_min: float | None = None,
                 sleep_max: float | None = None, max_retries: int | None = None,
                 circuit_breaker: int | None = None, cooldown_seconds: int = RATE_LIMIT_COOLDOWN_SECONDS,
                 sleeper: Callable[[float], None] = time.sleep, jitter: Callable[[float, float], float] = random.uniform,
                 lexicon: PersianIntentLexicon | None = None,
                 knowledge_reader: InstagramKnowledgeReader | None = None,
                 jev: JevCommentEvaluator | None = None):
        root = Path(__file__).resolve().parents[2]
        self.backend = backend
        self.output_dir = _resolve(root, output_dir or os.getenv("IG_COLLECT_OUTPUT_DIR", "data/ig_collected"))
        self.state_path = _resolve(root, state_path or os.getenv("IG_COLLECT_STATE_FILE", "output/ig_collector_state.sqlite3"))
        self.state_backend = os.getenv("COLLECTOR_STATE_BACKEND", "sqlite").strip().lower()
        self.daily_cap = max(1, daily_cap if daily_cap is not None else int(os.getenv("IG_COLLECT_DAILY_CAP", "100")))
        self.min_intent_score = max(0, min(3, min_intent_score if min_intent_score is not None else int(os.getenv("IG_MIN_INTENT_SCORE", "1"))))
        self.sleep_min = sleep_min if sleep_min is not None else float(os.getenv("IG_COLLECT_SLEEP_MIN", "12"))
        self.sleep_max = sleep_max if sleep_max is not None else float(os.getenv("IG_COLLECT_SLEEP_MAX", "25"))
        self.max_retries = max(0, max_retries if max_retries is not None else int(os.getenv("IG_COLLECT_MAX_RETRIES", "2")))
        self.circuit_breaker = max(1, circuit_breaker if circuit_breaker is not None else int(os.getenv("IG_COLLECT_CIRCUIT_BREAKER", "3")))
        self.cooldown_seconds = max(1, cooldown_seconds)
        self.sleeper, self.jitter = sleeper, jitter
        lexicon_path = Path(__file__).resolve().parents[2] / "data" / "ig_lexicon_fa.json"
        self.lexicon = lexicon or PersianIntentLexicon.load(lexicon_path)
        self.knowledge_reader = knowledge_reader
        self.jev = jev
        self.store: Any = None

    @staticmethod
    def load_seeds(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"could not load Instagram seed file: {path}") from exc
        return validate_seeds(payload)

    def run(self, seeds: list[dict[str, Any]], comments: dict[str, Any] | None = None, *,
            dry_run: bool = False, keep_rejected: bool = False) -> dict[str, Any]:
        comments = comments or {"mode": "off", "max_posts_per_run": 0, "max_per_post": 0}
        if dry_run:
            return {"status": "DRY_RUN", "backend": self.backend.name, "seeds": len(seeds),
                    "comments_mode": comments.get("mode", "off"), "output": str(self.output_dir)}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.state_backend == "sqlite":
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.store = create_state_store(self.state_path, platform="instagram", sqlite_factory=SeenStore)
        lock = (self.state_path.with_suffix(self.state_path.suffix + ".lock")
                if self.state_backend == "sqlite"
                else self.output_dir / ".collector_state.lock")
        try:
            with exclusive_file_lock(lock):
                result = self._run_locked(seeds, comments, keep_rejected)
            if result.get("status") in {"COMPLETED", "DAILY_CAP_REACHED", "PARTIAL", "CIRCUIT_OPEN"} and not result.get("failures"):
                beat_ok("instagram", int(result.get("collected", 0)))
            else:
                beat_error("instagram", f"cycle status {result.get('status', 'unknown')}",
                           result.get("cooldown_until"))
            return result
        except Exception as exc:
            beat_error("instagram", type(exc).__name__)
            raise

    def _run_locked(self, seeds: list[dict[str, Any]], comments: dict[str, Any], keep_rejected: bool) -> dict[str, Any]:
        assert self.store is not None
        now = datetime.now(timezone.utc)
        cooldown = Cooldown(self.store, self.cooldown_seconds).active_until(now)
        if cooldown:
            result = self._result("RATE_LIMITED", [], 0, 0, cooldown_until=cooldown,
                                  started_at=now.isoformat(), updated_at=now.isoformat(),
                                  handles={str(seed.get("value", "")): {
                                      "status": "rate_limited", "posts": 0, "comments": 0
                                  } for seed in seeds if seed.get("kind") == "account"})
            self._write_status(result)
            return result
        collected: list[dict[str, Any]] = []
        review_queue: list[dict[str, Any]] = []
        comment_evaluations: list[dict[str, Any]] = []
        post_reviews: dict[str, dict[str, Any]] = {}
        rejected: list[dict[str, Any]] = []
        filtered = failures = 0
        breaker = CircuitBreaker(self.circuit_breaker)
        status = "COMPLETED"
        comment_candidates: list[CommentCandidate] = []
        comment_candidate_ids: set[str] = set()
        pending_comment_fetches: set[str] = set()
        pending_seed_progress: dict[str, str | None] = {}
        seller_leads: list[dict[str, Any]] = []
        seller_lead_ids: set[str] = set()
        pending_seller_lead_ids: set[str] = set()
        seller_lead_count = 0
        policy_name = comments.get("policy") or "buyer_request"
        if policy_name not in COMMENT_POLICIES:
            policy_name = "buyer_request"

        handle_outcomes: dict[str, dict[str, Any]] = {}
        processed_handles: set[str] = set()
        stopped = False
        for seed_index, seed in enumerate(seeds):
            if DailyCap(self.daily_cap, self.store.count_today() + len(collected)
                        + len(pending_seller_lead_ids)).reached():
                status = "DAILY_CAP_REACHED"
                break
            kind, value = seed.get("kind"), str(seed.get("value", ""))
            limit = max(0, min(500, _int(seed.get("limit", 0))))
            if not limit:
                continue
            if kind == "hashtag" and not comments.get("read_hashtag_posts", True):
                continue
            seed_key = f"{kind}:{value}"
            if kind == "account" and value in processed_handles:
                continue
            if kind == "account":
                processed_handles.add(value)
            outcome = {"status": "ok", "posts": 0, "comments": 0}
            cooldown_until = self.store.get_seed_progress(f"skip_until:{seed_key}") if kind == "account" else None
            private_until = self.store.get_seed_progress(f"private:{seed_key}") if kind == "account" else None
            now = datetime.now(timezone.utc)
            if private_until and private_until > now:
                outcome.update(status="private", reason="private_cooldown")
                handle_outcomes[value] = outcome
                continue
            if cooldown_until and cooldown_until > now:
                outcome.update(status="error", reason="not_found_cooldown")
                handle_outcomes[value] = outcome
                continue
            capability = "user_posts" if kind == "account" else "hashtag_posts"
            try:
                self.backend.require(capability)
                progress_key = f"seller_post:{seed_key}" if policy_name == "seller_post" else seed_key
                since = self.store.get_seed_progress(progress_key) if kind == "account" else None
                if kind == "hashtag":
                    window_days = int(seed.get("window_days", 7))
                    since = datetime.now(timezone.utc) - timedelta(days=window_days)
                    posts = self._with_retry(lambda: self.backend.hashtag_posts(value, limit, since))
                    posts = [post for post in posts if _within_window(post.created_at, since)][:limit]
                else:
                    posts = self._with_retry(lambda: self.backend.user_posts(value, limit, since))[:limit]
                outcome["posts"] = len(posts)
                if kind == "account" and policy_name == "seller_post":
                    posts = [post for post in posts if post.shortcode not in comment_candidate_ids
                             and not self.store.comments_were_fetched(f"seller_post:{post.shortcode}")]
                breaker.success()
                latest: str | None = None
                reached_cap = False
                for post in posts:
                    if DailyCap(self.daily_cap, self.store.count_today() + len(collected)
                                + len(pending_seller_lead_ids)).reached():
                        status = "DAILY_CAP_REACHED"
                        reached_cap = True
                        break
                    timestamp = normalize_timestamp(post.created_at, warn=False)
                    if timestamp and (latest is None or timestamp > latest):
                        latest = timestamp
                    record = normalize_post(post, seed_key, self.backend.name, self.lexicon)
                    if record is None:
                        filtered += 1
                        if keep_rejected:
                            rejected.append(_rejected("post", "invalid_or_non_persian_or_missing_fields"))
                        continue
                    if kind == "hashtag":
                        record["metadata"]["collection_sources"] = [_collection_source(seed)]
                        suggested = _suggested_target(seed)
                        if suggested:
                            record["metadata"]["suggested_target"] = suggested
                        source = {
                            "kind": "hashtag", "value": value,
                            "business_id": seed.get("business_id"),
                            "category_id": seed.get("category_id"),
                        }
                        category_knowledge = knowledge_from_seeds(seeds, seed.get("business_id"))
                        target_category = next((category for category in category_knowledge.categories_for(source)
                                                if category.get("id") == seed.get("category_id")), None)
                        analysis = analyze_post({
                            "id": record["id"], "shortcode": post.shortcode,
                            "caption": record["text"], "username": post.username,
                            "url": record["url"], "hashtags": record["metadata"]["hashtags"],
                        }, source, category_knowledge)
                        decision = analysis["category_decision"]
                        record["metadata"]["comment_priority"] = seller_post_priority(
                            record["text"], analysis=analysis, category_decision=decision,
                        )
                        if decision["result"] == "rejected":
                            rejected.append(_rejected("post", decision["reason"]["code"]))
                            if keep_rejected:
                                rejected[-1]["source_hashtag"] = value
                        if seed.get("category_id"):
                            decision["source_category_id"] = seed["category_id"]
                            decision["source_category"] = seed.get("category")
                            decision["source_hashtag"] = value
                        post_reviews[post.shortcode] = {
                            "id": record["id"], "post_url": record["url"],
                            "caption": record["text"][:160],
                            "source_hashtag": value,
                            "source_category_id": seed.get("category_id"),
                            "source_category": seed.get("category"),
                            "category_decision": decision,
                            "category": target_category.get("name") if target_category else None,
                            "category_path": target_category.get("full_path") if target_category else None,
                        }
                        record["metadata"]["parent_post"] = post_reviews[post.shortcode]
                        record["metadata"]["category_decision"] = decision
                        record["metadata"]["decision_status"] = decision["result"]
                        record["metadata"]["suggested_category"] = (
                            decision["category"] or decision["category_candidate"]
                        )
                        record["metadata"]["suggested_product"] = (
                            decision["product"] or decision["product_candidate"]
                        )
                        record["metadata"]["match_score"] = decision["score"]
                        record["metadata"]["match_reason"] = decision["reason"]
                        record["metadata"]["review_queue_status"] = (
                            "queued" if decision["result"] == "human_review" else "not_queued"
                        )
                        if decision["result"] == "human_review":
                            record["metadata"]["human_review_required"] = True
                            review_queue.append(record)
                    score = int(record["metadata"]["intent"]["score"])
                    if kind == "hashtag":
                        self._collect_record(record, collected)
                        if (record["metadata"]["category_decision"]["result"] == "confident_match"
                                and policy_name == "seller_post"):
                            comment_fetch_key = post.shortcode
                            if (post.shortcode not in comment_candidate_ids
                                    and not self.store.comments_were_fetched(comment_fetch_key)):
                                comment_candidates.append((post, seed_key, record))
                                comment_candidate_ids.add(post.shortcode)
                        continue
                    if (policy_name == "seller_post"
                            and "advertisement" not in record["metadata"]["intent"]["labels"]):
                        filtered += 1
                        if keep_rejected:
                            rejected.append(_rejected("post", "not_seller_post"))
                        continue
                    if policy_name == "seller_post":
                        record["metadata"]["comment_priority"] = seller_post_priority(record["text"])
                        self._collect_record(record, collected)
                    comment_fetch_key = (f"seller_post:{post.shortcode}" if policy_name == "seller_post"
                                         else post.shortcode)
                    if (post.shortcode not in comment_candidate_ids
                            and (policy_name == "seller_post"
                                 or not self.store.comments_were_fetched(comment_fetch_key))):
                        comment_candidates.append((post, seed_key, record))
                        comment_candidate_ids.add(post.shortcode)
                    if policy_name == "buyer_request" and score < self.min_intent_score:
                        filtered += 1
                        if keep_rejected:
                            rejected.append(_rejected("post", "intent_below_threshold"))
                        continue
                    if policy_name == "buyer_request":
                        self._collect_record(record, collected)
                if not reached_cap:
                    pending_seed_progress[progress_key] = latest
            except UnsupportedCapability as exc:
                outcome.update(status="error", reason="unsupported")
                log.warning("Skipping %s: %s", seed_key, exc)
            except RateLimited as exc:
                cooldown_until = Cooldown(self.store, self.cooldown_seconds).start()
                outcome.update(status=self.backend.outcome_for_error(exc), reason="rate_limited",
                               cooldown_until=cooldown_until)
                status = "RATE_LIMITED"
                for later in seeds[seed_index + 1:]:
                    handle_outcomes[str(later.get("value", ""))] = {"status": "rate_limited", "posts": 0, "comments": 0}
                handle_outcomes[value] = outcome
                stopped = True
                break
            except AuthFailed as exc:
                outcome.update(status="error", reason="auth_failed")
                log.error("Instagram backend authentication failed: %s", exc)
                failures += 1
                status = "PARTIAL"
            except Transient as exc:
                outcome.update(status=self.backend.outcome_for_error(exc), reason=type(exc).__name__)
                failures += 1
                circuit_open = breaker.failure()
                log.warning("Temporary Instagram backend failure for %s (%s)", seed_key, type(exc).__name__)
                if circuit_open:
                    status = "CIRCUIT_OPEN"
                else:
                    status = "PARTIAL"
            except Fatal as exc:
                outcome["status"] = self.backend.outcome_for_error(exc)
                outcome["reason"] = outcome["status"]
                if outcome["status"] == "private":
                    private_until = datetime.now(timezone.utc) + timedelta(
                        seconds=self.backend.request_cooldown_seconds() or 86400
                    )
                    self.store.set_seed_progress(f"private:{seed_key}", private_until.isoformat())
                elif outcome["status"] == "not_found":
                    outcome["auto_paused"] = False
                failures += 1
                log.error("Instagram backend rejected %s (%s)", seed_key, type(exc).__name__)
                status = "PARTIAL"
            except Exception as exc:
                outcome.update(status="error", reason=type(exc).__name__)
                failures += 1
                log.error("Unexpected Instagram collection failure for %s (%s)", seed_key, type(exc).__name__)
                status = "PARTIAL"
            handle_outcomes[value] = outcome
            if seed_index < len(seeds) - 1:
                self.sleeper(max(0.0, self.jitter(self.sleep_min, self.sleep_max)))

        if status == "COMPLETED" and failures:
            status = "PARTIAL"
        mode = comments.get("mode", "off")
        max_posts = max(0, min(100, _int(comments.get("max_posts_per_run", 0))))
        max_comments = max(0, min(500, _int(comments.get("max_per_post", 0))))
        comment_window_days = max(1, min(90, _int(comments.get("window_days", 30))))
        comment_window_start = datetime.now(timezone.utc) - timedelta(days=comment_window_days)
        comment_candidates = select_comment_candidates(
            comment_candidates, since=comment_window_start,
        )
        if status in {"COMPLETED", "PARTIAL"} and mode != "off" and max_posts and max_comments:
            fetched_posts = 0
            for post, seed_key, post_record in comment_candidates:
                pending = len(pending_seller_lead_ids)
                if fetched_posts >= max_posts or DailyCap(
                        self.daily_cap, self.store.count_today() + len(collected) + pending
                ).reached():
                    break
                if (policy_name == "buyer_request" and mode == "signals"
                        and post_record["metadata"]["intent"]["score"] < self.min_intent_score):
                    continue
                try:
                    self.backend.require("post_comments")
                    comments_rows = self._with_retry(lambda: self.backend.post_comments(post, max_comments))[:max_comments]
                    parent_post = {
                        "id": f"ig:{post.shortcode}", "caption": post.caption[:160],
                        "url": post.url,
                        **(post_reviews.get(post.shortcode) or {}),
                    }
                    outcome = handle_outcomes.get(seed_key.split(":", 1)[-1])
                    if outcome:
                        outcome["comments"] += len(comments_rows)
                    comment_fetch_key = (f"seller_post:{post.shortcode}" if policy_name == "seller_post"
                                         else post.shortcode)
                    pending_comment_fetches.add(comment_fetch_key)
                    fetched_posts += 1
                    for comment in comments_rows:
                        if DailyCap(
                                self.daily_cap, self.store.count_today() + len(collected)
                                + len(pending_seller_lead_ids)
                        ).reached():
                            status = "DAILY_CAP_REACHED"
                            break
                        record = normalize_comment(comment, post, seed_key, self.backend.name, self.lexicon)
                        if record is None:
                            filtered += 1
                            if keep_rejected:
                                rejected.append(_rejected("comment", "invalid_or_non_persian_or_missing_fields"))
                            continue
                        if parent_post.get("category_decision"):
                            record["metadata"]["parent_post"] = parent_post
                            record["metadata"]["category_decision"] = parent_post["category_decision"]
                        else:
                            record["metadata"]["parent_post"] = parent_post
                        prefilter = prefilter_comment(
                            record["text"],
                            commenter_handle=record.get("author_handle"),
                            post_author_handle=post_record.get("author_handle"),
                        )
                        prefilter_data = {
                            "status": prefilter.status,
                            "reason": prefilter.reason,
                            "evidence": list(prefilter.evidence),
                        }
                        record["metadata"]["jev_prefilter"] = prefilter_data
                        if prefilter.status == "set_aside":
                            comment_evaluations.append({
                                "id": record["id"], "prefilter": prefilter_data, "jev": None,
                            })
                            filtered += 1
                            continue

                        category_context = record["metadata"].get("parent_post", {})
                        decision = evaluate_with_jev(
                            record["text"], post_context=category_context,
                            knowledge_reader=self.knowledge_reader, jev=self.jev,
                        )
                        record["metadata"]["jev_evaluation"] = decision
                        comment_evaluations.append({
                            "id": record["id"], "prefilter": prefilter_data, "jev": decision,
                        })
                        if decision["status"] == "uncertain":
                            record["metadata"]["review_queue_status"] = "queued"
                            record["metadata"]["human_review_required"] = True
                            review_queue.append(record)
                        else:
                            record["metadata"]["review_queue_status"] = "not_queued"
                        # If Jev is configured, its result gates the legacy lead policy.
                        # With no Jev adapter configured, keep the existing account-seed
                        # flow operational while recording the unavailable evaluation
                        # as uncertain and queued for review.
                        if self.jev is not None and decision["status"] != "probably a buyer":
                            continue

                        policy = COMMENT_POLICIES.get(policy_name, buyer_request)
                        accepted = policy(record, post_record, self.min_intent_score)
                        if accepted is None:
                            filtered += 1
                            if keep_rejected:
                                reason = "not_seller_lead" if policy_name == "seller_post" else "intent_below_threshold"
                                rejected.append(_rejected("comment", reason))
                            continue
                        record = accepted
                        if policy_name == "seller_post":
                            if (record["id"] not in seller_lead_ids
                                    and not self.store.contains("seller_lead",
                                                               f"seller_lead:{record['id']}")):
                                seller_leads.append(record)
                                seller_lead_ids.add(record["id"])
                                pending_seller_lead_ids.add(record["id"])
                            continue
                        if not self._collect_record(record, collected):
                            continue
                except UnsupportedCapability as exc:
                    log.warning("Skipping comments for %s: %s", post.shortcode, exc)
                    continue
                except RateLimited as exc:
                    cooldown_until = Cooldown(self.store, self.cooldown_seconds).start()
                    status = "RATE_LIMITED"
                    outcome = handle_outcomes.get(seed_key.split(":", 1)[-1])
                    if outcome:
                        outcome.update(status=self.backend.outcome_for_error(exc), reason="rate_limited",
                                       cooldown_until=cooldown_until)
                    break
                except AuthFailed as exc:
                    log.error("Instagram backend authentication failed: %s", exc)
                    outcome = handle_outcomes.get(seed_key.split(":", 1)[-1])
                    if outcome:
                        outcome.update(status="error", reason="auth_failed")
                    failures += 1
                    status = "PARTIAL"
                    continue
                except Transient:
                    failures += 1
                    log.warning("Temporary Instagram comment read failure")
                    status = "CIRCUIT_OPEN" if failures >= self.circuit_breaker else "PARTIAL"
                    outcome = handle_outcomes.get(seed_key.split(":", 1)[-1])
                    if outcome:
                        outcome.update(status="error", reason="transient")
                    continue
                except Fatal:
                    failures += 1
                    log.warning("Instagram comment request was rejected")
                    outcome = handle_outcomes.get(seed_key.split(":", 1)[-1])
                    if outcome:
                        outcome.update(status="error", reason="fatal")
                    status = "PARTIAL"
                    continue
        if stopped or status == "RATE_LIMITED":
            for seed in seeds:
                value = str(seed.get("value", ""))
                if value not in handle_outcomes:
                    handle_outcomes[value] = {"status": "rate_limited", "posts": 0, "comments": 0}

        if DailyCap(self.daily_cap, self.store.count_today() + len(collected)
                    + len(pending_seller_lead_ids)).reached() and status in {"COMPLETED", "PARTIAL"}:
            status = "DAILY_CAP_REACHED"
        output = self.output_dir / f"{date.today().isoformat()}.jsonl"
        if collected:
            try:
                self._persist_records(collected, output)
            except Exception:
                log.exception("Could not persist Instagram JSONL/state; rolling back appended lines")
                status = "FAILED"
                collected.clear()
        if review_queue:
            try:
                self._persist_review_queue(review_queue)
            except Exception:
                log.exception("Could not persist Instagram post review queue")
                status = "FAILED"
        if comment_evaluations:
            try:
                self._persist_comment_evaluations(comment_evaluations)
            except Exception:
                log.exception("Could not persist Instagram comment evaluations")
                status = "FAILED"
        if seller_leads:
            try:
                seller_lead_count = self._persist_seller_leads(seller_leads)
            except Exception:
                log.exception("Could not persist Instagram seller leads; posts will remain eligible for retry")
                status = "FAILED"
        if status != "FAILED" and self.store.count_today() < self.daily_cap:
            for seed_key, latest in pending_seed_progress.items():
                self.store.set_seed_progress(seed_key, latest)
            for shortcode in pending_comment_fetches:
                self.store.mark_comments_fetched(shortcode)
        if DailyCap(self.daily_cap, self.store.count_today()).reached() and status in {"COMPLETED", "PARTIAL"}:
            status = "DAILY_CAP_REACHED"
        if failures and status == "COMPLETED":
            status = "PARTIAL"
        if rejected and keep_rejected:
            self._write_rejected(rejected, self.output_dir / f"{date.today().isoformat()}.rejected.jsonl")
        result = self._result(status, collected, filtered, failures,
                              output=str(output), rejected=len(rejected),
                              human_review=len(review_queue),
                              started_at=now.isoformat(),
                              updated_at=datetime.now(timezone.utc).isoformat(),
                              handles=handle_outcomes,
                              cooldown_until=self.store.get_setting("rate_limit_until"),
                              seller_leads=seller_lead_count)
        self._write_status(result)
        return result

    def _with_retry(self, operation):
        retry = 0
        while True:
            try:
                return operation()
            except Transient:
                if retry >= self.max_retries:
                    raise
                self.sleeper(retry_delay(retry, self.jitter))
                retry += 1

    def _collect_record(self, record: dict[str, Any], collected: list[dict[str, Any]]) -> bool:
        assert self.store is not None
        if not validate_record(record):
            log.warning("Skipping malformed Instagram record")
            return False
        store_id = f"seller_lead:{record['id']}" if record["kind"] == "seller_lead" else record["id"]
        existing = next((row for row in collected if row["id"] == record["id"]), None)
        if existing:
            seed_kind, _, seed_value = existing["metadata"].get("seed", "").partition(":")
            original_source = (_collection_source({"kind": seed_kind, "value": seed_value})
                               if seed_kind in {"account", "hashtag"} else None)
            sources = existing["metadata"].setdefault(
                "collection_sources", [original_source] if original_source else []
            )
            for source in record["metadata"].get("collection_sources", []):
                if source not in sources:
                    sources.append(source)
            suggested = record["metadata"].get("suggested_target")
            if suggested and not existing["metadata"].get("suggested_target"):
                existing["metadata"]["suggested_target"] = suggested
            return False
        if self.store.contains(record["kind"], store_id):
            return False
        collected.append(record)
        return True

    def _persist_records(self, rows: list[dict[str, Any]], dated: Path) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        latest = self.output_dir / "latest.jsonl"
        paths = sorted({latest.resolve(), dated.resolve()}, key=str)
        locks = [path.with_suffix(path.suffix + ".lock") for path in paths]
        with exclusive_file_lock(locks[0]):
            with exclusive_file_lock(locks[1]):
                offsets = {path: path.stat().st_size if path.exists() else 0 for path in paths}
                try:
                    content = "".join(to_json_line(SourceRecord.from_dict(row)) + "\n" for row in rows)
                    write_latest(latest, content, append=True, locked=True)
                    write_latest(dated, content, append=True, locked=True)
                    assert self.store is not None
                    self.store.add_many(rows)
                except Exception:
                    for path, offset in offsets.items():
                        if path.exists():
                            with path.open("r+b") as stream:
                                stream.truncate(offset)
                    raise

    def _persist_seller_leads(self, rows: list[dict[str, Any]]) -> int:
        target = self.output_dir / "seller_leads.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        assert self.store is not None
        with exclusive_file_lock(target.with_suffix(target.suffix + ".lock")):
            existing = set()
            if target.exists():
                for line in target.read_text(encoding="utf-8").splitlines():
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if item.get("id"):
                        existing.add(str(item["id"]))
            new_rows = [row for row in rows if row["id"] not in existing]
            content = "".join(
                to_json_line(SourceRecord.from_dict(row)) + "\n"
                for row in new_rows
            )
            if content:
                offset = write_latest(target, content, append=True, locked=True)
                try:
                    state_rows = [{**row, "id": f"seller_lead:{row['id']}", "kind": "seller_lead"}
                                  for row in new_rows]
                    self.store.add_many(state_rows)
                except Exception:
                    with target.open("r+b") as stream:
                        stream.truncate(offset)
                    raise
            return len(new_rows)

    def _persist_review_queue(self, rows: list[dict[str, Any]]) -> int:
        target = self.output_dir / "needs-review.jsonl"
        existing: set[str] = set()
        if target.exists():
            for line in target.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("id"):
                    existing.add(str(item["id"]))
        new_rows = [row for row in rows if row["id"] not in existing]
        if new_rows:
            content = "".join(to_json_line(SourceRecord.from_dict(row)) + "\n" for row in new_rows)
            write_latest(target, content, append=True)
        return len(new_rows)

    def _persist_comment_evaluations(self, rows: list[dict[str, Any]]) -> int:
        target = self.output_dir / "comment-evaluations.jsonl"
        existing: set[str] = set()
        if target.exists():
            for line in target.read_text(encoding="utf-8").splitlines():
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("id"):
                    existing.add(str(item["id"]))
        new_rows = [row for row in rows if row["id"] not in existing]
        if new_rows:
            content = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                              for row in new_rows)
            write_latest(target, content, append=True)
        return len(new_rows)

    def _write_rejected(self, rows: list[dict[str, Any]], target: Path) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        content = "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows)
        append_line(target, content)

    def _result(self, status: str, rows: list[dict[str, Any]], filtered: int, failures: int, **extra) -> dict[str, Any]:
        result = {"status": status, "backend": self.backend.name, "collected": len(rows),
                  "filtered_out": filtered, "failures": failures, "collected_today": self.store.count_today() if self.store else 0}
        result.update(extra)
        return result

    def _write_status(self, result: dict[str, Any]) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        target = self.output_dir / "status.json"
        payload = {**result, "daily_cap": self.daily_cap, "updated_at": datetime.now(timezone.utc).isoformat()}
        if payload.get("output"):
            payload["output"] = Path(payload["output"]).name
        write_status(target, payload)


def _rejected(kind: str, reason: str) -> dict[str, str]:
    # Do not persist message text, shortcode, handles, or author ids in rejected data.
    return {"kind": kind, "reason": reason}


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return (path if path.is_absolute() else root / path).resolve()


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

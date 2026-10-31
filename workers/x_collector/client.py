from datetime import datetime, timezone, timedelta
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time
from typing import Any

from . import cli_mapping as mapping

log = logging.getLogger("x_collector.client")

_PERSIAN_SPECIFIC_LETTERS = re.compile(r"[گچپژ]")
_PERSIAN_AFFIXES = re.compile(r"\b(می|نمی)[\u200c\s]+[آ-ی]+")
_PERSIAN_WORDS = re.compile(
    r"\b(این|آن|با|برای|به|در|از|که|را|رو|است|شد|بود|باشه|باشند|کرد|کرده|دارد|دارم|داری|داریم|میشه|نمیشه|چی|کجا|کدوم|کی|چه|چطور|خوب|خوبه|بخرم|بگیرم|میخوام|می‌خوام|هستم|هست|نیست|پیشنهاد|معرفی|قیمت|خرید|فروش|پیراهن|لباس|لپ\s*تاپ)\b"
)
_PERSIAN_UNICODE = re.compile(r"[\u0600-\u06FF]")


def validate_persian_post(text: str, lang_meta: str | None = None) -> tuple[bool, str]:
    """Multi-layer Persian language validation:
    1. Metadata layer: reject if explicitly marked as non-Persian language (e.g. en, ar, tr, ur).
    2. Script layer: require Persian/Arabic Unicode block.
    3. Linguistic layer: require Persian-specific letters (گ چ پ ژ), affixes (می/نمی), or common Persian words
       to prevent Arabic/Urdu posts from being falsely accepted.
    """
    if not text or not text.strip():
        return False, "EMPTY_TEXT"
    clean_text = text.strip()

    # Layer 1: Language metadata
    if lang_meta:
        norm_lang = str(lang_meta).strip().lower()
        if norm_lang not in {"fa", "pes", "per", "und", "unknown", ""}:
            return False, f"META_LANG_{norm_lang.upper()}"

    # Layer 2: Persian Unicode script presence
    if not _PERSIAN_UNICODE.search(clean_text):
        return False, "NO_PERSIAN_SCRIPT"

    # Layer 3: Linguistic check
    if _PERSIAN_SPECIFIC_LETTERS.search(clean_text) or _PERSIAN_AFFIXES.search(clean_text) or _PERSIAN_WORDS.search(clean_text):
        return True, "PERSIAN_VALIDATED"

    if lang_meta and str(lang_meta).strip().lower() in {"fa", "pes", "per"}:
        return True, "PERSIAN_META_CONFIRMED"

    return False, "NOT_PERSIAN_LINGUISTIC"


def parse_created_at_utc(value: Any) -> datetime | None:
    """Parse various Twitter date formats into UTC datetime."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, (int, float)) or (isinstance(value, str) and value.replace(".", "", 1).isdigit()):
            epoch = float(value)
            if epoch > 10_000_000_000:
                epoch /= 1000
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
        else:
            raw = str(value).strip()
            try:
                dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            except ValueError:
                dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


class XCliError(RuntimeError):
    def __init__(self, message: str, kind: str = "fatal"):
        super().__init__(message)
        self.kind = kind


class XCliClient:
    def __init__(self, command: str | None = None, timeout: int | None = None):
        self.command = command or mapping.CLI_COMMAND
        self.timeout = timeout or int(os.getenv("X_CLI_TIMEOUT", "45"))

    def search_top_past_week(
        self,
        query: str,
        target_posts: int = 40,
        account: Any | None = None,
        max_pages: int | None = None,
        global_seen_ids: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Executes topic-centric Top search with native Past Week restriction, multi-layer Persian filtering,
        adaptive pagination (up to 10 pages), early stopping on 2 consecutive empty pages, and comprehensive search stats.
        """
        now = datetime.now(timezone.utc)
        cutoff_utc = now - timedelta(days=7)
        cutoff_ts = int(cutoff_utc.timestamp())

        # Construct native request query ensuring language and date filters are applied
        clean_q = query.strip()
        ops = []
        if "lang:fa" not in clean_q.casefold():
            ops.append("lang:fa")
        if "since_time:" not in clean_q.casefold() and "since:" not in clean_q.casefold():
            ops.append(f"since_time:{cutoff_ts}")

        full_query = f"{clean_q} {' '.join(ops)}".strip()

        acc_id = getattr(account, "id", None)
        acc_user = getattr(account, "username", "unknown")
        proxy = getattr(account, "proxy_url", None)

        configured_max = int(os.getenv("X_PAGINATION_MAX_PAGES", "10"))
        max_p = max_pages or min(max(5, configured_max), 10)

        stats: dict[str, Any] = {
            "query": query,
            "search_mode": "top",
            "date_range": "past_week",
            "language": "fa",
            "target_posts": target_posts,
            "account_id": acc_id,
            "account_username": acc_user,
            "proxy_url": proxy,
            "pages_fetched": 0,
            "raw_posts_received": 0,
            "valid_posts": 0,
            "duplicate_posts": 0,
            "out_of_range_posts": 0,
            "non_persian_posts": 0,
            "stop_reason": "UNKNOWN",
        }

        valid_records: list[dict[str, Any]] = []
        seen_in_job: set[str] = set(global_seen_ids or [])
        consecutive_zero_pages = 0
        cursor: str | None = None

        log.info(
            "Starting X Top search for '%s' [Account: @%s, Proxy: %s, Cutoff: %s]",
            query, acc_user, proxy, cutoff_utc.isoformat(),
        )

        use_in_process = False
        try:
            import twitter_cli.client as client_mod
            from twitter_cli.client import TwitterClient, FEATURES, parse_timeline_response, _deep_get
            use_in_process = True
        except ImportError:
            use_in_process = False

        if use_in_process and account and getattr(account, "auth_token", None) and getattr(account, "ct0", None):
            try:
                os.environ["TWITTER_AUTH_TOKEN"] = str(account.auth_token)
                os.environ["TWITTER_CT0"] = str(account.ct0)
                if proxy:
                    os.environ["TWITTER_PROXY"] = str(proxy)
                client_mod._cffi_session = None
                cl = TwitterClient(auth_token=account.auth_token, ct0=account.ct0)

                for page_idx in range(1, max_p + 1):
                    stats["pages_fetched"] += 1
                    variables: dict[str, Any] = {
                        "rawQuery": full_query,
                        "count": 20,
                        "querySource": "typed_query",
                        "product": "Top",
                    }
                    if cursor:
                        variables["cursor"] = cursor

                    _sm = os.environ.get("TWITTER_SEARCH_METHOD", "auto").strip().lower()
                    if _sm == "post" or getattr(cl, "_search_use_post", False):
                        data = cl._graphql_post("SearchTimeline", variables, FEATURES)
                    else:
                        try:
                            data = cl._graphql_get("SearchTimeline", variables, FEATURES)
                        except Exception as get_exc:
                            if "404" in str(get_exc):
                                data = cl._graphql_post("SearchTimeline", variables, FEATURES)
                                cl._search_use_post = True
                            else:
                                raise

                    new_tweets, next_cursor = parse_timeline_response(
                        data,
                        lambda d: _deep_get(d, "data", "search_by_raw_query", "search_timeline", "timeline", "instructions"),
                    )

                    stats["raw_posts_received"] += len(new_tweets)
                    new_valid_this_page = 0

                    for tw in new_tweets:
                        tid = str(getattr(tw, "id", "")).strip()
                        if not tid:
                            continue
                        text = getattr(tw, "text", "") or ""
                        lang_meta = getattr(tw, "lang", None)

                        # Check 1: Deduplication
                        if tid in seen_in_job:
                            stats["duplicate_posts"] += 1
                            continue

                        # Check 2: Date within fixed 7-day cutoff
                        created_dt = parse_created_at_utc(getattr(tw, "created_at", None))
                        if not created_dt or created_dt < cutoff_utc:
                            stats["out_of_range_posts"] += 1
                            continue

                        # Check 3: Multi-layer Persian language
                        is_fa, _ = validate_persian_post(text, lang_meta)
                        if not is_fa:
                            stats["non_persian_posts"] += 1
                            continue

                        # Valid unique post
                        seen_in_job.add(tid)
                        new_valid_this_page += 1
                        author = getattr(tw, "author", None)
                        raw_dict = {
                            "id": tid,
                            "text": text,
                            "created_at": getattr(tw, "created_at", None),
                            "createdAtISO": getattr(tw, "created_at", None),
                            "author_handle": getattr(author, "username", None) if author else None,
                            "author_name": getattr(author, "name", None) if author else None,
                            "author_id": str(getattr(author, "id", "")) if author else None,
                            "author_verified": getattr(author, "verified", False) if author else False,
                            "lang": lang_meta or "fa",
                            "isRetweet": getattr(tw, "is_retweet", False),
                            "author": {
                                "screenName": getattr(author, "username", None) if author else None,
                                "name": getattr(author, "name", None) if author else None,
                                "id": getattr(author, "id", None) if author else None,
                                "verified": getattr(author, "verified", False) if author else False,
                                "description": getattr(author, "bio", "") if author else "",
                            } if author else {},
                        }
                        valid_records.append(raw_dict)
                        if len(valid_records) >= target_posts:
                            break

                    stats["valid_posts"] = len(valid_records)

                    # Early stopping condition 1: Target reached
                    if len(valid_records) >= target_posts:
                        stats["stop_reason"] = "TARGET_REACHED"
                        log.info("Query '%s' reached target %d valid posts", query, target_posts)
                        break

                    # Early stopping condition 4: Two consecutive pages produce zero NEW valid posts
                    if new_valid_this_page == 0:
                        consecutive_zero_pages += 1
                        if consecutive_zero_pages >= 2:
                            stats["stop_reason"] = "CONSECUTIVE_EMPTY_PAGES"
                            log.info("Query '%s' stopped: 2 consecutive pages produced zero new valid posts", query)
                            break
                    else:
                        consecutive_zero_pages = 0

                    # Early stopping condition 2 & 3: Cursor missing or repeated
                    if not next_cursor:
                        stats["stop_reason"] = "EXHAUSTED"
                        log.info("Query '%s' exhausted: no next cursor returned", query)
                        break
                    if next_cursor == cursor:
                        stats["stop_reason"] = "CURSOR_REPEATED"
                        log.info("Query '%s' stopped: cursor did not advance", query)
                        break

                    cursor = next_cursor
                    if page_idx == max_p:
                        stats["stop_reason"] = "PAGINATION_LIMIT"

                    time.sleep(random.uniform(1.2, 2.5))

                return valid_records[:target_posts], stats

            except Exception as proc_err:
                log.warning("In-process SearchTimeline failed, falling back to CLI: %s", proc_err)

        # Fallback via CLI runner
        try:
            raw_items = self.search(full_query, target_posts * 2, account=account)
            stats["pages_fetched"] = 1
            stats["raw_posts_received"] = len(raw_items)
            for item in raw_items:
                tid = str(item.get("id") or "").strip()
                if not tid:
                    continue
                text = item.get("text") or ""
                lang_meta = item.get("lang")

                if tid in seen_in_job:
                    stats["duplicate_posts"] += 1
                    continue
                created_dt = parse_created_at_utc(item.get("created_at") or item.get("createdAtISO"))
                if not created_dt or created_dt < cutoff_utc:
                    stats["out_of_range_posts"] += 1
                    continue
                is_fa, _ = validate_persian_post(text, lang_meta)
                if not is_fa:
                    stats["non_persian_posts"] += 1
                    continue

                seen_in_job.add(tid)
                valid_records.append(item)
                if len(valid_records) >= target_posts:
                    break

            stats["valid_posts"] = len(valid_records)
            stats["stop_reason"] = "TARGET_REACHED" if len(valid_records) >= target_posts else "EXHAUSTED"
            return valid_records[:target_posts], stats
        except Exception as cli_exc:
            stats["stop_reason"] = f"SEARCH_FAILED: {cli_exc}"
            raise

    def search(self, query: str, limit: int, account: Any | None = None) -> list[dict[str, Any]]:
        if query.lstrip().startswith("-"):
            raise XCliError("Search queries starting with '-' are not supported because they may be parsed as CLI options.")
        executable = shutil.which(self.command)
        if not executable:
            raise XCliError(f"CLI '{self.command}' was not found. Install/diagnose Agent Reach, then run 'agent-reach doctor'.")
        sub_env = os.environ.copy()
        if account is not None:
            if getattr(account, "auth_token", None):
                sub_env["TWITTER_AUTH_TOKEN"] = str(account.auth_token)
            if getattr(account, "ct0", None):
                sub_env["TWITTER_CT0"] = str(account.ct0)
            if getattr(account, "proxy_url", None):
                sub_env["TWITTER_PROXY"] = str(account.proxy_url)
        if not sub_env.get("TWITTER_AUTH_TOKEN") or not sub_env.get("TWITTER_CT0"):
            raise XCliError("X session cookies are missing. Configure TWITTER_AUTH_TOKEN and TWITTER_CT0 securely, then run 'agent-reach doctor'.")
        args = [executable, mapping.CLI_SEARCH_SUBCOMMAND, query, "-t", mapping.CLI_TIME_FILTER,
                mapping.CLI_EXCLUDE_RETWEETS_FLAG, mapping.CLI_EXCLUDE_RETWEETS_VALUE,
                mapping.CLI_LIMIT_FLAG, str(limit), mapping.CLI_OUTPUT_FLAG]
        try:
            completed = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=self.timeout, check=False, shell=False, env=sub_env)
        except subprocess.TimeoutExpired as exc:
            raise XCliError("X CLI search timed out", kind="transient") from exc
        except OSError as exc:
            raise XCliError(f"Could not start X CLI: {exc}") from exc
        try:
            payload = json.loads(completed.stdout) if completed.stdout.strip() else None
        except json.JSONDecodeError:
            payload = None
        if isinstance(payload, dict) and payload.get("ok") is False:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            code = str(error.get("code") or "unknown")
            if code == "rate_limited":
                raise XCliError("X CLI rate limit detected", kind="rate_limit")
            if code == "not_found":   # stale query id / endpoint for this search: skip the query, not the whole cycle
                raise XCliError("X CLI search endpoint returned 404 (twitter-cli query id may be stale)", kind="query")
            if code == "not_authenticated":
                raise XCliError("X CLI authentication failed; verify the session and run 'agent-reach doctor'")
            if code in {"timeout", "timed_out", "server_error", "internal_error", "bad_gateway", "service_unavailable"} or re.fullmatch(r"5\d\d", code):
                raise XCliError(f"X CLI transient server error ({code})", kind="transient")
            raise XCliError(f"X CLI failed with error code: {code}")
        if completed.returncode and not (isinstance(payload, dict) and payload.get("ok") is True):
            detail = (completed.stderr or completed.stdout or "CLI returned an error").strip()
            for secret in (sub_env.get("TWITTER_AUTH_TOKEN"), sub_env.get("TWITTER_CT0")):
                if secret:
                    detail = detail.replace(secret, "[REDACTED]")
            detail = detail[:500]
            lowered = detail.casefold()
            if re.search(r"\b(?:429|rate limit|too many requests)\b", lowered):
                raise XCliError("X CLI rate limit detected", kind="rate_limit")
            if re.search(r"\b(?:unauthorized|authentication failed|invalid credentials|session expired|login required)\b", lowered):
                raise XCliError("X CLI authentication failed; verify cookies and run 'agent-reach doctor'")
            if re.search(r"\b5\d\d\b", lowered):
                raise XCliError(f"X CLI transient server error (exit {completed.returncode})", kind="transient")
            raise XCliError(f"X CLI failed (exit {completed.returncode}): {detail}")
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = next((payload[key] for key in mapping.OUTPUT_ITEMS_KEYS if isinstance(payload.get(key), list)), None)
            if payload.get("ok") is True and items is None:
                items = []
        else:
            items = None
        if items is None:
            raise XCliError("X CLI output was not valid success JSON; verify the configured CLI output mode")
        if any(not isinstance(item, dict) for item in items):
            raise XCliError("X CLI JSON did not contain a recognized tweet list; update cli_mapping.py from the CLI's actual output")
        return [item for item in items if item.get("isRetweet") is not True][:limit]

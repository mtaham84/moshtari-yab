from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .core import ProductProfile, SourceRecord, normalize_text


class BaseSourceAdapter(ABC):
    @abstractmethod
    def search(self, query: str, **kwargs: Any) -> list[SourceRecord]:
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw_data: dict[str, Any]) -> SourceRecord:
        raise NotImplementedError


class XAdapter(BaseSourceAdapter):
    base_url = "https://api.x.com/2/tweets/search/recent"

    def __init__(self, bearer_token: str | None = None, max_requests: int = 100, timeout: int = 30):
        self.token = bearer_token or os.getenv("X_BEARER_TOKEN", "")
        self.max_requests, self.timeout, self.requests_used = max_requests, timeout, 0

    def normalize(self, raw_data: dict[str, Any]) -> SourceRecord:
        post_id, username = raw_data.get("id", ""), raw_data.get("username")
        url = raw_data.get("url") or (f"https://x.com/{username}/status/{post_id}" if username and post_id else None)
        return SourceRecord("x", str(post_id), raw_data.get("text", ""), url, raw_data.get("author_id"), f"@{username}" if username and not username.startswith("@") else username, raw_data.get("created_at"), raw_data.get("query_used"), raw_data)

    def search(self, query: str, **kwargs: Any) -> list[SourceRecord]:
        if not self.token:
            raise RuntimeError("X_BEARER_TOKEN is required for live X search")
        records, next_token = [], None
        while self.requests_used < self.max_requests:
            params = {"query": query, "max_results": 100, "tweet.fields": "author_id,created_at,conversation_id,lang,public_metrics", "expansions": "author_id", "user.fields": "username,name"}
            if next_token:
                params["next_token"] = next_token
            request = urllib.request.Request(self.base_url + "?" + urllib.parse.urlencode(params), headers={"Authorization": f"Bearer {self.token}"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read())
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    raise RuntimeError("RATE_LIMITED") from exc
                raise RuntimeError(f"X API returned HTTP {exc.code}") from exc
            self.requests_used += 1
            users = {u["id"]: u for u in payload.get("includes", {}).get("users", [])}
            for raw in payload.get("data", []):
                user = users.get(raw.get("author_id"), {})
                raw.update(username=user.get("username"), name=user.get("name"), query_used=query)
                records.append(self.normalize(raw))
            next_token = payload.get("meta", {}).get("next_token")
            if not next_token:
                break
        return records


class DivarAdapter(BaseSourceAdapter):
    def __init__(self, api_key: str | None = None, base_url: str | None = None, endpoint: str | None = None, max_requests: int = 30, timeout: int = 30):
        self.api_key = api_key or os.getenv("DIVAR_API_KEY", "")
        self.base_url = (base_url or os.getenv("DIVAR_BASE_URL", "https://open-api.divar.ir")).rstrip("/")
        self.endpoint = endpoint or os.getenv("DIVAR_SEARCH_ENDPOINT", "/v2/open-platform/finder/post")
        self.max_requests, self.timeout, self.requests_used = max_requests, timeout, 0

    def normalize(self, raw_data: dict[str, Any]) -> SourceRecord:
        token = raw_data.get("token") or raw_data.get("post_token") or raw_data.get("id") or ""
        text = " ".join(str(raw_data.get(k, "")) for k in ("title", "description", "text") if raw_data.get(k))
        return SourceRecord("divar", str(token), text, raw_data.get("url") or (f"https://divar.ir/v/{token}" if token else None), created_at=raw_data.get("created_at"), query_used=raw_data.get("query_used"), raw_data=raw_data)

    def search(self, query: str, **kwargs: Any) -> list[SourceRecord]:
        if not self.api_key:
            raise RuntimeError("DIVAR_API_KEY is required for live Divar search")
        if self.requests_used >= self.max_requests:
            return []
        url = self.base_url + "/" + self.endpoint.lstrip("/")
        body = json.dumps({"query": {"query_text": query}}).encode()
        request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", "x-api-key": self.api_key})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                raise RuntimeError("DIVAR_QUOTA_EXCEEDED") from exc
            raise RuntimeError(f"Divar API returned HTTP {exc.code}; verify documented schema and access") from exc
        self.requests_used += 1
        items = payload.get("posts", payload.get("items", payload.get("data", [])))
        if isinstance(items, dict):
            items = items.get("posts", [])
        return [self.normalize({**item, "query_used": query}) for item in items if isinstance(item, dict)]


MOCK_RECORDS = [
    {"source": "x", "id": "x1", "username": "cafe_owner", "text": "برای کافه‌ام دنبال دستگاه اسپرسوساز صنعتی هستم، ۲ دستگاه", "days": 1},
    {"source": "x", "id": "x2", "username": "buyer_two", "text": "برای کافه جدیدم 3 تا دستگاه اسپرسوساز لازم دارم تهران", "days": 2},
    {"source": "x", "id": "x3", "username": "seller_machine", "text": "فروش دستگاه اسپرسوساز صنعتی با تخفیف و ارسال", "days": 0},
    {"source": "x", "id": "x4", "username": "curious", "text": "قیمت دستگاه اسپرسوساز چنده؟", "days": 3},
    {"source": "x", "id": "x5", "username": "noise", "text": "امروز هوا عالیه و قهوه خوشمزه بود", "days": 0},
    {"source": "divar", "id": "d1", "title": "خریدار دستگاه اسپرسوساز برای کافه", "description": "برای راه‌اندازی کافه دستگاه اسپرسوساز می‌خواهم بخرم", "days": 4},
    {"source": "divar", "id": "d2", "title": "فروش دستگاه اسپرسوساز", "description": "اسپرسوساز صنعتی موجود، فروش عمده", "days": 1},
    {"source": "divar", "id": "d3", "title": "نیازمند دستگاه اسپرسوساز", "description": "برای رستوران 2 دستگاه لازم دارم", "days": 7},
]


class MockAdapter(BaseSourceAdapter):
    def __init__(self, source: str):
        self.source = source

    def normalize(self, raw_data: dict[str, Any]) -> SourceRecord:
        created = (datetime.now(timezone.utc) - timedelta(days=raw_data.get("days", 1))).isoformat()
        text = raw_data.get("text", "") or (raw_data.get("title", "") + " " + raw_data.get("description", ""))
        if raw_data["source"] == "x":
            url = f"https://x.com/{raw_data['username']}/status/{raw_data['id']}"
        else:
            url = f"https://divar.ir/v/{raw_data['id']}"
        return SourceRecord(raw_data["source"], str(raw_data["id"]), text, url, username=("@" + raw_data["username"]) if raw_data.get("username") else None, created_at=created, query_used=raw_data.get("query_used"), raw_data=raw_data)

    def search(self, query: str, **kwargs: Any) -> list[SourceRecord]:
        first_term = normalize_text(query.split()[0].strip('"'))
        return [self.normalize({**raw, "query_used": query}) for raw in MOCK_RECORDS if raw["source"] == self.source and first_term in normalize_text(raw.get("text", "") + raw.get("title", "") + raw.get("description", ""))]


class XFileAdapter(BaseSourceAdapter):
    def __init__(self, path: str | None = None):
        self.path = Path(path or os.getenv("X_COLLECT_INPUT_FILE", "data/x_collected/latest.jsonl"))
        if not self.path.is_file():
            daily_files = sorted(self.path.parent.glob("*.jsonl")) if self.path.parent.is_dir() else []
            if daily_files:
                self.path = daily_files[-1]

    def normalize(self, raw_data: dict[str, Any]) -> SourceRecord:
        return SourceRecord("x", str(raw_data.get("id", "")), str(raw_data.get("text", "")), raw_data.get("url"), raw_data.get("author_id"), raw_data.get("author_handle"), raw_data.get("created_at"), raw_data.get("query_used"), raw_data.get("metadata") or {})

    def search(self, query: str, **kwargs: Any) -> list[SourceRecord]:
        from sources.file_adapter import iter_records

        needle = normalize_text(query).replace('"', "")
        query_tokens = [token for token in needle.split() if len(token) > 2]
        records = []
        if not self.path.is_file():
            return []
        for raw in iter_records(self.path):
            text = normalize_text(raw.get("text", ""))
            if raw.get("source") == "x" and (needle in text or (query_tokens and all(token in text for token in query_tokens))):
                records.append(self.normalize(raw))
        return records


def build_x_adapter(**kwargs: Any) -> BaseSourceAdapter:
    backend = os.getenv("X_BACKEND", "api").strip().lower()
    if backend == "api":
        return XAdapter(**kwargs)
    if backend == "file":
        return XFileAdapter()
    raise ValueError("X_BACKEND must be one of: api, file")


def selected_x_backend() -> BaseSourceAdapter:
    return build_x_adapter()

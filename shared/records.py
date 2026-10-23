from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


class RecordError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SourceRecord:
    id: str
    source: Literal["x", "instagram", "telegram"]
    kind: Literal["post", "reply", "comment"]
    text: str
    author_handle: str | None
    author_id: str | None
    created_at: datetime | None
    url: str | None
    meta: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    present: frozenset[str] | None = field(default=None, repr=False, compare=False)

    @classmethod
    def from_dict(cls, data: object) -> SourceRecord:
        if not isinstance(data, dict):
            raise RecordError("record must be an object")
        record_id = data.get("id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise RecordError("id must be a non-empty string")
        source = data.get("source")
        if not isinstance(source, str) or source not in {"x", "instagram", "telegram"}:
            raise RecordError("source must be x, instagram, or telegram")
        kind = data.get("kind", "post")
        if not isinstance(kind, str) or kind not in {"post", "reply", "comment"}:
            raise RecordError("kind must be post, reply, or comment")
        if source == "instagram" and kind == "reply":
            raise RecordError("kind must be post or comment for Instagram records")
        text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise RecordError("text must be a non-empty string")
        if "author_handle" not in data:
            raise RecordError("author_handle is required")
        author_handle = data.get("author_handle")
        if author_handle is not None and not isinstance(author_handle, str):
            raise RecordError("author_handle must be a string or null")
        author_id = data.get("author_id")
        if "author_id" not in data:
            raise RecordError("author_id is required")
        if author_id is not None and (not isinstance(author_id, (str, int)) or isinstance(author_id, bool)):
            raise RecordError("author_id must be a string, integer, or null")
        created_at = _created_at(data.get("created_at"))
        url = data.get("url")
        if url is not None and not isinstance(url, str):
            raise RecordError("url must be a string or null")
        meta = data.get("metadata", data.get("meta", {}))
        if not isinstance(meta, dict):
            raise RecordError("metadata must be an object")
        standard_fields = {"id", "source", "kind", "text", "author_handle", "author_id", "created_at", "url", "metadata", "meta"}
        extra = {key: value for key, value in data.items() if key not in standard_fields}
        return cls(
            id=record_id, source=source, kind=kind, text=text, author_handle=author_handle,
            author_id=str(author_id) if author_id is not None else None, created_at=created_at,
            url=url, meta=dict(meta), extra=extra, present=frozenset(data),
        )

    def as_dict(self) -> dict[str, Any]:
        values = {
            "id": self.id, "source": self.source, "kind": self.kind, "text": self.text,
            "author_handle": self.author_handle, "author_id": self.author_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "url": self.url, "metadata": self.meta,
        }
        if self.present is not None:
            values = {key: value for key, value in values.items()
                      if key in self.present or (key == "metadata" and "meta" in self.present)}
            if "meta" in self.present and "metadata" not in self.present:
                values["meta"] = values.pop("metadata")
        values.update(self.extra)
        return values


def to_json_line(record: SourceRecord) -> str:
    return json.dumps(record.as_dict(), ensure_ascii=False, default=str, separators=(",", ":"))


def _created_at(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RecordError("created_at must be ISO-8601 or null") from exc
    else:
        raise RecordError("created_at must be ISO-8601 or null")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RecordError("created_at must include a timezone")
    return parsed.astimezone(timezone.utc)

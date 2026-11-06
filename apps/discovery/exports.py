"""Shared helpers for lead/opportunity exports (CSV and JSON)."""
import csv


class _Echo:
    """File-like object whose write() returns the line, for StreamingHttpResponse."""

    def write(self, value):
        return value


def safe_csv_cell(value):
    """Neutralise spreadsheet formulas (CSV injection): '=1+1' -> "'=1+1"."""
    value = "" if value is None else str(value)
    if value != "-" and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def safe_csv_row(values):
    return [safe_csv_cell(v) for v in values]


def stream_csv_rows(header, rows):
    """Yields encoded CSV lines (with a UTF-8 BOM first) for StreamingHttpResponse."""
    writer = csv.writer(_Echo())
    yield "\ufeff"
    yield writer.writerow(safe_csv_row(header))
    for row in rows:
        yield writer.writerow(safe_csv_row(row))


def instagram_lead_row(lead) -> dict:
    """One Instagram engine lead as plain data; the CSV and JSON admin exports share it."""
    return {
        "ig_user_pk": lead.ig_user_pk,
        "username": lead.username,
        "full_name": lead.full_name or "",
        "profile_url": lead.profile_url or f"https://www.instagram.com/{lead.username}/",
        "comment_text": lead.comment_text,
        "source_post_url": lead.source_post_url or "",
        "post_caption_snippet": lead.post_caption_snippet or "",
        "matched_cta": lead.matched_cta or "",
        "intent_score": lead.intent_score,
        "status": lead.status,
        "status_display": lead.get_status_display(),
        "tenant_id": lead.tenant_id,
        "created_at": lead.created_at,
    }

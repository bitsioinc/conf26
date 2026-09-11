"""Time-window and event-shaping logic for the Anthropic Admin API.

No Splunk imports and no network access, so this stays unit testable.
Python 3.9 compatible: Splunk's app runtime is CPython 3.9.25.
"""
from datetime import datetime
from datetime import timedelta, timezone

RFC3339 = "%Y-%m-%dT%H:%M:%SZ"


def _parse(ts):
    """Parse an RFC 3339 timestamp ('...Z') into an aware UTC datetime."""
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fmt(dt):
    return dt.strftime(RFC3339)


def _snap(dt, bucket_width):
    """Truncate down to the start of the containing bucket."""
    if bucket_width == "1d":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return dt.replace(minute=0, second=0, microsecond=0)


def compute_window(checkpoint, now, backfill_days, bucket_width):
    """Return (starting_at, ending_at) RFC 3339 strings, or None.

    ``ending_at`` is snapped down to the last *complete* bucket boundary, so a
    partially elapsed hour/day is never collected. ``starting_at`` resumes from
    ``checkpoint`` when present, otherwise it backfills ``backfill_days`` from
    the end of the window. Returns None when no complete bucket is pending.
    """
    ending = _snap(now.astimezone(timezone.utc), bucket_width)
    if checkpoint:
        starting = _parse(checkpoint)
    else:
        starting = _snap(ending - timedelta(days=backfill_days), bucket_width)
    if starting >= ending:
        return None
    return _fmt(starting), _fmt(ending)


def _flatten(page, enrich):
    """One event per (bucket, result); event time is the bucket start."""
    events = []
    for bucket in page.get("data", []):
        epoch = _parse(bucket["starting_at"]).timestamp()
        for result in bucket.get("results", []):
            body = dict(result)
            body["bucket_start"] = bucket["starting_at"]
            body["bucket_end"] = bucket["ending_at"]
            enrich(body)
            events.append({"time": epoch, "data": body})
    return events


def flatten_usage(page):
    """Flatten a usage_report page and add total_input_tokens.

    total_input_tokens = uncached + cache_read + BOTH cache_creation counts
    (the 5m and 1h ephemeral buckets).
    """
    def enrich(body):
        cache_creation = body.get("cache_creation") or {}
        body["total_input_tokens"] = (
            (body.get("uncached_input_tokens") or 0)
            + (body.get("cache_read_input_tokens") or 0)
            + (cache_creation.get("ephemeral_5m_input_tokens") or 0)
            + (cache_creation.get("ephemeral_1h_input_tokens") or 0)
        )

    return _flatten(page, enrich)


def flatten_cost(page):
    """Flatten a cost_report page; API amounts are cents, so emit dollars."""
    def enrich(body):
        body["amount_usd"] = round(float(body.get("amount") or 0) / 100.0, 6)

    return _flatten(page, enrich)


def max_ending_at(pages):
    """Newest bucket ending_at across pages -- the next checkpoint value."""
    endings = [b["ending_at"] for p in pages for b in p.get("data", [])]
    return max(endings) if endings else None

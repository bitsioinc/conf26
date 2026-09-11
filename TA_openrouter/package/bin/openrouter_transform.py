"""Window and event-shaping logic for the OpenRouter management API.

No Splunk imports and no network access, so this stays unit testable.
Python 3.9 compatible: Splunk's app runtime is CPython 3.9.25.

Money is USD. Unlike the Anthropic Admin API there is no cents divisor --
every currency metric is already in dollars.

Timestamps on the wire are inconsistent between endpoints. Some values are
RFC 3339 (``2026-07-31T18:00:00Z``). Real ``/analytics/query`` rows are not:
they arrive space-separated and offset-free (``"2026-07-31 18:00:00"``),
e.g. ``date__hour`` / ``created_at__hour`` bucket values. Both shapes are
UTC wall-clock time -- OpenRouter's analytics buckets, the ``/analytics/meta``
endpoint, and the ``time_range`` request params are all UTC -- so a value
with no explicit offset is treated as UTC rather than left ambiguous.
"""
from datetime import datetime, timedelta, timezone

RFC3339 = "%Y-%m-%dT%H:%M:%SZ"

#: /activity is capped at 30 completed UTC days. /analytics/query states no
#: limit, but it is unmeasured, so we clamp conservatively to the known figure.
MAX_BACKFILL_DAYS = 30


def _parse(ts):
    """Parse an API timestamp, assuming UTC when it carries no offset.

    Handles both RFC 3339 (``2026-07-31T18:00:00Z``) and the space-separated,
    offset-free shape real ``/analytics/query`` rows actually use
    (``"2026-07-31 18:00:00"``). A string that already carries an explicit
    offset is left exactly as parsed -- only naive results get UTC attached.
    """
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt(dt):
    return dt.strftime(RFC3339)


def _snap_hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def _int_or(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


#: One resume step per granularity value the API accepts for
#: ``/analytics/query``'s ``granularity`` parameter. "month" is deliberately
#: absent: it has no fixed length (28-31 days), so stepping a checkpoint
#: forward by "one month" has no single correct answer (calendar month?
#: fixed 30 days?) without inventing a convention nothing has asked for yet.
#: Nothing in this add-on currently requests month granularity --
#: openrouter_analytics_helper.GRANULARITY is "hour" -- so
#: compute_window raises rather than silently picking an approximation a
#: future caller didn't ask for.
GRANULARITY_STEPS = {
    "minute": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(weeks=1),
}


def _granularity_step(granularity):
    try:
        return GRANULARITY_STEPS[granularity]
    except KeyError:
        raise ValueError(
            "Unsupported granularity {!r}; compute_window supports {}".format(
                granularity, sorted(GRANULARITY_STEPS)
            )
        )


def compute_window(checkpoint, now, backfill_days, granularity="hour"):
    """Return (start, end) RFC 3339 strings, or None.

    ``now`` must be timezone-aware. Naive datetimes will be silently
    interpreted as system-local time, producing an analytics window shifted by
    the host's UTC offset. Pass timezone-aware datetime objects only.

    ``end`` snaps down to the last complete hour, so a partially elapsed hour
    is never collected -- re-running inside the same hour correctly yields
    nothing.

    ``start`` resumes one ``granularity`` step past ``checkpoint`` when a
    checkpoint is present, else backfills ``backfill_days`` clamped to
    MAX_BACKFILL_DAYS. ``checkpoint`` holds the START of the newest bucket
    already emitted (see ``max_bucket``), and the server's ``time_range``
    start bound is INCLUSIVE -- reusing ``checkpoint`` verbatim as the next
    window's start would re-request, and re-ingest, that same bucket on
    every single run, forever. Stepping past it by one ``granularity``
    interval (default: one hour, matching the analytics buckets this add-on
    requests) is what makes consecutive windows disjoint: re-running inside
    the same completed bucket correctly yields nothing further, and each new
    run picks up only buckets that completed since the last one.
    """
    if now.tzinfo is None:
        raise ValueError(
            "now must be timezone-aware; naive datetimes are silently "
            "interpreted as system-local time, producing incorrect analytics "
            "windows"
        )
    ending = _snap_hour(now.astimezone(timezone.utc))
    if checkpoint:
        starting = _parse(checkpoint) + _granularity_step(granularity)
    else:
        # Floor at 1, not just cap at MAX_BACKFILL_DAYS: backfill_days<=0
        # (0, a negative, or a numeric string of either) parses successfully
        # via _int_or -- it is not garbage, so it never hits _int_or's
        # MAX_BACKFILL_DAYS fallback -- and would otherwise make
        # `starting >= ending` below true on every single run, permanently
        # wedging this input with no checkpoint ever written to recover
        # from. 1 (not MAX_BACKFILL_DAYS) is the floor because a value that
        # parsed to <=1 still carries directional intent toward a *minimal*
        # backfill, which flooring to the smallest valid window honours;
        # collapsing it to the opposite end of the range (a 30-day backfill)
        # would surprise an operator who typed 0 expecting little to no
        # history, not the maximum. This mirrors the existing upper-bound
        # clamp (min(..., MAX_BACKFILL_DAYS)) as a symmetric lower bound,
        # rather than reusing the un-parseable-input fallback path, which
        # stays reserved for values with no directional intent to honour
        # (e.g. "abc", None).
        days = max(1, min(_int_or(backfill_days, MAX_BACKFILL_DAYS), MAX_BACKFILL_DAYS))
        starting = ending - timedelta(days=days)
    if starting >= ending:
        return None
    return _fmt(starting), _fmt(ending)


#: Metrics reported in USD by /analytics/meta (display_format == "currency").
#: Emitted with a _usd suffix so a reader never has to guess the unit.
CURRENCY_METRICS = (
    "total_usage", "byok_usage", "credits_usage", "openrouter_usage",
    "byok_fees", "usage_upstream", "usage_cache", "usage_data", "usage_web",
    "usage_upstream_web", "usage_file", "usage_upstream_file",
    "usage_web_fetch", "usage_upstream_web_fetch",
)

#: Currency fields on the /keys roster. Same reasoning as CURRENCY_METRICS.
KEY_CURRENCY_FIELDS = (
    "usage", "usage_daily", "usage_weekly", "usage_monthly",
    "byok_usage", "byok_usage_daily", "byok_usage_weekly", "byok_usage_monthly",
    "limit", "limit_remaining",
)

#: Metric field names per /analytics/meta -- the API's own definition of a
#: "metric" (a measured value) as opposed to a dimension like model,
#: api_key_id, or provider (free text, grouped-by, never numeric). Recorded
#: responses show these fields arriving as digit strings (request_count,
#: tokens_total, tokens_prompt, tokens_completion, cached_tokens), already
#: numeric (total_usage as float, byok_usage as int), and -- worse --
#: inconsistently typed for the identical field across sibling queries
#: (reasoning_tokens: string in analytics_by_key_model.json, int in
#: analytics_by_model_provider.json). flatten_analytics() coerces every
#: field in this set to a number via _coerce_number(). CURRENCY_METRICS is a
#: subset that additionally gets the _usd rename below. Fields NOT in this
#: set (model, api_key_id, provider, bucket_start, group_by, ...) are never
#: touched, so a model name like "4.1" can never become a float.
METRIC_FIELDS = CURRENCY_METRICS + (
    "request_count", "tokens_total", "tokens_prompt", "tokens_completion",
    "reasoning_tokens", "cached_tokens",
    "avg_latency", "p50_latency", "p90_latency", "p99_latency",
    "cache_hit_rate", "blended_cost_per_million_tokens",
    "avg_throughput", "p50_throughput", "p90_throughput", "p99_throughput",
    "guardrail_invoked_count", "guardrail_invoked_rate",
    "response_cached_count", "response_cached_rate",
    "byok_request_count",
)


def _rows(payload):
    return ((payload or {}).get("data") or {}).get("data") or []


def _metadata(payload):
    return ((payload or {}).get("data") or {}).get("metadata") or {}


def is_truncated(payload):
    """True when the server capped the result set.

    A truncated response means rows are missing. The caller MUST NOT advance
    its checkpoint past a truncated window -- doing so loses those rows
    permanently and silently.
    """
    return bool(_metadata(payload).get("truncated"))


def row_count(payload):
    """Row count the server reports in ``metadata``.

    Independent of how many events ``flatten_analytics`` ultimately
    produces (a row missing ``time_field`` is silently dropped there) --
    this is the server's own count, used for truncation diagnostics where
    the caller needs to say how many rows actually came back.
    """
    return _metadata(payload).get("row_count")


def max_bucket(payload, time_field="date"):
    """Newest time bucket across rows -- the next checkpoint value."""
    stamps = [r[time_field] for r in _rows(payload) if r.get(time_field)]
    return max(stamps) if stamps else None


def _suffix_currency(body, fields):
    for name in fields:
        if name in body:
            body[name + "_usd"] = body.pop(name)


def _coerce_number(value):
    """Best-effort numeric coercion for a single metric value.

    Real responses mix already-numeric values (float, int) with digit
    strings ("3", "10800") for the identical field -- sometimes across
    sibling queries for the identical field name. Tries ``int`` first
    (exact for count-style metrics), falls back to ``float`` (money and
    rate metrics), and returns the value completely unchanged if neither
    parses: a metric that starts arriving malformed must degrade visibly
    rather than silently become 0.
    """
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def flatten_analytics(payload, dimensions, time_field="date"):
    """One event per analytics row; event time is the bucket start.

    Rows are untyped and shaped by the request, so nothing is assumed beyond
    the presence of ``time_field``. Real ``time_field`` values are
    space-separated and offset-free (e.g. ``"2026-07-31 18:00:00"`` from
    ``date__hour`` / ``created_at__hour``); see ``_parse``. Fields in
    METRIC_FIELDS are coerced to numbers -- see METRIC_FIELDS and
    ``_coerce_number`` for why and how. ``dimensions`` is recorded on the
    event so downstream searches can tell the two queries apart.
    """
    events = []
    group_by = ",".join(dimensions)
    for row in _rows(payload):
        stamp = row.get(time_field)
        if not stamp:
            continue
        body = dict(row)
        body["bucket_start"] = stamp
        body["group_by"] = group_by
        for name in METRIC_FIELDS:
            if name in body:
                body[name] = _coerce_number(body[name])
        _suffix_currency(body, CURRENCY_METRICS)
        events.append({"time": _parse(stamp).timestamp(), "data": body})
    return events


def flatten_keys(keys, snapshot_at):
    """One event per API key, stamped with the snapshot time.

    There is no checkpoint for the key roster: every run emits the full list
    and downstream searches dedup on ``snapshot_at``.
    """
    epoch = _parse(snapshot_at).timestamp()
    events = []
    for key in keys or []:
        body = dict(key)
        body["snapshot_at"] = snapshot_at
        _suffix_currency(body, KEY_CURRENCY_FIELDS)
        events.append({"time": epoch, "data": body})
    return events

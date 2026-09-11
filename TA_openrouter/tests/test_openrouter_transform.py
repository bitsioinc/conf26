from datetime import datetime, timezone
import pytest

from openrouter_transform import compute_window, MAX_BACKFILL_DAYS

NOW = datetime(2026, 7, 31, 12, 34, 56, tzinfo=timezone.utc)


def test_first_run_backfills_and_snaps_to_hour():
    start, end = compute_window(None, NOW, backfill_days=7)
    assert start == "2026-07-24T12:00:00Z"
    assert end == "2026-07-31T12:00:00Z"


def test_resumes_from_checkpoint():
    # FIX 2 (93-agent audit): the checkpoint stores the newest
    # already-emitted bucket's START (see max_bucket / collect_query). The
    # server's time_range start bound is INCLUSIVE (see
    # openrouter_mockserver/app.py and
    # test_analytics_time_range_includes_bucket_at_start), so reusing the
    # checkpoint verbatim as the next window's start re-collects that same
    # bucket forever -- that was the bug. The resume point must be
    # checkpoint + one granularity step (default: one hour) so consecutive
    # windows are disjoint.
    start, end = compute_window("2026-07-31T10:00:00Z", NOW, backfill_days=7)
    assert start == "2026-07-31T11:00:00Z"
    assert end == "2026-07-31T12:00:00Z"


def test_resume_step_honours_explicit_day_granularity():
    # Analytics buckets are not always hourly -- the API accepts
    # minute/hour/day/week granularity. The resume step must be derived
    # from whichever granularity the caller actually requested, not
    # hardcoded to one hour.
    start, end = compute_window("2026-07-21T00:00:00Z", NOW, backfill_days=30,
                                 granularity="day")
    assert start == "2026-07-22T00:00:00Z"
    assert end == "2026-07-31T12:00:00Z"


def test_resume_step_honours_explicit_minute_granularity():
    start, end = compute_window("2026-07-31T11:58:00Z", NOW, backfill_days=7,
                                 granularity="minute")
    assert start == "2026-07-31T11:59:00Z"
    assert end == "2026-07-31T12:00:00Z"


def test_unsupported_granularity_raises_value_error():
    # "month" has no fixed length (28-31 days) so it is deliberately not in
    # the supported step map -- a caller that ever needs it must decide its
    # own semantics rather than inherit a silent approximation here. Failing
    # loudly beats silently mis-stepping the checkpoint.
    with pytest.raises(ValueError):
        compute_window("2026-07-31T10:00:00Z", NOW, backfill_days=7,
                        granularity="month")


def test_partial_hour_is_never_collected():
    # 12:34 is mid-hour; the window must end at 12:00, not 12:34.
    _, end = compute_window(None, NOW, backfill_days=1)
    assert end == "2026-07-31T12:00:00Z"


def test_no_complete_bucket_returns_none():
    assert compute_window("2026-07-31T12:00:00Z", NOW, backfill_days=7) is None


def test_backfill_clamps_to_retention_cap():
    start, _ = compute_window(None, NOW, backfill_days=365)
    assert start == "2026-07-01T12:00:00Z"   # 30 days, not 365
    assert MAX_BACKFILL_DAYS == 30


def test_backfill_days_garbage_falls_back_to_cap():
    start, _ = compute_window(None, NOW, backfill_days=None)
    assert start == "2026-07-01T12:00:00Z"


# --- Blocker 2 (99-agent final review): backfill_days<=0 must not
# permanently wedge the window at None -----------------------------------
#
# backfill_days=0 is a plausible operator input ("no backfill"), and unlike
# "abc"/None it parses to a real int via _int_or -- so it never reaches the
# garbage fallback above. Before the fix, `ending - timedelta(days=0)` made
# `starting == ending`, and compute_window's own "no complete bucket"
# contract (test_no_complete_bucket_returns_none) correctly returns None for
# that -- forever, since no checkpoint is ever written to move past it. A
# negative value made it worse (starting > ending). compute_window now
# floors the backfill at 1 day rather than letting either case reach that
# equal/inverted state.

def test_backfill_days_zero_does_not_wedge_the_window():
    start, end = compute_window(None, NOW, backfill_days=0)
    assert start is not None
    assert end is not None
    assert start == "2026-07-30T12:00:00Z"  # floored to 1 day, not 0


def test_backfill_days_negative_does_not_wedge_the_window():
    start, end = compute_window(None, NOW, backfill_days=-1)
    assert start is not None
    assert end is not None
    assert start == "2026-07-30T12:00:00Z"  # floored to 1 day, not -1


def test_backfill_days_string_zero_does_not_wedge_the_window():
    # A UI text field submits strings, not ints -- "0" must be floored the
    # same way the int 0 is, not treated as unparseable garbage (which
    # would coincidentally also avoid None, but for the wrong reason and
    # via the wrong fallback -- see test_backfill_days_garbage_falls_back_to_cap
    # which returns the 30-day cap, not the 1-day floor this path takes).
    start, end = compute_window(None, NOW, backfill_days="0")
    assert start is not None
    assert end is not None
    assert start == "2026-07-30T12:00:00Z"


def test_naive_datetime_raises_value_error():
    # now must be timezone-aware; naive datetimes are silently interpreted
    # as system-local time, producing incorrect analytics windows.
    naive_now = datetime(2026, 7, 31, 12, 34, 56)
    with pytest.raises(ValueError):
        compute_window(None, naive_now, backfill_days=7)


from openrouter_transform import (
    flatten_analytics, flatten_keys, max_bucket, is_truncated,
)

SAMPLE = {
    "data": {
        "data": [
            {"date": "2026-07-31T10:00:00Z", "api_key_id": "hash_a",
             "model": "openai/gpt-4.1", "request_count": 10,
             "total_usage": 1.25, "tokens_total": 5000},
            {"date": "2026-07-31T11:00:00Z", "api_key_id": "hash_b",
             "model": "anthropic/claude-opus-5", "request_count": 3,
             "total_usage": 0.5, "tokens_total": 900},
        ],
        "metadata": {"row_count": 2, "query_time_ms": 12, "truncated": False},
        "warnings": [],
    }
}


def test_flatten_analytics_one_event_per_row():
    events = flatten_analytics(SAMPLE, ["api_key_id", "model"])
    assert len(events) == 2
    assert events[0]["time"] == datetime(
        2026, 7, 31, 10, 0, tzinfo=timezone.utc).timestamp()
    assert events[0]["data"]["api_key_id"] == "hash_a"
    assert events[0]["data"]["model"] == "openai/gpt-4.1"


def test_flatten_analytics_renames_currency_metrics_to_usd():
    events = flatten_analytics(SAMPLE, ["api_key_id", "model"])
    assert events[0]["data"]["total_usage_usd"] == 1.25
    assert "total_usage" not in events[0]["data"]


def test_flatten_analytics_records_bucket_and_dimensions():
    events = flatten_analytics(SAMPLE, ["api_key_id", "model"])
    assert events[0]["data"]["bucket_start"] == "2026-07-31T10:00:00Z"
    assert events[0]["data"]["group_by"] == "api_key_id,model"


def test_flatten_analytics_tolerates_missing_metrics():
    payload = {"data": {"data": [{"date": "2026-07-31T10:00:00Z",
                                  "model": "x"}], "metadata": {}}}
    events = flatten_analytics(payload, ["model"])
    assert events[0]["data"]["model"] == "x"


def test_flatten_analytics_empty_is_empty():
    assert flatten_analytics({"data": {"data": [], "metadata": {}}}, ["model"]) == []


def test_max_bucket_returns_newest():
    assert max_bucket(SAMPLE) == "2026-07-31T11:00:00Z"


def test_max_bucket_none_when_empty():
    assert max_bucket({"data": {"data": [], "metadata": {}}}) is None


def test_is_truncated_reads_metadata():
    assert is_truncated(SAMPLE) is False
    assert is_truncated({"data": {"data": [], "metadata": {"truncated": True}}}) is True


def test_is_truncated_missing_metadata_is_false():
    assert is_truncated({"data": {"data": []}}) is False


def test_flatten_keys_stamps_snapshot():
    keys = [{"hash": "hash_a", "name": "prod", "disabled": False, "usage": 12.5}]
    events = flatten_keys(keys, "2026-07-31T12:00:00Z")
    assert len(events) == 1
    assert events[0]["data"]["snapshot_at"] == "2026-07-31T12:00:00Z"
    assert events[0]["data"]["hash"] == "hash_a"
    assert events[0]["data"]["usage_usd"] == 12.5


# --- Task 3b: real OpenRouter wire format ---------------------------------
#
# /analytics/query actually returns space-separated, offset-free timestamps
# (e.g. "2026-07-31 18:00:00", not "2026-07-31T18:00:00Z") and several
# metrics as strings. Rows below are taken verbatim from the recorded
# fixtures: TA_openrouter/openrouter_mockserver/fixtures/analytics_by_key_model.json
# and analytics_by_model_provider.json.

REAL_ROW_KEY_MODEL = {
    "date__hour": "2026-07-31 18:00:00",
    "api_key_id": "Alpaca",
    "model": "moonshotai/kimi-k2.6-20260420",
    "request_count": "3",
    "total_usage": 0.012887,
    "tokens_total": "10800",
    "tokens_prompt": "6278",
    "tokens_completion": "4522",
    "reasoning_tokens": "4373",
    "cached_tokens": "4131",
    "byok_usage": 0,
}

REAL_ROW_MODEL_PROVIDER = {
    "created_at__hour": "2026-07-31 18:00:00",
    "model": "moonshotai/kimi-k2.6-20260420",
    "provider": "Baidu",
    "request_count": "3",
    "total_usage": 0.012887,
    "tokens_total": "10800",
    "tokens_prompt": "6278",
    "tokens_completion": "4522",
    "reasoning_tokens": 4373,  # int here; string in the sibling query above
    "cached_tokens": "4131",
    "byok_usage": 0,
}


def test_naive_space_separated_stamp_is_treated_as_utc():
    # Real /analytics/query rows carry offset-free, space-separated
    # timestamps. Asserting against a known UTC epoch means a host-local
    # misinterpretation fails this test on any machine, not just ones west
    # of UTC (this repo measured a -5.0 hour skew).
    payload = {"data": {"data": [REAL_ROW_KEY_MODEL], "metadata": {}}}
    events = flatten_analytics(payload, ["api_key_id", "model"],
                                time_field="date__hour")
    assert events[0]["time"] == datetime(
        2026, 7, 31, 18, 0, 0, tzinfo=timezone.utc).timestamp()


def test_checkpoint_round_trip_through_max_bucket_does_not_raise():
    # max_bucket() hands back a raw, offset-free API timestamp as the next
    # checkpoint. compute_window() must accept it without raising, even
    # though `now` is tz-aware.
    payload = {"data": {"data": [REAL_ROW_KEY_MODEL], "metadata": {}}}
    checkpoint = max_bucket(payload, time_field="date__hour")
    assert checkpoint == "2026-07-31 18:00:00"
    later = datetime(2026, 7, 31, 20, 0, 0, tzinfo=timezone.utc)
    start, end = compute_window(checkpoint, later, backfill_days=7)
    assert start is not None
    assert end is not None


def test_offset_bearing_stamp_is_left_unchanged():
    # A timestamp that DOES carry an explicit offset must not be
    # reinterpreted as UTC.
    row = {"date": "2026-07-31T10:00:00+05:30", "model": "x"}
    payload = {"data": {"data": [row], "metadata": {}}}
    events = flatten_analytics(payload, ["model"])
    expected = datetime.fromisoformat("2026-07-31T10:00:00+05:30").timestamp()
    assert events[0]["time"] == expected


def test_string_metrics_are_coerced_to_numbers():
    payload = {"data": {"data": [REAL_ROW_KEY_MODEL], "metadata": {}}}
    events = flatten_analytics(payload, ["api_key_id", "model"],
                                time_field="date__hour")
    data = events[0]["data"]
    assert data["request_count"] == 3
    assert isinstance(data["request_count"], int)
    assert data["tokens_total"] == 10800
    assert isinstance(data["tokens_total"], int)
    assert data["tokens_prompt"] == 6278
    assert data["tokens_completion"] == 4522
    assert data["cached_tokens"] == 4131
    assert data["reasoning_tokens"] == 4373


def test_reasoning_tokens_coerces_consistently_across_queries():
    # Same field name, string in one recorded response and int in the
    # sibling response -- both must come out as the same numeric value.
    payload_a = {"data": {"data": [REAL_ROW_KEY_MODEL], "metadata": {}}}
    payload_b = {"data": {"data": [REAL_ROW_MODEL_PROVIDER], "metadata": {}}}
    events_a = flatten_analytics(payload_a, ["api_key_id", "model"],
                                  time_field="date__hour")
    events_b = flatten_analytics(payload_b, ["model", "provider"],
                                  time_field="created_at__hour")
    assert events_a[0]["data"]["reasoning_tokens"] == 4373
    assert events_b[0]["data"]["reasoning_tokens"] == 4373
    assert isinstance(events_a[0]["data"]["reasoning_tokens"], int)
    assert isinstance(events_b[0]["data"]["reasoning_tokens"], int)


def test_non_numeric_metric_value_is_preserved_not_zeroed():
    row = dict(REAL_ROW_KEY_MODEL)
    row["request_count"] = "n/a"
    payload = {"data": {"data": [row], "metadata": {}}}
    events = flatten_analytics(payload, ["api_key_id", "model"],
                                time_field="date__hour")
    assert events[0]["data"]["request_count"] == "n/a"


def test_textual_fields_are_never_coerced():
    row = dict(REAL_ROW_KEY_MODEL)
    row["model"] = "4.1"  # a model name that looks numeric
    payload = {"data": {"data": [row], "metadata": {}}}
    events = flatten_analytics(payload, ["api_key_id", "model"],
                                time_field="date__hour")
    assert events[0]["data"]["model"] == "4.1"
    assert isinstance(events[0]["data"]["model"], str)
    assert events[0]["data"]["api_key_id"] == "Alpaca"

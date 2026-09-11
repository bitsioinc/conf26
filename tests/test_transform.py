import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from anthropic_transform import compute_window, flatten_usage, flatten_cost, max_ending_at

FIXTURES = Path(__file__).parent.parent / "mockserver" / "fixtures"
NOW = datetime(2026, 7, 30, 12, 34, 56, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_first_run_backfills_and_snaps_to_hour():
    start, end = compute_window(None, NOW, backfill_days=7, bucket_width="1h")
    assert start == "2026-07-23T12:00:00Z"
    assert end == "2026-07-30T12:00:00Z"


def test_resumes_from_checkpoint():
    start, end = compute_window("2026-07-30T10:00:00Z", NOW, backfill_days=7, bucket_width="1h")
    assert start == "2026-07-30T10:00:00Z"
    assert end == "2026-07-30T12:00:00Z"


def test_empty_window_returns_none():
    assert compute_window("2026-07-30T12:00:00Z", NOW, 7, "1h") is None


def test_daily_snapping():
    start, end = compute_window(None, NOW, backfill_days=30, bucket_width="1d")
    assert start == "2026-06-30T00:00:00Z"
    assert end == "2026-07-30T00:00:00Z"


def test_flatten_usage_events():
    events = flatten_usage(load("usage_report_page1.json"))
    assert len(events) == 3  # 2 results in bucket 1 + 1 in bucket 2
    first = events[0]
    assert first["time"] == datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc).timestamp()
    assert first["data"]["model"] == "claude-opus-5"
    assert first["data"]["total_input_tokens"] == 84100 + 611000 + 41200 + 0


def test_flatten_cost_converts_cents():
    # The Admin API returns `amount` in the lowest currency unit (cents), so
    # "41250.00" cents is $412.50 -- NOT $41,250.
    events = flatten_cost(load("cost_report.json"))
    assert events[0]["data"]["amount"] == "41250.00"
    assert events[0]["data"]["amount_usd"] == 412.5
    assert events[0]["data"]["currency"] == "USD"


def test_max_ending_at():
    pages = [load("usage_report_page1.json"), load("usage_report_page2.json")]
    assert max_ending_at(pages) == "2026-07-28T17:00:00Z"


# ---- Additional coverage beyond the plan's baseline seven ----------------


def test_checkpoint_ahead_of_now_returns_none():
    assert compute_window("2026-08-01T00:00:00Z", NOW, 7, "1h") is None


def test_partial_hour_is_never_emitted():
    # NOW is 12:34:56; the 12:00-13:00 bucket is still open, so the window
    # must stop at 12:00:00Z, not include a partial bucket.
    _, end = compute_window("2026-07-30T11:00:00Z", NOW, 7, "1h")
    assert end == "2026-07-30T12:00:00Z"


def test_non_utc_now_is_normalised():
    ist = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime(2026, 7, 30, 18, 4, 56, tzinfo=ist)  # == 12:34:56Z
    start, end = compute_window(None, now_ist, backfill_days=1, bucket_width="1h")
    assert (start, end) == ("2026-07-29T12:00:00Z", "2026-07-30T12:00:00Z")


def test_daily_window_none_when_checkpoint_is_today():
    assert compute_window("2026-07-30T00:00:00Z", NOW, 30, "1d") is None


def test_flatten_usage_carries_bucket_bounds_and_all_result_fields():
    events = flatten_usage(load("usage_report_page1.json"))
    body = events[0]["data"]
    assert body["bucket_start"] == "2026-07-28T14:00:00Z"
    assert body["bucket_end"] == "2026-07-28T15:00:00Z"
    for key in ("api_key_id", "workspace_id", "service_tier", "output_tokens",
                "cache_creation", "cache_read_input_tokens", "uncached_input_tokens"):
        assert key in body
    # second event belongs to the same bucket -> same event time
    assert events[1]["time"] == events[0]["time"]
    assert events[1]["data"]["api_key_id"] == "apikey_demo_claudecode"
    # third event is the next bucket
    assert events[2]["data"]["bucket_start"] == "2026-07-28T15:00:00Z"


def test_flatten_usage_counts_both_cache_creation_buckets():
    events = flatten_usage(load("usage_report_page1.json"))
    second = events[1]["data"]  # 1h cache creation, 5m zero
    assert second["cache_creation"]["ephemeral_1h_input_tokens"] == 120000
    assert second["total_input_tokens"] == (
        second["uncached_input_tokens"]
        + second["cache_read_input_tokens"]
        + second["cache_creation"]["ephemeral_5m_input_tokens"]
        + second["cache_creation"]["ephemeral_1h_input_tokens"]
    )


def test_flatten_usage_tolerates_missing_fields():
    page = {"data": [{"starting_at": "2026-07-28T14:00:00Z",
                      "ending_at": "2026-07-28T15:00:00Z",
                      "results": [{"model": "claude-opus-5"}]}]}
    events = flatten_usage(page)
    assert events[0]["data"]["total_input_tokens"] == 0


def test_flatten_does_not_mutate_source_page():
    page = load("usage_report_page1.json")
    flatten_usage(page)
    assert "total_input_tokens" not in page["data"][0]["results"][0]
    assert "bucket_start" not in page["data"][0]["results"][0]


def test_flatten_cost_all_rows_and_epochs():
    page = load("cost_report.json")
    events = flatten_cost(page)
    expected = sum(len(b["results"]) for b in page["data"])
    assert len(events) == expected
    assert events[0]["time"] == datetime(2026, 7, 27, 0, 0, tzinfo=timezone.utc).timestamp()
    for ev in events:
        assert ev["data"]["amount_usd"] == round(float(ev["data"]["amount"]) / 100.0, 6)


def test_flatten_cost_handles_missing_amount():
    page = {"data": [{"starting_at": "2026-07-27T00:00:00Z",
                      "ending_at": "2026-07-28T00:00:00Z",
                      "results": [{"currency": "USD"}]}]}
    assert flatten_cost(page)[0]["data"]["amount_usd"] == 0.0


def test_max_ending_at_empty_and_single_page():
    assert max_ending_at([]) is None
    assert max_ending_at([{"data": [], "has_more": False}]) is None
    assert max_ending_at([load("usage_report_page1.json")]) == "2026-07-28T16:00:00Z"


def test_max_ending_at_ignores_page_order():
    pages = [load("usage_report_page2.json"), load("usage_report_page1.json")]
    assert max_ending_at(pages) == "2026-07-28T17:00:00Z"


def test_max_ending_at_feeds_next_compute_window():
    # The checkpoint round-trip: newest ending_at becomes the next start.
    pages = [load("usage_report_page1.json"), load("usage_report_page2.json")]
    checkpoint = max_ending_at(pages)
    now = datetime(2026, 7, 28, 19, 5, 0, tzinfo=timezone.utc)
    start, end = compute_window(checkpoint, now, backfill_days=7, bucket_width="1h")
    assert start == "2026-07-28T17:00:00Z"
    assert end == "2026-07-28T19:00:00Z"

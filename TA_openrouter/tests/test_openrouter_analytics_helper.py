from datetime import datetime, timedelta, timezone

from openrouter_analytics_helper import QUERIES, collect_query

NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)


class StubClient(object):
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def query_analytics(self, metrics, dimensions, granularity, start, end,
                        limit=None):
        self.calls.append((tuple(metrics), tuple(dimensions), granularity,
                           start, end))
        return self._payload


def payload(rows, truncated=False):
    return {"data": {"data": rows,
                     "metadata": {"truncated": truncated, "row_count": len(rows)},
                     "warnings": []}}


# CORRECTED vs. the task brief: schema-verification.md (VERIFIED against a
# live recording) established that the time-bucket column differs per query
# -- date__hour for [api_key_id, model], created_at__hour for [model,
# provider] -- and that real /analytics/query rows are space-separated and
# offset-free ("2026-07-31 10:00:00"), not RFC 3339. The brief's rows used a
# single {"date": "...Z"} shape that matches neither real query and would
# have every row silently skipped by flatten_analytics/max_bucket.
ROWS_KEY_MODEL = [
    {"date__hour": "2026-07-31 10:00:00", "api_key_id": "Alpaca", "model": "m",
     "total_usage": 1.0, "request_count": "2"},
    {"date__hour": "2026-07-31 11:00:00", "api_key_id": "Nimbus_Prod_Key",
     "model": "m", "total_usage": 2.0, "request_count": "3"},
]

ROWS_MODEL_PROVIDER = [
    {"created_at__hour": "2026-07-31 10:00:00", "model": "m", "provider": "Baidu",
     "total_usage": 1.0, "request_count": "2"},
    {"created_at__hour": "2026-07-31 11:00:00", "model": "m", "provider": "Fireworks",
     "total_usage": 2.0, "request_count": "3"},
]


def test_two_queries_are_defined_with_distinct_checkpoints():
    assert len(QUERIES) == 2
    suffixes = [q["checkpoint_suffix"] for q in QUERIES]
    assert len(set(suffixes)) == 2
    assert [q["sourcetype"] for q in QUERIES] == [
        "openrouter:analytics", "openrouter:providers"]


def test_queries_carry_correct_time_field():
    # This is the difference between working and silently ingesting nothing.
    # flatten_analytics/max_bucket both default to time_field="date", which
    # matches neither real query's bucket column -- the human partner ruled
    # each QUERIES entry must carry its own explicit, correct time_field
    # rather than have collect_query auto-detect it.
    by_suffix = {q["checkpoint_suffix"]: q for q in QUERIES}
    assert by_suffix["analytics_by_key_model"]["time_field"] == "date__hour"
    assert by_suffix["analytics_by_model_provider"]["time_field"] == "created_at__hour"


def test_queries_use_only_additive_metrics():
    # Rate metrics (is_rate true per analytics_meta.json) cannot be summed
    # across buckets.
    rate_metrics = {
        "avg_latency", "p50_latency", "p90_latency", "p99_latency",
        "avg_throughput", "p50_throughput", "p90_throughput", "p99_throughput",
        "cache_hit_rate", "blended_cost_per_million_tokens",
        "guardrail_invoked_rate", "response_cached_rate",
    }
    for query in QUERIES:
        assert not set(query["metrics"]) & rate_metrics, query["sourcetype"]


def test_query_dimensions_cover_key_model_and_provider():
    dims = [tuple(q["dimensions"]) for q in QUERIES]
    assert ("api_key_id", "model") in dims
    assert ("model", "provider") in dims


def test_collect_returns_events_and_advances_checkpoint():
    client = StubClient(payload(ROWS_KEY_MODEL))
    events, checkpoint = collect_query(client, QUERIES[0], None, NOW, 7)
    assert len(events) == 2
    assert checkpoint == "2026-07-31 11:00:00"


def test_collect_does_not_advance_checkpoint_when_truncated():
    # Advancing past a truncated response drops the missing rows forever.
    client = StubClient(payload(ROWS_KEY_MODEL, truncated=True))
    events, checkpoint = collect_query(client, QUERIES[0], "2026-07-31 09:00:00",
                                       NOW, 7)
    assert checkpoint is None, "must not advance past a truncated window"
    assert events == [], "must not emit a partial window"


def test_collect_query_diagnostics_flag_truncation_distinctly():
    # FIX 3 (93-agent audit): collect_query returns ([], None) for THREE
    # different conditions -- truncation, an empty window (no complete
    # bucket yet), and a genuinely-zero-row window -- and they are
    # indistinguishable from the return value alone. Truncation is the one
    # that needs a WARN (rows are being silently withheld, forever, until
    # the input's interval is shortened); the other two are unremarkable
    # and should stay at INFO. diagnostics["reason"] is the channel the
    # caller (stream_events) uses to tell them apart without changing
    # collect_query's 2-tuple return shape or breaking any existing caller.
    truncated_client = StubClient(payload(ROWS_KEY_MODEL, truncated=True))
    diagnostics = {}
    events, checkpoint = collect_query(
        truncated_client, QUERIES[0], "2026-07-31 09:00:00", NOW, 7,
        diagnostics=diagnostics,
    )
    assert (events, checkpoint) == ([], None), "truncation guard must still hold"
    assert diagnostics["reason"] == "truncated"
    assert diagnostics["row_count"] == len(ROWS_KEY_MODEL)
    assert diagnostics["window"] == (
        "2026-07-31T10:00:00Z", "2026-07-31T12:00:00Z"
    )


def test_collect_query_diagnostics_flag_no_window_distinctly_from_truncation():
    client = StubClient(payload([]))
    diagnostics = {}
    events, checkpoint = collect_query(
        client, QUERIES[0], "2026-07-31 12:00:00", NOW, 7,
        diagnostics=diagnostics,
    )
    assert (events, checkpoint) == ([], None)
    assert diagnostics["reason"] == "no_window"
    assert client.calls == []


def test_collect_query_diagnostics_flag_empty_window_distinctly_from_truncation():
    client = StubClient(payload([]))
    diagnostics = {}
    events, checkpoint = collect_query(
        client, QUERIES[0], "2026-07-31 10:00:00", NOW, 7,
        diagnostics=diagnostics,
    )
    assert (events, checkpoint) == ([], None)
    assert diagnostics["reason"] == "empty"


def test_collect_query_diagnostics_flag_success():
    client = StubClient(payload(ROWS_KEY_MODEL))
    diagnostics = {}
    events, checkpoint = collect_query(
        client, QUERIES[0], None, NOW, 7, diagnostics=diagnostics,
    )
    assert len(events) == 2
    assert diagnostics["reason"] == "ok"


def test_collect_query_diagnostics_parameter_is_optional_and_backward_compatible():
    # Every pre-existing caller calls collect_query with exactly 5
    # positional args and unpacks a plain 2-tuple. That must keep working
    # completely unmodified.
    client = StubClient(payload(ROWS_KEY_MODEL))
    events, checkpoint = collect_query(client, QUERIES[0], None, NOW, 7)
    assert len(events) == 2
    assert checkpoint == "2026-07-31 11:00:00"


def test_collect_makes_no_call_when_window_is_empty():
    client = StubClient(payload([]))
    events, checkpoint = collect_query(client, QUERIES[0], "2026-07-31 12:00:00",
                                       NOW, 7)
    assert events == []
    assert checkpoint is None
    assert client.calls == [], "no API call when there is no complete bucket"


def test_collect_holds_checkpoint_when_window_has_no_rows():
    client = StubClient(payload([]))
    events, checkpoint = collect_query(client, QUERIES[0], "2026-07-31 10:00:00",
                                       NOW, 7)
    assert events == []
    assert checkpoint is None


def test_collect_passes_window_to_client():
    # FIX 2 (93-agent audit): checkpoint "09:00:00" must resume at
    # "10:00:00" (checkpoint + one GRANULARITY step), not "09:00:00" -- the
    # old, buggy value re-requested (and re-ingested) the already-emitted
    # 09:00 bucket on every single run. See test_openrouter_transform.py's
    # test_resumes_from_checkpoint for the compute_window-level version of
    # this same fix.
    client = StubClient(payload(ROWS_KEY_MODEL))
    collect_query(client, QUERIES[0], "2026-07-31 09:00:00", NOW, 7)
    _, _, granularity, start, end = client.calls[0]
    assert granularity == "hour"
    assert start == "2026-07-31T10:00:00Z"
    assert end == "2026-07-31T12:00:00Z"


def test_collect_model_provider_query_uses_created_at_hour_and_produces_events():
    # Guards the defect that motivated the time_field correction: this is
    # the [model, provider] query, whose real bucket column is
    # created_at__hour, not date__hour and not the flatten_analytics/
    # max_bucket default of "date". A wiring mistake here (e.g. reusing
    # QUERIES[0]'s time_field, or falling back to the default) makes this
    # query silently collect zero events forever with no error anywhere --
    # exactly the scenario schema-verification.md warns about. Exercising
    # only the [api_key_id, model] / date__hour combination would not catch
    # that regression.
    client = StubClient(payload(ROWS_MODEL_PROVIDER))
    events, checkpoint = collect_query(client, QUERIES[1], None, NOW, 7)
    assert len(events) == 2
    assert checkpoint == "2026-07-31 11:00:00"
    assert events[0]["data"]["bucket_start"] == "2026-07-31 10:00:00"
    assert events[0]["data"]["provider"] == "Baidu"


# --- FIX 2 (93-agent audit): disjointness regression --------------------
#
# max_bucket() returns the newest bucket's START, and the pre-fix
# compute_window() reused that value verbatim as the NEXT window's
# (inclusive) start. The controller reproduced this over five consecutive
# runs against the real fixture: the newest hour was re-collected on every
# single run, forever, inflating every sum(total_usage_usd) without bound.
#
# StubClient (above) ignores start/end entirely, so it cannot catch this --
# it always returns the same rows no matter what window is requested. This
# stub actually filters by [start, end) the way the real
# openrouter_mockserver/app.py does (start-inclusive, end-exclusive; see
# test_analytics_time_range_includes_bucket_at_start), so a checkpoint bug
# that re-requests an already-covered bucket actually re-fetches an
# overlapping row here too.
class FilteringStubClient(object):
    def __init__(self, rows, time_field):
        self._rows = rows
        self._time_field = time_field
        self.calls = []

    @staticmethod
    def _parse(ts):
        if ts.endswith("Z"):
            return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=timezone.utc)

    def query_analytics(self, metrics, dimensions, granularity, start, end,
                        limit=None):
        self.calls.append((tuple(metrics), tuple(dimensions), granularity,
                           start, end))
        start_dt, end_dt = self._parse(start), self._parse(end)
        rows = [r for r in self._rows
                if start_dt <= self._parse(r[self._time_field]) < end_dt]
        return payload(rows)


# Four consecutive hourly buckets for the same (api_key_id, model) pair, so
# a duplicate shows up as an exact tuple collision rather than merely a
# row-count mismatch.
FOUR_HOUR_ROWS_KEY_MODEL = [
    {"date__hour": "2026-07-31 09:00:00", "api_key_id": "Alpaca", "model": "m",
     "total_usage": 1.0, "request_count": "2"},
    {"date__hour": "2026-07-31 10:00:00", "api_key_id": "Alpaca", "model": "m",
     "total_usage": 1.0, "request_count": "2"},
    {"date__hour": "2026-07-31 11:00:00", "api_key_id": "Alpaca", "model": "m",
     "total_usage": 2.0, "request_count": "3"},
    {"date__hour": "2026-07-31 12:00:00", "api_key_id": "Alpaca", "model": "m",
     "total_usage": 3.0, "request_count": "4"},
]


def _bucket_tuples(events):
    return {(e["data"]["bucket_start"], e["data"]["api_key_id"], e["data"]["model"])
            for e in events}


def test_consecutive_collect_query_runs_emit_disjoint_bucket_tuples():
    # Reproduces the controller's five-run duplicate-ingest defect at unit
    # scale: feed run 1's returned checkpoint straight into run 2 (exactly
    # what stream_events does every interval) and assert no
    # (bucket_start, api_key_id, model) tuple is emitted by both runs.
    #
    # Against the unfixed compute_window (checkpoint reused verbatim as an
    # inclusive start) run 2's window starts back at run 1's newest bucket,
    # re-collecting it -- this assertion fails on that code.
    client = FilteringStubClient(FOUR_HOUR_ROWS_KEY_MODEL, "date__hour")

    run1_now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    events1, ckpt1 = collect_query(client, QUERIES[0], None, run1_now, 7)
    assert ckpt1 == "2026-07-31 11:00:00"  # newest bucket strictly before 12:00

    run2_now = datetime(2026, 7, 31, 13, 0, 0, tzinfo=timezone.utc)
    events2, ckpt2 = collect_query(client, QUERIES[0], ckpt1, run2_now, 7)

    tuples1, tuples2 = _bucket_tuples(events1), _bucket_tuples(events2)
    overlap = tuples1 & tuples2
    assert not overlap, (
        "duplicate (bucket_start, api_key_id, model) tuples emitted by "
        "consecutive runs: {}".format(overlap)
    )
    # And the fix should still make forward progress: run 2 must pick up
    # the new 12:00 bucket that became complete between the two runs,
    # rather than emitting nothing.
    assert tuples2 == {("2026-07-31 12:00:00", "Alpaca", "m")}


def test_five_consecutive_collect_query_runs_never_repeat_a_bucket_tuple():
    # Same shape as the controller's actual five-run reproduction transcript
    # (run@19:05 .. run@23:05), one hour apart, checkpoint threaded forward
    # each time.
    #
    # Mirrors stream_events's checkpoint-holding pattern exactly
    # (`if next_ckpt: ckpt.update(ckpt_key, next_ckpt)`): a None return means
    # "hold what we had", not "reset to None". A run with nothing new to
    # collect (e.g. no bucket has completed since the last run) must not
    # blank out an already-advanced checkpoint.
    client = FilteringStubClient(FOUR_HOUR_ROWS_KEY_MODEL, "date__hour")
    seen = set()
    checkpoint = None
    now = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)
    for _ in range(5):
        events, next_ckpt = collect_query(client, QUERIES[0], checkpoint, now, 7)
        if next_ckpt:
            checkpoint = next_ckpt
        this_run = _bucket_tuples(events)
        assert not (seen & this_run), (
            "run re-emitted a bucket tuple already seen: {}".format(seen & this_run)
        )
        seen |= this_run
        now = now + timedelta(hours=1)

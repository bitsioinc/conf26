# TA_openrouter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `TA_openrouter`, a shippable Splunk add-on that ingests OpenRouter's analytics and key-management APIs and reports on model-mix spend, provider routing, and API key governance.

**Architecture:** UCC-framework add-on with two modular inputs. `openrouter_analytics` issues two `POST /analytics/query` calls per interval — one grouped by `[api_key_id, model]`, one by `[model, provider]` — each with its own KV Store checkpoint. `openrouter_keys` snapshots the key roster. A pure-HTTP client module and a pure-logic transform module (no Splunk imports) keep everything unit-testable without a Splunk runtime.

**Tech Stack:** Python 3.9 (Splunk app runtime), `splunk-add-on-ucc-framework` (`ucc-gen`), Splunk Packaging Toolkit (`slim`), `solnlib`, `splunklib`, FastAPI (mock server), pytest.

**Spec:** `docs/superpowers/specs/2026-07-31-openrouter-ta-design.md`

## Global Constraints

- **Python 3.9 compatible** in `TA_openrouter/package/bin/`. No f-strings, no PEP 604 unions (`int | None`), no `match`. Splunk's app runtime is CPython 3.9.25; the dev venv is 3.11 and will happily compile code that fails at runtime.
- **Nothing outside `TA_openrouter/` may be created or modified.** Not `scripts/build.sh`, not `tests/`, not `mockserver/`, not `TA_anthropic/`. `TA_anthropic` is AppInspect-clean at 58 passing tests and six weeks from `.conf2026`.
- **Test files are named `test_openrouter_*.py`.** Running `pytest` from the repo root collects both trees; the existing `tests/` already has `test_client.py`, `test_transform.py`, `test_alerts.py`, `test_mockserver.py`, `test_fixtures.py`. Duplicate basenames with no `__init__.py` raise *import file mismatch*.
- **Branch:** `openrouter-ta`. Already checked out.
- **Base URL:** `https://openrouter.ai/api/v1`
- **Auth header:** `Authorization: Bearer <management key>`
- **Money is USD.** No cents divisor. `TA_anthropic`'s `/100` is correct *there* and wrong here.
- **Additive metrics only in v0.1.0.** Never ingest a metric with `is_rate: true`.
- **Gates before done:** unit tests pass · `slim validate` 0 errors · AppInspect precert 0 errors 0 failures · no `__pycache__`/`.pyc`/`.DS_Store` in the artifact · every `package/bin/` module compiles under Python 3.9.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `TA_openrouter/package/bin/openrouter_client.py` | HTTP only. Bearer auth, `POST /analytics/query`, `GET /keys` offset paging, one retry on 429/5xx. No Splunk imports. |
| `TA_openrouter/package/bin/openrouter_transform.py` | Pure logic. Window computation, row flattening, checkpoint extraction. No Splunk imports, no network. |
| `TA_openrouter/package/bin/openrouter_analytics_helper.py` | Modular input. Two queries, two checkpoints, truncation handling, event writing. |
| `TA_openrouter/package/bin/openrouter_keys_helper.py` | Modular input. Key roster snapshot. |
| `TA_openrouter/globalConfig.json` | UCC config: account tab + two input services. |
| `TA_openrouter/package/default/props.conf` | Three sourcetypes, `KV_MODE=json`, `TZ=UTC`. |
| `TA_openrouter/package/default/macros.conf` | `openrouter_index` macro. |
| `TA_openrouter/package/default/savedsearches.conf` | Baseline builder + two alerts. |
| `TA_openrouter/package/default/data/ui/views/*.xml` | Three dashboards. |
| `TA_openrouter/package/lookups/openrouter_key_baseline.csv` | Headers only. |
| `TA_openrouter/mockserver/app.py` | FastAPI replay, query-driven responses. |
| `TA_openrouter/mockserver/fixtures/*.json` | Recorded from the live API in Task 1. |
| `TA_openrouter/scripts/record_fixtures.py` | Captures live responses into fixtures. |
| `TA_openrouter/scripts/build.sh` | `ucc-gen` → `slim validate` → `slim package`. |
| `TA_openrouter/tests/` | `conftest.py` + `test_openrouter_*.py`. |
| `TA_openrouter/docs/` | `architecture.md`, `operations.md`, `schema-verification.md`. |

---

### Task 1: Scaffold and record real fixtures

The spec flags every response schema as unverified, and `/analytics/query` returns **untyped rows** whose shape depends on the request. Guessing field names here would poison every downstream task. This task turns the live API into the fixture contract.

**Files:**
- Create: `TA_openrouter/scripts/record_fixtures.py`
- Create: `TA_openrouter/mockserver/fixtures/` (populated by the script)
- Create: `TA_openrouter/tests/conftest.py`
- Create: `TA_openrouter/docs/schema-verification.md`

**Interfaces:**
- Consumes: nothing
- Produces: `mockserver/fixtures/analytics_by_key_model.json`, `analytics_by_model_provider.json`, `keys.json`, `analytics_meta.json` — each the verbatim response body. `docs/schema-verification.md` records the observed field names.

- [ ] **Step 1: Create the directory skeleton**

```bash
mkdir -p TA_openrouter/package/bin \
         TA_openrouter/package/default/data/ui/views \
         TA_openrouter/package/default/data/ui/nav \
         TA_openrouter/package/lookups \
         TA_openrouter/mockserver/fixtures \
         TA_openrouter/tests \
         TA_openrouter/scripts \
         TA_openrouter/docs
```

- [ ] **Step 2: Write the fixture recorder**

Create `TA_openrouter/scripts/record_fixtures.py`:

```python
"""Record live OpenRouter API responses into mockserver fixtures.

Usage:
    OPENROUTER_MANAGEMENT_KEY=sk-or-v1-... python3 TA_openrouter/scripts/record_fixtures.py

Writes verbatim response bodies. The recorded shapes become the contract the
mock server and the transform module are written against.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://openrouter.ai/api/v1"
FIXTURES = Path(__file__).resolve().parent.parent / "mockserver" / "fixtures"

METRICS = [
    "request_count", "total_usage", "tokens_total", "tokens_prompt",
    "tokens_completion", "reasoning_tokens", "cached_tokens", "byok_usage",
]


def _call(path, key, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Authorization", "Bearer " + key)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main():
    key = os.environ.get("OPENROUTER_MANAGEMENT_KEY")
    if not key:
        sys.exit("OPENROUTER_MANAGEMENT_KEY is not set")
    FIXTURES.mkdir(parents=True, exist_ok=True)

    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    window = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    captures = {
        "analytics_meta.json": ("/analytics/meta", None),
        "keys.json": ("/keys?include_disabled=true", None),
        "analytics_by_key_model.json": ("/analytics/query", {
            "metrics": METRICS,
            "dimensions": ["api_key_id", "model"],
            "granularity": "hour",
            "time_range": window,
        }),
        "analytics_by_model_provider.json": ("/analytics/query", {
            "metrics": METRICS,
            "dimensions": ["model", "provider"],
            "granularity": "hour",
            "time_range": window,
        }),
    }

    for name, (path, body) in captures.items():
        payload = _call(path, key, body)
        (FIXTURES / name).write_text(json.dumps(payload, indent=2) + "\n")
        print("wrote", name)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run it against the live API**

```bash
OPENROUTER_MANAGEMENT_KEY='sk-or-v1-...' python3 TA_openrouter/scripts/record_fixtures.py
```

Expected: four files written under `TA_openrouter/mockserver/fixtures/`.

- [ ] **Step 4: Record the observed schema**

```bash
python3 -c "
import json
d=json.load(open('TA_openrouter/mockserver/fixtures/analytics_by_key_model.json'))
rows=d['data']['data']
print('row count:', len(rows))
print('metadata:', d['data']['metadata'])
print('keys on row 0:', sorted(rows[0].keys()) if rows else 'NO ROWS')
"
```

Create `TA_openrouter/docs/schema-verification.md` recording, verbatim: the time-bucket column name, the dimension column names, the metric column names, and the `metadata` keys.

**If `row count` is 0** (the account has little usage), the field names cannot be observed. Set the time bucket column to `date` — the OpenAPI `order_by.field` description names `"date"` explicitly as an orderable field alongside metrics and dimensions — record that as UNVERIFIED in `schema-verification.md`, and hand-author the fixtures in Task 5 using that shape.

- [ ] **Step 5: Write the test conftest**

Create `TA_openrouter/tests/conftest.py`:

```python
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))                       # mockserver package
sys.path.insert(0, str(ROOT / "package" / "bin"))   # add-on modules
```

- [ ] **Step 6: Verify nothing outside TA_openrouter changed**

```bash
git status --porcelain | grep -v 'TA_openrouter/' | grep -v 'HANDOFF.md' | grep -v 'splunk/.env.example'
```

Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add TA_openrouter/
git commit -m "feat(openrouter): scaffold TA and record live API fixtures"
```

---

### Task 2: Window computation

**Files:**
- Create: `TA_openrouter/package/bin/openrouter_transform.py`
- Create: `TA_openrouter/tests/test_openrouter_transform.py`

**Interfaces:**
- Consumes: nothing
- Produces: `compute_window(checkpoint, now, backfill_days) -> tuple or None` returning `(start, end)` as RFC 3339 strings; module constants `RFC3339`, `MAX_BACKFILL_DAYS = 30`

- [ ] **Step 1: Write the failing tests**

Create `TA_openrouter/tests/test_openrouter_transform.py`:

```python
from datetime import datetime, timezone

from openrouter_transform import compute_window, MAX_BACKFILL_DAYS

NOW = datetime(2026, 7, 31, 12, 34, 56, tzinfo=timezone.utc)


def test_first_run_backfills_and_snaps_to_hour():
    start, end = compute_window(None, NOW, backfill_days=7)
    assert start == "2026-07-24T12:00:00Z"
    assert end == "2026-07-31T12:00:00Z"


def test_resumes_from_checkpoint():
    start, end = compute_window("2026-07-31T10:00:00Z", NOW, backfill_days=7)
    assert start == "2026-07-31T10:00:00Z"
    assert end == "2026-07-31T12:00:00Z"


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_transform.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openrouter_transform'`

- [ ] **Step 3: Write the implementation**

Create `TA_openrouter/package/bin/openrouter_transform.py`:

```python
"""Window and event-shaping logic for the OpenRouter management API.

No Splunk imports and no network access, so this stays unit testable.
Python 3.9 compatible: Splunk's app runtime is CPython 3.9.25.

Money is USD. Unlike the Anthropic Admin API there is no cents divisor --
every currency metric is already in dollars.
"""
from datetime import datetime, timedelta, timezone

RFC3339 = "%Y-%m-%dT%H:%M:%SZ"

#: /activity is capped at 30 completed UTC days. /analytics/query states no
#: limit, but it is unmeasured, so we clamp conservatively to the known figure.
MAX_BACKFILL_DAYS = 30


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fmt(dt):
    return dt.strftime(RFC3339)


def _snap_hour(dt):
    return dt.replace(minute=0, second=0, microsecond=0)


def _int_or(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def compute_window(checkpoint, now, backfill_days):
    """Return (start, end) RFC 3339 strings, or None.

    ``end`` snaps down to the last complete hour, so a partially elapsed hour
    is never collected -- re-running inside the same hour correctly yields
    nothing. ``start`` resumes from ``checkpoint`` when present, else backfills
    ``backfill_days`` clamped to MAX_BACKFILL_DAYS.
    """
    ending = _snap_hour(now.astimezone(timezone.utc))
    if checkpoint:
        starting = _parse(checkpoint)
    else:
        days = min(_int_or(backfill_days, MAX_BACKFILL_DAYS), MAX_BACKFILL_DAYS)
        starting = ending - timedelta(days=days)
    if starting >= ending:
        return None
    return _fmt(starting), _fmt(ending)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_transform.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add TA_openrouter/package/bin/openrouter_transform.py TA_openrouter/tests/test_openrouter_transform.py
git commit -m "feat(openrouter): add compute_window with complete-hour and retention clamp"
```

---

### Task 3: Row flattening

`/analytics/query` returns untyped rows shaped by the request, so flattening is parameterised by the dimensions actually sent rather than a hardcoded schema.

**Files:**
- Modify: `TA_openrouter/package/bin/openrouter_transform.py`
- Modify: `TA_openrouter/tests/test_openrouter_transform.py`

**Interfaces:**
- Consumes: `_parse`, `_fmt`, `RFC3339` from Task 2
- Produces:
  - `flatten_analytics(payload, dimensions, time_field="date") -> list` of `{"time": float, "data": dict}`
  - `flatten_keys(keys, snapshot_at) -> list` of `{"time": float, "data": dict}`
  - `max_bucket(payload, time_field="date") -> str or None`
  - `is_truncated(payload) -> bool`
  - Constants `CURRENCY_METRICS`, `KEY_CURRENCY_FIELDS`

- [ ] **Step 1: Write the failing tests**

Append to `TA_openrouter/tests/test_openrouter_transform.py`:

```python
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_transform.py -v`
Expected: FAIL — `ImportError: cannot import name 'flatten_analytics'`

- [ ] **Step 3: Write the implementation**

Append to `TA_openrouter/package/bin/openrouter_transform.py`:

```python
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


def max_bucket(payload, time_field="date"):
    """Newest time bucket across rows -- the next checkpoint value."""
    stamps = [r[time_field] for r in _rows(payload) if r.get(time_field)]
    return max(stamps) if stamps else None


def _suffix_currency(body, fields):
    for name in fields:
        if name in body:
            body[name + "_usd"] = body.pop(name)


def flatten_analytics(payload, dimensions, time_field="date"):
    """One event per analytics row; event time is the bucket start.

    Rows are untyped and shaped by the request, so nothing is assumed beyond
    the presence of ``time_field``. ``dimensions`` is recorded on the event so
    downstream searches can tell the two queries apart.
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_transform.py -v`
Expected: 16 passed

- [ ] **Step 5: Verify Python 3.9 compatibility**

Run: `/opt/splunk/bin/python3 -m py_compile TA_openrouter/package/bin/openrouter_transform.py && echo OK`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add TA_openrouter/package/bin/openrouter_transform.py TA_openrouter/tests/test_openrouter_transform.py
git commit -m "feat(openrouter): add query-driven row flattening and truncation detection"
```

---

### Task 4: HTTP client

**Files:**
- Create: `TA_openrouter/package/bin/openrouter_client.py`
- Create: `TA_openrouter/tests/test_openrouter_client.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `OpenRouterAPIError(Exception)` with `.status_code`, `.message`
  - `OpenRouterClient(api_key, base_url="https://openrouter.ai/api/v1", session=None, timeout=30, retry_backoff=2)`
  - `.query_analytics(metrics, dimensions, granularity, start, end, limit=None) -> dict` (full response body)
  - `.list_keys(include_disabled=True) -> list`
  - Constants `ANALYTICS_QUERY_PATH`, `KEYS_PATH`, `KEYS_PAGE_LIMIT`, `DEFAULT_ROW_LIMIT`, `DEFAULT_BASE_URL`

- [ ] **Step 1: Write the failing tests**

Create `TA_openrouter/tests/test_openrouter_client.py`:

```python
import pytest

from openrouter_client import OpenRouterAPIError, OpenRouterClient


class FakeResponse(object):
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class FakeSession(object):
    """Records calls and replays queued responses."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def _next(self):
        return self._responses.pop(0)

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(("GET", url, headers, params, None))
        return self._next()

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append(("POST", url, headers, None, json))
        return self._next()


def test_sends_bearer_authorization_header():
    session = FakeSession([FakeResponse(200, {"data": {"data": []}})])
    client = OpenRouterClient("sk-or-v1-secret", session=session, retry_backoff=0)
    client.query_analytics(["total_usage"], ["model"], "hour",
                           "2026-07-31T10:00:00Z", "2026-07-31T12:00:00Z")
    _, _, headers, _, _ = session.calls[0]
    assert headers["Authorization"] == "Bearer sk-or-v1-secret"
    assert "x-api-key" not in headers


def test_analytics_query_builds_expected_body():
    session = FakeSession([FakeResponse(200, {"data": {"data": []}})])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    client.query_analytics(["total_usage", "request_count"],
                           ["api_key_id", "model"], "hour",
                           "2026-07-31T10:00:00Z", "2026-07-31T12:00:00Z")
    method, url, _, _, body = session.calls[0]
    assert method == "POST"
    assert url.endswith("/analytics/query")
    assert body["metrics"] == ["total_usage", "request_count"]
    assert body["dimensions"] == ["api_key_id", "model"]
    assert body["granularity"] == "hour"
    assert body["time_range"] == {"start": "2026-07-31T10:00:00Z",
                                  "end": "2026-07-31T12:00:00Z"}


def test_analytics_query_sets_explicit_row_limit():
    # The server default is 1000; an hourly window across keys and models
    # overflows that easily, so we always ask for more.
    session = FakeSession([FakeResponse(200, {"data": {"data": []}})])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    client.query_analytics(["total_usage"], ["model"], "hour", "a", "b")
    _, _, _, _, body = session.calls[0]
    assert body["limit"] > 1000


def test_retries_once_on_429_then_succeeds():
    session = FakeSession([
        FakeResponse(429, text="slow down"),
        FakeResponse(200, {"data": {"data": [{"x": 1}]}}),
    ])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    out = client.query_analytics(["total_usage"], ["model"], "hour", "a", "b")
    assert out["data"]["data"] == [{"x": 1}]
    assert len(session.calls) == 2


def test_raises_after_retry_still_failing():
    session = FakeSession([FakeResponse(500, text="boom"),
                           FakeResponse(500, text="boom")])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    with pytest.raises(OpenRouterAPIError) as exc:
        client.query_analytics(["total_usage"], ["model"], "hour", "a", "b")
    assert exc.value.status_code == 500


def test_401_does_not_retry():
    # A wrong key type is a permanent failure; retrying wastes an interval.
    session = FakeSession([FakeResponse(401, text="unauthorized")])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    with pytest.raises(OpenRouterAPIError) as exc:
        client.query_analytics(["total_usage"], ["model"], "hour", "a", "b")
    assert exc.value.status_code == 401
    assert len(session.calls) == 1


def test_list_keys_pages_by_offset():
    page1 = {"data": [{"hash": "a"}] * 100}
    page2 = {"data": [{"hash": "b"}]}
    session = FakeSession([FakeResponse(200, page1), FakeResponse(200, page2)])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    keys = client.list_keys()
    assert len(keys) == 101
    assert session.calls[0][3]["offset"] == 0
    assert session.calls[1][3]["offset"] == 100


def test_list_keys_stops_on_short_page():
    session = FakeSession([FakeResponse(200, {"data": [{"hash": "a"}]})])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    assert len(client.list_keys()) == 1
    assert len(session.calls) == 1


def test_list_keys_includes_disabled_by_default():
    session = FakeSession([FakeResponse(200, {"data": []})])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    client.list_keys()
    assert session.calls[0][3]["include_disabled"] == "true"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openrouter_client'`

- [ ] **Step 3: Write the implementation**

Create `TA_openrouter/package/bin/openrouter_client.py`:

```python
"""Pure REST client for the OpenRouter management API. No Splunk imports.

Dependency-light (only ``requests``, imported lazily) so it unit tests without
a Splunk runtime. Python 3.9 compatible: Splunk's app runtime is CPython
3.9.25.

Two things differ from the Anthropic client and are easy to get wrong:

* Auth is ``Authorization: Bearer``, not ``x-api-key``. Management keys carry
  the same ``sk-or-v1-`` prefix as ordinary inference keys, so a 401 may mean
  "right string, wrong key type" rather than a typo.
* Analytics is a POST with a query body, and the response row shape depends on
  what was asked for. The client returns the body verbatim; interpreting it is
  openrouter_transform's job.
"""
import time

USER_AGENT = "TA_openrouter/0.1.0 (Splunk add-on)"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

ANALYTICS_QUERY_PATH = "/analytics/query"
KEYS_PATH = "/keys"

KEYS_PAGE_LIMIT = 100

#: The server defaults to 1000 rows. An hour x key x model grid crosses that
#: with only a handful of keys and models, and an overflow is reported as
#: metadata.truncated rather than an error, so ask for considerably more.
DEFAULT_ROW_LIMIT = 50000


class OpenRouterAPIError(Exception):
    """Raised when the management API returns a non-200 we cannot recover."""

    def __init__(self, status_code, message):
        self.status_code = status_code
        self.message = message
        super(OpenRouterAPIError, self).__init__(
            "OpenRouter API error {}: {}".format(status_code, message)
        )


class OpenRouterClient(object):
    """Analytics queries plus the API key roster."""

    def __init__(self, api_key, base_url=DEFAULT_BASE_URL, session=None,
                 timeout=30, retry_backoff=2):
        if session is None:
            import requests
            session = requests.Session()
        self._session = session
        self._timeout = timeout
        self._retry_backoff = retry_backoff
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": "Bearer {}".format(api_key),
            "User-Agent": USER_AGENT,
        }

    # -- transport ---------------------------------------------------------

    def _retryable(self, status_code):
        return status_code == 429 or status_code >= 500

    def _request(self, method, path, params=None, body=None):
        url = "{}{}".format(self._base_url, path)

        def send():
            if method == "POST":
                return self._session.post(url, headers=self._headers,
                                          json=body, timeout=self._timeout)
            return self._session.get(url, headers=self._headers,
                                     params=params, timeout=self._timeout)

        resp = send()
        if self._retryable(resp.status_code):
            # Exactly one retry, then give up. 4xx other than 429 is permanent
            # (a wrong key type will never succeed) so it is not retried.
            if self._retry_backoff:
                time.sleep(self._retry_backoff)
            resp = send()
        if resp.status_code != 200:
            raise OpenRouterAPIError(resp.status_code, resp.text[:500])
        return resp.json()

    # -- analytics ---------------------------------------------------------

    def query_analytics(self, metrics, dimensions, granularity, start, end,
                        limit=None):
        """POST /analytics/query. Returns the response body verbatim."""
        body = {
            "metrics": list(metrics),
            "dimensions": list(dimensions),
            "granularity": granularity,
            "time_range": {"start": start, "end": end},
            "limit": limit or DEFAULT_ROW_LIMIT,
        }
        return self._request("POST", ANALYTICS_QUERY_PATH, body=body)

    # -- keys (offset pagination) ------------------------------------------

    def list_keys(self, include_disabled=True):
        """GET /keys, following offset pagination to exhaustion.

        Only the default workspace is returned; multi-workspace enumeration
        via workspace_id is out of scope for v0.1.0.
        """
        items = []
        offset = 0
        while True:
            params = {
                "offset": offset,
                "include_disabled": "true" if include_disabled else "false",
            }
            page = self._request("GET", KEYS_PATH, params=params)
            batch = page.get("data") or []
            items.extend(batch)
            if len(batch) < KEYS_PAGE_LIMIT:
                return items
            offset += len(batch)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_client.py -v`
Expected: 9 passed

- [ ] **Step 5: Verify Python 3.9 compatibility**

Run: `/opt/splunk/bin/python3 -m py_compile TA_openrouter/package/bin/openrouter_client.py && echo OK`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add TA_openrouter/package/bin/openrouter_client.py TA_openrouter/tests/test_openrouter_client.py
git commit -m "feat(openrouter): add Bearer-auth client with analytics query and offset key paging"
```

---

### Task 5: Mock server

**Files:**
- Create: `TA_openrouter/mockserver/__init__.py`
- Create: `TA_openrouter/mockserver/app.py`
- Create: `TA_openrouter/tests/test_openrouter_mockserver.py`

**Interfaces:**
- Consumes: fixtures from Task 1
- Produces: FastAPI `app` serving `POST /api/v1/analytics/query` and `GET /api/v1/keys`

- [ ] **Step 1: Write the failing tests**

Create `TA_openrouter/tests/test_openrouter_mockserver.py`:

```python
from fastapi.testclient import TestClient

from mockserver.app import app

client = TestClient(app)
AUTH = {"Authorization": "Bearer sk-or-v1-anything"}

QUERY = {
    "metrics": ["total_usage", "request_count"],
    "dimensions": ["api_key_id", "model"],
    "granularity": "hour",
    "time_range": {"start": "2000-01-01T00:00:00Z", "end": "2100-01-01T00:00:00Z"},
    "limit": 50000,
}


def test_analytics_requires_authorization_header():
    resp = client.post("/api/v1/analytics/query", json=QUERY)
    assert resp.status_code == 401


def test_analytics_returns_rows_for_requested_dimensions():
    resp = client.post("/api/v1/analytics/query", json=QUERY, headers=AUTH)
    assert resp.status_code == 200
    rows = resp.json()["data"]["data"]
    assert rows
    for row in rows:
        assert "api_key_id" in row
        assert "model" in row


def test_analytics_switches_shape_on_provider_dimensions():
    body = dict(QUERY, dimensions=["model", "provider"])
    rows = client.post("/api/v1/analytics/query", json=body,
                       headers=AUTH).json()["data"]["data"]
    assert rows
    for row in rows:
        assert "provider" in row
        assert "api_key_id" not in row


def test_analytics_honours_time_range():
    body = dict(QUERY, time_range={"start": "2000-01-01T00:00:00Z",
                                   "end": "2000-01-02T00:00:00Z"})
    rows = client.post("/api/v1/analytics/query", json=body,
                       headers=AUTH).json()["data"]["data"]
    assert rows == []


def test_analytics_reports_truncation_when_limit_is_low():
    body = dict(QUERY, limit=1)
    payload = client.post("/api/v1/analytics/query", json=body,
                          headers=AUTH).json()
    assert payload["data"]["metadata"]["truncated"] is True
    assert len(payload["data"]["data"]) == 1


def test_analytics_not_truncated_at_full_limit():
    payload = client.post("/api/v1/analytics/query", json=QUERY,
                          headers=AUTH).json()
    assert payload["data"]["metadata"]["truncated"] is False


def test_keys_requires_authorization_header():
    assert client.get("/api/v1/keys").status_code == 401


def test_keys_returns_roster():
    data = client.get("/api/v1/keys", headers=AUTH).json()["data"]
    assert data
    assert "hash" in data[0]
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_mockserver.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mockserver'`

- [ ] **Step 3: Write the implementation**

Create `TA_openrouter/mockserver/__init__.py` (empty), then `TA_openrouter/mockserver/app.py`:

```python
"""Local replay of the OpenRouter management API for offline development.

Two deliberate properties:

1. **Anchored to the current UTC hour, not to import time.** Fixture buckets
   are shifted so the newest lands on the hour the process started in. Because
   the anchor is an hour boundary rather than an import instant, restarting the
   mock inside the same hour regenerates identical buckets -- so a restart
   cannot mint a second set of timestamps that slip past a consumer's
   checkpoint. (TA_anthropic's mock anchors at import and has exactly that
   hazard, documented in HANDOFF.md as the double-ingest trap.)

2. **Responses are query-driven.** The real endpoint's row shape depends on the
   requested dimensions, so the mock selects a fixture per dimension set and
   honours time_range and limit. Requesting a low limit sets
   metadata.truncated, which is how the truncation path gets tested.
"""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

FIXTURES = Path(__file__).parent / "fixtures"
app = FastAPI(title="OpenRouter management API (mock)")

BY_KEY_MODEL = "analytics_by_key_model.json"
BY_MODEL_PROVIDER = "analytics_by_model_provider.json"
KEYS = "keys.json"

RFC3339 = "%Y-%m-%dT%H:%M:%SZ"
TIME_FIELD = "date"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fmt(dt):
    return dt.strftime(RFC3339)


def _rows(payload):
    return payload["data"]["data"]


def _anchor(payload):
    """Shift buckets so the newest lands on the current UTC hour."""
    rows = _rows(payload)
    if not rows:
        return payload
    newest = max(_parse(r[TIME_FIELD]) for r in rows)
    top_of_hour = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0)
    delta = top_of_hour - newest
    shifted = copy.deepcopy(payload)
    for row in _rows(shifted):
        row[TIME_FIELD] = _fmt(_parse(row[TIME_FIELD]) + delta)
    return shifted


#: Frozen at import, but anchored to an hour boundary -- see the docstring.
ANALYTICS = {
    BY_KEY_MODEL: _anchor(_load(BY_KEY_MODEL)),
    BY_MODEL_PROVIDER: _anchor(_load(BY_MODEL_PROVIDER)),
}
KEY_ROSTER = _load(KEYS)


def _require_auth(authorization):
    if not authorization:
        raise HTTPException(status_code=401,
                            detail={"error": "missing Authorization header"})


@app.post("/api/v1/analytics/query")
async def analytics_query(request: Request,
                          authorization: str = Header(default=None)):
    _require_auth(authorization)
    body = await request.json()
    dimensions = body.get("dimensions") or []
    fixture = BY_MODEL_PROVIDER if "provider" in dimensions else BY_KEY_MODEL

    window = body.get("time_range") or {}
    start = _parse(window["start"]) if window.get("start") else None
    end = _parse(window["end"]) if window.get("end") else None

    rows = []
    for row in _rows(ANALYTICS[fixture]):
        stamp = _parse(row[TIME_FIELD])
        if start is not None and stamp < start:
            continue
        if end is not None and stamp >= end:
            continue
        rows.append(dict(row))

    limit = int(body.get("limit") or 1000)
    truncated = len(rows) > limit
    rows = rows[:limit]

    return {
        "data": {
            "data": rows,
            "metadata": {"query_time_ms": 1, "row_count": len(rows),
                         "truncated": truncated},
            "warnings": [],
        }
    }


@app.get("/api/v1/keys")
def list_keys(authorization: str = Header(default=None)):
    _require_auth(authorization)
    return KEY_ROSTER
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_mockserver.py -v`
Expected: 8 passed

**If `test_analytics_returns_rows_for_requested_dimensions` fails with an empty row list**, the fixtures recorded in Task 1 had no data. Hand-author both analytics fixtures now using the shape in `docs/schema-verification.md`, with at least: three distinct `api_key_id` values, four models, two providers serving one shared model at different `total_usage` per request, seven or more hourly buckets, and a spend spike on the third key in the newest bucket.

- [ ] **Step 5: Commit**

```bash
git add TA_openrouter/mockserver/ TA_openrouter/tests/test_openrouter_mockserver.py
git commit -m "feat(openrouter): add query-driven mock server anchored to the UTC hour"
```

---

### Task 6: Fixture integrity tests

The fixtures encode the demo scenario. If they drift, the dashboards render correctly and show nothing — a failure mode no other test catches.

**Files:**
- Create: `TA_openrouter/tests/test_openrouter_fixtures.py`
- Modify: `TA_openrouter/mockserver/fixtures/*.json` (only if assertions fail)

**Interfaces:**
- Consumes: fixtures from Task 1
- Produces: nothing consumed by later tasks

- [ ] **Step 1: Write the tests**

Create `TA_openrouter/tests/test_openrouter_fixtures.py`:

```python
import json
from collections import defaultdict
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "mockserver" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def rows(name):
    return load(name)["data"]["data"]


def test_key_roster_has_at_least_three_keys():
    data = load("keys.json")["data"]
    assert len(data) >= 3, "need two sanctioned keys plus one unrecognised"
    assert all("hash" in k for k in data)


def test_attribution_fixture_has_multiple_keys_and_models():
    data = rows("analytics_by_key_model.json")
    assert len({r["api_key_id"] for r in data}) >= 3
    assert len({r["model"] for r in data}) >= 2


def test_one_key_is_absent_from_the_roster():
    # The shadow-AI reveal needs a key that appears in analytics but not in
    # the roster the baseline search will be built from.
    roster = {k["hash"] for k in load("keys.json")["data"]}
    seen = {r["api_key_id"] for r in rows("analytics_by_key_model.json")}
    assert seen - roster, "no unrecognised key in the analytics fixture"


def test_provider_fixture_has_one_model_on_two_providers():
    # Without this the Provider Routing dashboard renders but demonstrates
    # nothing -- the spend-per-request comparison has no signal.
    by_model = defaultdict(set)
    for row in rows("analytics_by_model_provider.json"):
        by_model[row["model"]].add(row["provider"])
    shared = [m for m, ps in by_model.items() if len(ps) >= 2]
    assert shared, "no model served by two providers"


def test_provider_costs_differ_for_the_shared_model():
    usage = defaultdict(float)
    counts = defaultdict(int)
    for row in rows("analytics_by_model_provider.json"):
        combo = (row["model"], row["provider"])
        usage[combo] += row["total_usage"]
        counts[combo] += row["request_count"]
    per_request = defaultdict(list)
    for combo, spend in usage.items():
        if counts[combo]:
            per_request[combo[0]].append(round(spend / counts[combo], 8))
    assert any(len(set(v)) > 1 for v in per_request.values()), \
        "every provider costs the same; the comparison panel has no signal"


def test_analytics_fixtures_span_enough_buckets_for_the_alert():
    # streamstats window=6 needs a reference set before the spike.
    for name in ("analytics_by_key_model.json", "analytics_by_model_provider.json"):
        assert len({r["date"] for r in rows(name)}) >= 7, name
```

- [ ] **Step 2: Run the tests**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_fixtures.py -v`

- [ ] **Step 3: Repair any failing fixture**

For each failure, edit the JSON fixture to satisfy the assertion. Do **not** weaken an assertion to make it pass — each encodes a property the dashboards depend on. Re-run until green, then re-run Task 5's mock tests to confirm they still pass.

- [ ] **Step 4: Commit**

```bash
git add TA_openrouter/tests/test_openrouter_fixtures.py TA_openrouter/mockserver/fixtures/
git commit -m "test(openrouter): assert fixtures encode the demo scenario"
```

---

### Task 7: UCC globalConfig

**Files:**
- Create: `TA_openrouter/globalConfig.json`

**Interfaces:**
- Consumes: nothing
- Produces: input types `openrouter_analytics` and `openrouter_keys`; account conf `ta_openrouter_account` with fields `name`, `api_key`, `api_base_url`; settings conf `ta_openrouter_settings`

- [ ] **Step 1: Write globalConfig.json**

Create `TA_openrouter/globalConfig.json`:

```json
{
  "meta": {
    "name": "TA_openrouter",
    "restRoot": "TA_openrouter",
    "version": "0.1.0",
    "displayName": "OpenRouter Add-on for Splunk",
    "schemaVersion": "0.0.10",
    "supportedThemes": ["light", "dark"]
  },
  "pages": {
    "configuration": {
      "title": "Configuration",
      "description": "Configure your OpenRouter management credential",
      "tabs": [
        {
          "name": "account",
          "title": "Account",
          "table": {
            "header": [
              {"field": "name", "label": "Name"},
              {"field": "api_base_url", "label": "Base URL"}
            ],
            "actions": ["edit", "delete", "clone"]
          },
          "entity": [
            {
              "type": "text",
              "label": "Name",
              "field": "name",
              "help": "Unique name for this account",
              "required": true,
              "validators": [
                {"type": "regex", "pattern": "^[a-zA-Z]\\w*$",
                 "errorMsg": "Name must start with a letter and contain only letters, numbers and underscores"},
                {"type": "string", "minLength": 1, "maxLength": 50,
                 "errorMsg": "Length of Name should be between 1 and 50"}
              ]
            },
            {
              "type": "text",
              "label": "Management API Key",
              "field": "api_key",
              "encrypted": true,
              "required": true,
              "help": "Create one at https://openrouter.ai/settings/management-keys. Management keys share the sk-or-v1- prefix with ordinary inference keys, so verify you copied it from the management-keys page."
            },
            {
              "type": "text",
              "label": "API Base URL",
              "field": "api_base_url",
              "required": true,
              "defaultValue": "https://openrouter.ai/api/v1",
              "help": "Point at a local mock server for offline testing"
            }
          ]
        },
        {
          "name": "logging",
          "title": "Logging",
          "entity": [
            {
              "type": "singleSelect",
              "label": "Log level",
              "field": "loglevel",
              "defaultValue": "INFO",
              "options": {
                "disableSearch": true,
                "autoCompleteFields": [
                  {"value": "DEBUG", "label": "DEBUG"},
                  {"value": "INFO", "label": "INFO"},
                  {"value": "WARNING", "label": "WARNING"},
                  {"value": "ERROR", "label": "ERROR"},
                  {"value": "CRITICAL", "label": "CRITICAL"}
                ]
              }
            }
          ]
        }
      ]
    },
    "inputs": {
      "title": "Inputs",
      "description": "Collect OpenRouter analytics and API key inventory",
      "table": {
        "header": [
          {"field": "name", "label": "Name"},
          {"field": "interval", "label": "Interval"},
          {"field": "index", "label": "Index"},
          {"field": "disabled", "label": "Status"}
        ],
        "moreInfo": [
          {"field": "name", "label": "Name"},
          {"field": "interval", "label": "Interval"},
          {"field": "index", "label": "Index"},
          {"field": "account", "label": "Account"}
        ],
        "actions": ["edit", "enable", "delete", "clone"]
      },
      "services": [
        {
          "name": "openrouter_analytics",
          "title": "Analytics",
          "entity": [
            {"type": "text", "label": "Name", "field": "name", "required": true,
             "validators": [{"type": "regex", "pattern": "^[a-zA-Z]\\w*$",
                             "errorMsg": "Name must start with a letter and contain only letters, numbers and underscores"}]},
            {"type": "interval", "label": "Interval", "field": "interval",
             "defaultValue": "3600", "required": true,
             "help": "Seconds between collections. Analytics buckets are hourly."},
            {"type": "singleSelect", "label": "Index", "field": "index",
             "defaultValue": "default", "required": true,
             "options": {"endpointUrl": "data/indexes", "createSearchChoice": true}},
            {"type": "singleSelect", "label": "Account", "field": "account",
             "required": true,
             "options": {"referenceName": "account"}},
            {"type": "text", "label": "Backfill days", "field": "backfill_days",
             "defaultValue": "7", "required": false,
             "help": "First run only. Clamped to 30 days."}
          ]
        },
        {
          "name": "openrouter_keys",
          "title": "API Keys",
          "entity": [
            {"type": "text", "label": "Name", "field": "name", "required": true,
             "validators": [{"type": "regex", "pattern": "^[a-zA-Z]\\w*$",
                             "errorMsg": "Name must start with a letter and contain only letters, numbers and underscores"}]},
            {"type": "interval", "label": "Interval", "field": "interval",
             "defaultValue": "3600", "required": true},
            {"type": "singleSelect", "label": "Index", "field": "index",
             "defaultValue": "default", "required": true,
             "options": {"endpointUrl": "data/indexes", "createSearchChoice": true}},
            {"type": "singleSelect", "label": "Account", "field": "account",
             "required": true,
             "options": {"referenceName": "account"}}
          ]
        }
      ]
    }
  }
}
```

- [ ] **Step 2: Verify it parses and names the services correctly**

```bash
python3 -c "
import json
d=json.load(open('TA_openrouter/globalConfig.json'))
svc=[s['name'] for s in d['pages']['inputs']['services']]
assert svc == ['openrouter_analytics','openrouter_keys'], svc
assert d['meta']['name']=='TA_openrouter'
print('OK', svc)
"
```

Expected: `OK ['openrouter_analytics', 'openrouter_keys']`

- [ ] **Step 3: Commit**

```bash
git add TA_openrouter/globalConfig.json
git commit -m "feat(openrouter): add UCC globalConfig with account and two inputs"
```

---

### Task 8: Analytics modular input

The truncation rule is the highest-consequence logic in the add-on: advancing a checkpoint past a truncated response loses rows permanently and silently.

**Files:**
- Create: `TA_openrouter/package/bin/openrouter_analytics_helper.py`
- Create: `TA_openrouter/tests/test_openrouter_analytics_helper.py`

**Interfaces:**
- Consumes: `OpenRouterClient.query_analytics` (Task 4); `compute_window`, `flatten_analytics`, `max_bucket`, `is_truncated` (Tasks 2-3)
- Produces: `QUERIES` (tuple of dicts with keys `dimensions`, `metrics`, `sourcetype`, `checkpoint_suffix`), `METRICS`, `collect_query(client, query, checkpoint, now, backfill_days) -> (events, next_checkpoint)`, plus UCC `validate_input` / `stream_events`

- [ ] **Step 1: Write the failing tests**

Create `TA_openrouter/tests/test_openrouter_analytics_helper.py`:

```python
from datetime import datetime, timezone

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


ROWS = [
    {"date": "2026-07-31T10:00:00Z", "api_key_id": "a", "model": "m",
     "total_usage": 1.0, "request_count": 2},
    {"date": "2026-07-31T11:00:00Z", "api_key_id": "b", "model": "m",
     "total_usage": 2.0, "request_count": 3},
]


def test_two_queries_are_defined_with_distinct_checkpoints():
    assert len(QUERIES) == 2
    suffixes = [q["checkpoint_suffix"] for q in QUERIES]
    assert len(set(suffixes)) == 2
    assert [q["sourcetype"] for q in QUERIES] == [
        "openrouter:analytics", "openrouter:providers"]


def test_queries_use_only_additive_metrics():
    # Rate metrics (is_rate true) cannot be summed across buckets.
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
    client = StubClient(payload(ROWS))
    events, checkpoint = collect_query(client, QUERIES[0], None, NOW, 7)
    assert len(events) == 2
    assert checkpoint == "2026-07-31T11:00:00Z"


def test_collect_does_not_advance_checkpoint_when_truncated():
    # Advancing past a truncated response drops the missing rows forever.
    client = StubClient(payload(ROWS, truncated=True))
    events, checkpoint = collect_query(client, QUERIES[0], "2026-07-31T09:00:00Z",
                                       NOW, 7)
    assert checkpoint is None, "must not advance past a truncated window"
    assert events == [], "must not emit a partial window"


def test_collect_makes_no_call_when_window_is_empty():
    client = StubClient(payload([]))
    events, checkpoint = collect_query(client, QUERIES[0], "2026-07-31T12:00:00Z",
                                       NOW, 7)
    assert events == []
    assert checkpoint is None
    assert client.calls == [], "no API call when there is no complete bucket"


def test_collect_holds_checkpoint_when_window_has_no_rows():
    client = StubClient(payload([]))
    events, checkpoint = collect_query(client, QUERIES[0], "2026-07-31T10:00:00Z",
                                       NOW, 7)
    assert events == []
    assert checkpoint is None


def test_collect_passes_window_to_client():
    client = StubClient(payload(ROWS))
    collect_query(client, QUERIES[0], "2026-07-31T09:00:00Z", NOW, 7)
    _, _, granularity, start, end = client.calls[0]
    assert granularity == "hour"
    assert start == "2026-07-31T09:00:00Z"
    assert end == "2026-07-31T12:00:00Z"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_analytics_helper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openrouter_analytics_helper'`

- [ ] **Step 3: Write the implementation**

Create `TA_openrouter/package/bin/openrouter_analytics_helper.py`:

```python
"""Modular input helper for the ``openrouter_analytics`` input.

Issues two POST /analytics/query calls per run -- the dimension cap of 2
prevents combining them -- and writes each to its own sourcetype under its own
KV Store checkpoint.

Separate checkpoints matter because truncation is per-response. A shared
checkpoint would either stall the healthy query when the other truncates, or
force it to re-ingest a window it already emitted.

Python 3.9 compatible: Splunk's app runtime is CPython 3.9.25 (no f-strings,
no PEP 604 unions, no match statements).
"""
try:
    import import_declare_test  # noqa: F401  isort:skip

    from solnlib import conf_manager, log
    from solnlib.modular_input import checkpointer
    from splunklib import modularinput as smi
except ImportError:  # unit tests run without the Splunk runtime
    conf_manager = log = checkpointer = smi = None

import json
from datetime import datetime, timezone

from openrouter_client import OpenRouterClient
from openrouter_transform import (
    compute_window, flatten_analytics, is_truncated, max_bucket,
)

ADDON_NAME = "TA_openrouter"
SETTINGS_CONF = "ta_openrouter_settings"
ACCOUNT_CONF = "ta_openrouter_account"
CHECKPOINT_COLLECTION = "TA_openrouter_checkpoints"
INPUT_TYPE = "openrouter_analytics"
GRANULARITY = "hour"
DEFAULT_BACKFILL_DAYS = 7
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"

#: Additive metrics only. Every metric carrying is_rate=true in
#: /analytics/meta is excluded, because summing a rate across time buckets or
#: dimension values produces a meaningless number and Splunk will do it
#: silently.
METRICS = [
    "request_count",
    "total_usage",
    "tokens_total",
    "tokens_prompt",
    "tokens_completion",
    "reasoning_tokens",
    "cached_tokens",
    "byok_usage",
]

#: Two queries because `dimensions` caps at 2 and all three of api_key_id,
#: model, and provider are needed. `model` is the shared axis and appears in
#: both, which is what the dashboards join on.
QUERIES = (
    {
        "dimensions": ["api_key_id", "model"],
        "metrics": METRICS,
        "sourcetype": "openrouter:analytics",
        "checkpoint_suffix": "analytics_by_key_model",
    },
    {
        "dimensions": ["model", "provider"],
        "metrics": METRICS,
        "sourcetype": "openrouter:providers",
        "checkpoint_suffix": "analytics_by_model_provider",
    },
)


def logger_for_input(input_name):
    """Per-input logger -> $SPLUNK_HOME/var/log/splunk/ta_openrouter_<input>.log."""
    return log.Logs().get_logger("{}_{}".format(ADDON_NAME.lower(), input_name))


def get_account_config(session_key, account_name):
    """Return the account stanza (api_key is decrypted by conf_manager)."""
    cfm = conf_manager.ConfManager(
        session_key,
        ADDON_NAME,
        realm="__REST_CREDENTIAL__#{}#configs/conf-{}".format(ADDON_NAME, ACCOUNT_CONF),
    )
    return cfm.get_conf(ACCOUNT_CONF).get(account_name)


def _int_or_default(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def collect_query(client, query, checkpoint, now, backfill_days):
    """Run one query. Returns (events, next_checkpoint).

    ``next_checkpoint`` is None whenever the checkpoint must not move: no
    complete bucket pending, an empty window, or a truncated response. On
    truncation the events are discarded too -- emitting a partial window while
    holding the checkpoint would duplicate those rows on the next run.
    """
    window = compute_window(checkpoint, now, backfill_days)
    if window is None:
        return [], None
    start, end = window
    payload = client.query_analytics(
        query["metrics"], query["dimensions"], GRANULARITY, start, end,
    )
    if is_truncated(payload):
        return [], None
    events = flatten_analytics(payload, query["dimensions"])
    if not events:
        return [], None
    return events, max_bucket(payload)


def validate_input(definition):
    """No external validation beyond what globalConfig already enforces."""
    return


def stream_events(inputs, event_writer):
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        logger = logger_for_input(normalized_input_name)
        try:
            session_key = inputs.metadata["session_key"]
            log_level = conf_manager.get_log_level(
                logger=logger,
                session_key=session_key,
                app_name=ADDON_NAME,
                conf_name=SETTINGS_CONF,
            )
            logger.setLevel(log_level)
            log.modular_input_start(logger, normalized_input_name)

            account = get_account_config(session_key, input_item.get("account"))
            client = OpenRouterClient(
                api_key=account.get("api_key"),
                base_url=account.get("api_base_url") or DEFAULT_BASE_URL,
            )
            ckpt = checkpointer.KVStoreCheckpointer(
                CHECKPOINT_COLLECTION, session_key, ADDON_NAME
            )
            backfill_days = _int_or_default(
                input_item.get("backfill_days"), DEFAULT_BACKFILL_DAYS
            )
            index = input_item.get("index")
            source = "{}://{}".format(INPUT_TYPE, normalized_input_name)
            now = datetime.now(timezone.utc)

            for query in QUERIES:
                ckpt_key = "{}_{}".format(
                    normalized_input_name, query["checkpoint_suffix"]
                )
                events, next_ckpt = collect_query(
                    client, query, ckpt.get(ckpt_key), now, backfill_days
                )
                for event in events:
                    event_writer.write_event(
                        smi.Event(
                            data=json.dumps(event["data"]),
                            time=event["time"],
                            index=index,
                            sourcetype=query["sourcetype"],
                            source=source,
                        )
                    )
                if next_ckpt:
                    ckpt.update(ckpt_key, next_ckpt)
                logger.info(
                    "Ingested {} {} events; checkpoint={}".format(
                        len(events), query["sourcetype"], next_ckpt
                    )
                )

            log.modular_input_end(logger, normalized_input_name)
        except Exception as exc:  # noqa: BLE001
            log.log_exception(
                logger, exc, "OpenRouterAnalyticsError",
                msg_before="Failed to collect OpenRouter analytics: ",
            )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_analytics_helper.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add TA_openrouter/package/bin/openrouter_analytics_helper.py TA_openrouter/tests/test_openrouter_analytics_helper.py
git commit -m "feat(openrouter): add analytics input with per-query checkpoints and truncation guard"
```

---

### Task 9: Keys modular input

**Files:**
- Create: `TA_openrouter/package/bin/openrouter_keys_helper.py`
- Create: `TA_openrouter/tests/test_openrouter_keys_helper.py`

**Interfaces:**
- Consumes: `OpenRouterClient.list_keys` (Task 4), `flatten_keys` and `RFC3339` (Tasks 2-3)
- Produces: `SOURCETYPE = "openrouter:keys"`, `collect_keys(client, now) -> list`, plus UCC `validate_input` / `stream_events`

- [ ] **Step 1: Write the failing tests**

Create `TA_openrouter/tests/test_openrouter_keys_helper.py`:

```python
from datetime import datetime, timezone

from openrouter_keys_helper import SOURCETYPE, collect_keys

NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)


class StubClient(object):
    def __init__(self, keys):
        self._keys = keys
        self.calls = 0

    def list_keys(self, include_disabled=True):
        self.calls += 1
        return self._keys


def test_sourcetype_is_openrouter_keys():
    assert SOURCETYPE == "openrouter:keys"


def test_collect_emits_one_event_per_key():
    client = StubClient([{"hash": "a", "name": "prod", "usage": 1.5},
                         {"hash": "b", "name": "dev", "usage": 0.0}])
    events = collect_keys(client, NOW)
    assert len(events) == 2
    assert events[0]["data"]["hash"] == "a"


def test_collect_stamps_snapshot_at():
    client = StubClient([{"hash": "a"}])
    events = collect_keys(client, NOW)
    assert events[0]["data"]["snapshot_at"] == "2026-07-31T12:00:00Z"


def test_collect_renames_currency_fields():
    client = StubClient([{"hash": "a", "usage": 12.5, "limit_remaining": 3.0}])
    body = collect_keys(client, NOW)[0]["data"]
    assert body["usage_usd"] == 12.5
    assert body["limit_remaining_usd"] == 3.0


def test_collect_on_empty_roster():
    assert collect_keys(StubClient([]), NOW) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_keys_helper.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openrouter_keys_helper'`

- [ ] **Step 3: Write the implementation**

Create `TA_openrouter/package/bin/openrouter_keys_helper.py`:

```python
"""Modular input helper for the ``openrouter_keys`` input.

Snapshots the API key roster. There is no checkpoint: the roster is small and
slowly changing, so every run emits the full list stamped with ``snapshot_at``
and downstream searches dedup on it.

Only the default workspace's keys are returned; multi-workspace enumeration
via workspace_id is out of scope for v0.1.0.

Python 3.9 compatible: Splunk's app runtime is CPython 3.9.25.
"""
try:
    import import_declare_test  # noqa: F401  isort:skip

    from solnlib import conf_manager, log
    from splunklib import modularinput as smi
except ImportError:  # unit tests run without the Splunk runtime
    conf_manager = log = smi = None

import json
from datetime import datetime, timezone

from openrouter_client import OpenRouterClient
from openrouter_transform import RFC3339, flatten_keys

ADDON_NAME = "TA_openrouter"
SETTINGS_CONF = "ta_openrouter_settings"
ACCOUNT_CONF = "ta_openrouter_account"
SOURCETYPE = "openrouter:keys"
INPUT_TYPE = "openrouter_keys"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"


def logger_for_input(input_name):
    return log.Logs().get_logger("{}_{}".format(ADDON_NAME.lower(), input_name))


def get_account_config(session_key, account_name):
    cfm = conf_manager.ConfManager(
        session_key,
        ADDON_NAME,
        realm="__REST_CREDENTIAL__#{}#configs/conf-{}".format(ADDON_NAME, ACCOUNT_CONF),
    )
    return cfm.get_conf(ACCOUNT_CONF).get(account_name)


def collect_keys(client, now):
    """Return one event per API key, stamped with a shared snapshot time."""
    snapshot_at = now.astimezone(timezone.utc).strftime(RFC3339)
    return flatten_keys(client.list_keys(include_disabled=True), snapshot_at)


def validate_input(definition):
    return


def stream_events(inputs, event_writer):
    for input_name, input_item in inputs.inputs.items():
        normalized_input_name = input_name.split("/")[-1]
        logger = logger_for_input(normalized_input_name)
        try:
            session_key = inputs.metadata["session_key"]
            log_level = conf_manager.get_log_level(
                logger=logger,
                session_key=session_key,
                app_name=ADDON_NAME,
                conf_name=SETTINGS_CONF,
            )
            logger.setLevel(log_level)
            log.modular_input_start(logger, normalized_input_name)

            account = get_account_config(session_key, input_item.get("account"))
            client = OpenRouterClient(
                api_key=account.get("api_key"),
                base_url=account.get("api_base_url") or DEFAULT_BASE_URL,
            )
            events = collect_keys(client, datetime.now(timezone.utc))
            source = "{}://{}".format(INPUT_TYPE, normalized_input_name)
            for event in events:
                event_writer.write_event(
                    smi.Event(
                        data=json.dumps(event["data"]),
                        time=event["time"],
                        index=input_item.get("index"),
                        sourcetype=SOURCETYPE,
                        source=source,
                    )
                )
            logger.info("Ingested {} key events".format(len(events)))
            log.modular_input_end(logger, normalized_input_name)
        except Exception as exc:  # noqa: BLE001
            log.log_exception(
                logger, exc, "OpenRouterKeysError",
                msg_before="Failed to collect OpenRouter API keys: ",
            )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_keys_helper.py -v`
Expected: 5 passed

- [ ] **Step 5: Verify Python 3.9 compatibility across all modules**

```bash
for f in TA_openrouter/package/bin/*.py; do
  /opt/splunk/bin/python3 -m py_compile "$f" || echo "FAILED: $f"
done
echo done
```

Expected: `done` with no FAILED lines.

- [ ] **Step 6: Commit**

```bash
git add TA_openrouter/package/bin/openrouter_keys_helper.py TA_openrouter/tests/test_openrouter_keys_helper.py
git commit -m "feat(openrouter): add API key roster snapshot input"
```

---

### Task 10: Sourcetypes, macro, and lookup

**Files:**
- Create: `TA_openrouter/package/default/props.conf`
- Create: `TA_openrouter/package/default/transforms.conf`
- Create: `TA_openrouter/package/default/macros.conf`
- Create: `TA_openrouter/package/lookups/openrouter_key_baseline.csv`
- Create: `TA_openrouter/tests/test_openrouter_conf.py`

**Interfaces:**
- Consumes: sourcetypes emitted by Tasks 8-9
- Produces: `openrouter_index` macro; `openrouter_key_baseline` lookup definition with columns `api_key_id,name,first_seen`

- [ ] **Step 1: Write props.conf**

Create `TA_openrouter/package/default/props.conf`:

```ini
# TZ is load-bearing. TIME_FORMAT treats the trailing Z as a literal
# character, not a UTC designator, so without TZ the indexer would apply its
# own local offset to every event.
#
# TIMESTAMP_FIELDS is deliberately absent: it only applies under
# INDEXED_EXTRACTIONS, and these stanzas use search-time KV_MODE = json, so it
# would be a no-op that invites someone to delete the lines below that do the
# actual work. The authoritative timestamp is the modular input's explicit
# event time; these props are the fallback.

[openrouter:analytics]
KV_MODE = json
TIME_PREFIX = "bucket_start":\s*"
TIME_FORMAT = %Y-%m-%dT%H:%M:%SZ
TZ = UTC
MAX_TIMESTAMP_LOOKAHEAD = 30
SHOULD_LINEMERGE = false
category = Cloud
pulldown_type = 1

[openrouter:providers]
KV_MODE = json
TIME_PREFIX = "bucket_start":\s*"
TIME_FORMAT = %Y-%m-%dT%H:%M:%SZ
TZ = UTC
MAX_TIMESTAMP_LOOKAHEAD = 30
SHOULD_LINEMERGE = false
category = Cloud
pulldown_type = 1

[openrouter:keys]
KV_MODE = json
TIME_PREFIX = "snapshot_at":\s*"
TIME_FORMAT = %Y-%m-%dT%H:%M:%SZ
TZ = UTC
MAX_TIMESTAMP_LOOKAHEAD = 30
SHOULD_LINEMERGE = false
category = Cloud
pulldown_type = 1
```

- [ ] **Step 2: Write macros.conf**

Create `TA_openrouter/package/default/macros.conf`:

```ini
# Every dashboard panel and saved search goes through this macro rather than
# relying on the role's default indexes. Shipping `index=*` means the add-on
# works regardless of which index the operator configured on the inputs;
# narrowing it is a local override.
#
# The alternative -- omitting index= entirely -- searches only the role's
# default indexes and produces a silently empty dashboard when data lands
# anywhere else, with nothing visibly broken.

[openrouter_index]
definition = index=*
iseval = 0
```

- [ ] **Step 3: Write transforms.conf and the lookup**

Create `TA_openrouter/package/default/transforms.conf`:

```ini
[openrouter_key_baseline]
filename = openrouter_key_baseline.csv
```

Create `TA_openrouter/package/lookups/openrouter_key_baseline.csv` containing exactly one line:

```csv
api_key_id,name,first_seen
```

- [ ] **Step 4: Write the conf tests**

Create `TA_openrouter/tests/test_openrouter_conf.py`:

```python
import configparser
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "package"
DEFAULT = PKG / "default"
LOOKUPS = PKG / "lookups"


def read(name):
    parser = configparser.ConfigParser(strict=False)
    parser.optionxform = str
    parser.read(DEFAULT / name)
    return parser


def test_all_three_sourcetypes_declared():
    props = read("props.conf")
    assert set(props.sections()) == {
        "openrouter:analytics", "openrouter:providers", "openrouter:keys"}


def test_every_sourcetype_pins_utc_and_json():
    # Without TZ the indexer applies its local offset, because TIME_FORMAT
    # treats the trailing Z as a literal.
    props = read("props.conf")
    for section in props.sections():
        assert props[section]["TZ"] == "UTC", section
        assert props[section]["KV_MODE"] == "json", section


def test_no_timestamp_fields_under_kv_mode_json():
    props = read("props.conf")
    for section in props.sections():
        assert "TIMESTAMP_FIELDS" not in props[section], section


def test_index_macro_is_permissive_by_default():
    macros = read("macros.conf")
    assert macros["openrouter_index"]["definition"] == "index=*"


def test_baseline_lookup_ships_empty():
    # Shipping fixture keys would make every installer's first run flag every
    # real key as unrecognised.
    lines = (LOOKUPS / "openrouter_key_baseline.csv").read_text().strip().splitlines()
    assert len(lines) == 1, "baseline lookup must ship headers only"
    assert lines[0] == "api_key_id,name,first_seen"
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_conf.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add TA_openrouter/package/default/ TA_openrouter/package/lookups/ TA_openrouter/tests/test_openrouter_conf.py
git commit -m "feat(openrouter): add sourcetypes, index macro, and empty baseline lookup"
```

---

### Task 11: Saved searches and alerts

**Files:**
- Create: `TA_openrouter/package/default/savedsearches.conf`
- Create: `TA_openrouter/tests/test_openrouter_alerts.py`

**Interfaces:**
- Consumes: sourcetypes, macro, and lookup from Task 10
- Produces: saved searches `OpenRouter - Build API Key Baseline`, `OpenRouter - Unrecognised API Key Active`, `OpenRouter - Hourly Spend Spike by Key`

- [ ] **Step 1: Derive the alert threshold against the fixtures**

Create `TA_openrouter/tests/test_openrouter_alerts.py`:

```python
"""Derive and pin the spend-spike threshold against fixture data.

Nothing is copied from TA_anthropic's test_alerts.py -- the shape is the same
(hourly buckets, streamstats window=6) but the multiplier is re-derived here
because the data is different.
"""
import json
import statistics
from collections import defaultdict
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "mockserver" / "fixtures"

#: Standard deviations above the trailing mean before an hour counts as a
#: spike. Pinned by test_threshold_isolates_exactly_one_key below.
SPIKE_SIGMA = 3
WINDOW = 6


def rows():
    payload = json.loads((FIXTURES / "analytics_by_key_model.json").read_text())
    return payload["data"]["data"]


def hourly_spend_by_key():
    totals = defaultdict(float)
    for row in rows():
        totals[(row["api_key_id"], row["date"])] += row["total_usage"]
    by_key = defaultdict(list)
    for (key, stamp), usage in sorted(totals.items(), key=lambda kv: kv[0][1]):
        by_key[key].append((stamp, usage))
    return by_key


def spikes(sigma, window=WINDOW):
    """Replicate `streamstats window=6 current=f` + threshold, in Python."""
    found = []
    for key, series in hourly_spend_by_key().items():
        for index in range(len(series)):
            trailing = [v for _, v in series[max(0, index - window):index]]
            if len(trailing) < window:
                continue
            mean = statistics.fmean(trailing)
            stdev = statistics.pstdev(trailing)
            stamp, value = series[index]
            if stdev and value > mean + sigma * stdev:
                found.append((key, stamp))
    return found


def test_fixture_has_enough_history_for_the_window():
    for key, series in hourly_spend_by_key().items():
        assert len(series) >= WINDOW + 1, "{} has only {} buckets".format(
            key, len(series))


def test_threshold_isolates_exactly_one_key():
    detected = {key for key, _ in spikes(SPIKE_SIGMA)}
    assert len(detected) == 1, "expected one spiking key, got {}".format(detected)


def test_threshold_is_not_trivially_permissive():
    # A sigma so low that everything fires would pass the test above by
    # accident on a fixture with only one key.
    assert len({k for k, _ in spikes(0.5)}) > 1, \
        "fixture cannot distinguish a real threshold from a permissive one"
```

- [ ] **Step 2: Run and tune**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_alerts.py -v`

If `test_threshold_isolates_exactly_one_key` fails, adjust `SPIKE_SIGMA` until exactly one key is detected, then re-run. Record the final value — it goes into the saved search below. If no value isolates one key, the fixture spike is too small; enlarge it in `analytics_by_key_model.json` and re-run Task 6.

- [ ] **Step 3: Write savedsearches.conf**

Create `TA_openrouter/package/default/savedsearches.conf`, substituting the derived `SPIKE_SIGMA` for `3` in the third stanza if it differs:

```ini
# Alert triggers use counttype/relation/quantity. The REST API argument names
# (alert_type, alert_comparator, alert_threshold) are absent from
# savedsearches.conf.spec and slim validate rejects them.

[OpenRouter - Build API Key Baseline]
search = `openrouter_index` sourcetype=openrouter:keys \
| stats latest(name) as name min(_time) as first_seen by hash \
| rename hash as api_key_id \
| eval first_seen=strftime(first_seen,"%Y-%m-%dT%H:%M:%SZ") \
| table api_key_id name first_seen \
| outputlookup openrouter_key_baseline
description = Populates the sanctioned API key baseline from the observed roster. Run once from inside the TA_openrouter app after the keys input has collected, then re-run whenever keys are legitimately added.
dispatch.earliest_time = -7d
dispatch.latest_time = now
enableSched = 0
cron_schedule = 0 6 * * *

[OpenRouter - Unrecognised API Key Active]
search = `openrouter_index` sourcetype=openrouter:analytics \
| stats sum(total_usage_usd) as spend_usd sum(request_count) as requests values(model) as models by api_key_id \
| lookup openrouter_key_baseline api_key_id OUTPUT name as baseline_name \
| where isnull(baseline_name) \
| table api_key_id spend_usd requests models
description = An API key is producing traffic but is absent from the sanctioned baseline.
dispatch.earliest_time = -24h
dispatch.latest_time = now
enableSched = 1
cron_schedule = 15 * * * *
counttype = number of events
relation = greater than
quantity = 0
alert.severity = 4
alert.suppress = 1
alert.suppress.period = 6h
alert.track = 1

[OpenRouter - Hourly Spend Spike by Key]
search = `openrouter_index` sourcetype=openrouter:analytics \
| bin _time span=1h \
| stats sum(total_usage_usd) as spend_usd by _time api_key_id \
| sort 0 api_key_id _time \
| streamstats window=6 current=f global=f avg(spend_usd) as trailing_avg stdev(spend_usd) as trailing_stdev by api_key_id \
| where isnotnull(trailing_stdev) AND trailing_stdev>0 AND spend_usd > trailing_avg + 3*trailing_stdev \
| eval spike_ratio=round(spend_usd/trailing_avg,2) \
| table _time api_key_id spend_usd trailing_avg spike_ratio
description = Hourly spend for an API key exceeded three standard deviations above its six-hour trailing mean.
dispatch.earliest_time = -48h
dispatch.latest_time = now
enableSched = 1
cron_schedule = 20 * * * *
counttype = number of events
relation = greater than
quantity = 0
alert.severity = 3
alert.suppress = 1
alert.suppress.period = 2h
alert.track = 1
```

- [ ] **Step 4: Verify the conf parses and uses the correct trigger keys**

```bash
python3 -c "
import configparser
p=configparser.ConfigParser(strict=False); p.optionxform=str
p.read('TA_openrouter/package/default/savedsearches.conf')
banned={'alert_type','alert_comparator','alert_threshold'}
for s in p.sections():
    assert not banned & set(p[s]), (s, banned & set(p[s]))
    if p[s].get('enableSched')=='1':
        assert p[s]['counttype']=='number of events', s
print('OK', p.sections())
"
```

Expected: `OK` followed by the three stanza names.

- [ ] **Step 5: Commit**

```bash
git add TA_openrouter/package/default/savedsearches.conf TA_openrouter/tests/test_openrouter_alerts.py
git commit -m "feat(openrouter): add baseline builder and two alerts with derived threshold"
```

---

### Task 12: Dashboards and navigation

**Files:**
- Create: `TA_openrouter/package/default/data/ui/views/openrouter_model_mix.xml`
- Create: `TA_openrouter/package/default/data/ui/views/openrouter_provider_routing.xml`
- Create: `TA_openrouter/package/default/data/ui/views/openrouter_key_governance.xml`
- Create: `TA_openrouter/package/default/data/ui/nav/default.xml`
- Create: `TA_openrouter/tests/test_openrouter_dashboards.py`

**Interfaces:**
- Consumes: sourcetypes, macro, and lookup from Tasks 10-11
- Produces: nothing consumed by later tasks

- [ ] **Step 1: Write the Model Mix dashboard**

Create `TA_openrouter/package/default/data/ui/views/openrouter_model_mix.xml`:

```xml
<dashboard version="1.1" theme="light">
  <label>OpenRouter - Model Mix &amp; Spend</label>
  <description>Which models are being used, and what each costs.</description>
  <row>
    <panel>
      <title>Spend by model (USD/hour)</title>
      <chart>
        <search>
          <query>`openrouter_index` sourcetype=openrouter:analytics | timechart span=1h sum(total_usage_usd) by model</query>
          <earliest>-24h</earliest>
          <latest>now</latest>
        </search>
        <option name="charting.chart">column</option>
        <option name="charting.chart.stackMode">stacked</option>
        <option name="charting.axisTitleY.text">USD</option>
      </chart>
    </panel>
  </row>
  <row>
    <panel>
      <title>Token volume by model</title>
      <chart>
        <search>
          <query>`openrouter_index` sourcetype=openrouter:analytics | stats sum(tokens_prompt) as Prompt sum(tokens_completion) as Completion sum(reasoning_tokens) as Reasoning sum(cached_tokens) as Cached by model</query>
          <earliest>-24h</earliest>
          <latest>now</latest>
        </search>
        <option name="charting.chart">bar</option>
        <option name="charting.chart.stackMode">stacked</option>
      </chart>
    </panel>
    <panel>
      <title>Top models</title>
      <table>
        <search>
          <query>`openrouter_index` sourcetype=openrouter:analytics | stats sum(request_count) as requests sum(tokens_total) as tokens sum(total_usage_usd) as spend_usd by model | eval usd_per_request=round(spend_usd/requests,6) | sort - spend_usd</query>
          <earliest>-24h</earliest>
          <latest>now</latest>
        </search>
        <option name="drilldown">none</option>
      </table>
    </panel>
  </row>
  <row>
    <panel>
      <title>Model mix per API key</title>
      <table>
        <search>
          <query>`openrouter_index` sourcetype=openrouter:analytics | stats sum(total_usage_usd) as spend_usd by api_key_id model | sort - spend_usd</query>
          <earliest>-24h</earliest>
          <latest>now</latest>
        </search>
        <option name="drilldown">none</option>
      </table>
    </panel>
  </row>
</dashboard>
```

- [ ] **Step 2: Write the Provider Routing dashboard**

Create `TA_openrouter/package/default/data/ui/views/openrouter_provider_routing.xml`:

```xml
<dashboard version="1.1" theme="light">
  <label>OpenRouter - Provider Routing</label>
  <description>Which upstream providers served each model, and at what cost.</description>
  <row>
    <panel>
      <title>Spend share by provider (USD/hour)</title>
      <chart>
        <search>
          <query>`openrouter_index` sourcetype=openrouter:providers | timechart span=1h sum(total_usage_usd) by provider</query>
          <earliest>-24h</earliest>
          <latest>now</latest>
        </search>
        <option name="charting.chart">area</option>
        <option name="charting.chart.stackMode">stacked</option>
        <option name="charting.axisTitleY.text">USD</option>
      </chart>
    </panel>
  </row>
  <row>
    <panel>
      <title>Provider by model</title>
      <table>
        <search>
          <query>`openrouter_index` sourcetype=openrouter:providers | stats sum(request_count) as requests sum(total_usage_usd) as spend_usd by model provider | sort model - spend_usd</query>
          <earliest>-24h</earliest>
          <latest>now</latest>
        </search>
        <option name="drilldown">none</option>
      </table>
    </panel>
    <panel>
      <title>Cost per request by provider, same model</title>
      <description>A routing shift shows up here as a cost change nobody requested.</description>
      <table>
        <search>
          <query>`openrouter_index` sourcetype=openrouter:providers | stats sum(total_usage_usd) as spend_usd sum(request_count) as requests by model provider | where requests>0 | eval usd_per_request=round(spend_usd/requests,6) | eventstats dc(provider) as providers by model | where providers>1 | table model provider requests usd_per_request | sort model - usd_per_request</query>
          <earliest>-24h</earliest>
          <latest>now</latest>
        </search>
        <option name="drilldown">none</option>
      </table>
    </panel>
  </row>
</dashboard>
```

- [ ] **Step 3: Write the Key Governance dashboard**

Create `TA_openrouter/package/default/data/ui/views/openrouter_key_governance.xml`:

```xml
<dashboard version="1.1" theme="light">
  <label>OpenRouter - Key Governance</label>
  <description>API key inventory and unrecognised key activity.</description>
  <row>
    <panel>
      <title>Unrecognised keys (active but not in baseline)</title>
      <table>
        <search>
          <query>`openrouter_index` sourcetype=openrouter:analytics | stats sum(total_usage_usd) as spend_usd sum(request_count) as requests values(model) as models by api_key_id | lookup openrouter_key_baseline api_key_id OUTPUT name as baseline_name | where isnull(baseline_name) | table api_key_id spend_usd requests models</query>
          <earliest>-24h</earliest>
          <latest>now</latest>
        </search>
        <option name="drilldown">none</option>
      </table>
    </panel>
  </row>
  <row>
    <panel>
      <title>API key roster</title>
      <table>
        <search>
          <query>`openrouter_index` sourcetype=openrouter:keys | stats latest(name) as name latest(label) as label latest(disabled) as disabled latest(created_at) as created latest(limit_usd) as limit_usd latest(limit_remaining_usd) as remaining_usd latest(usage_usd) as usage_usd by hash | sort - usage_usd</query>
          <earliest>-24h</earliest>
          <latest>now</latest>
        </search>
        <option name="drilldown">none</option>
      </table>
    </panel>
  </row>
  <row>
    <panel>
      <title>Spend by key (USD/hour)</title>
      <chart>
        <search>
          <query>`openrouter_index` sourcetype=openrouter:analytics | timechart span=1h sum(total_usage_usd) by api_key_id</query>
          <earliest>-24h</earliest>
          <latest>now</latest>
        </search>
        <option name="charting.chart">line</option>
        <option name="charting.axisTitleY.text">USD</option>
      </chart>
    </panel>
  </row>
</dashboard>
```

- [ ] **Step 4: Write the navigation**

Create `TA_openrouter/package/default/data/ui/nav/default.xml`:

```xml
<nav search_view="search" color="#65A637">
  <view name="configuration" default="true" />
  <view name="inputs" />
  <collection label="Dashboards">
    <view name="openrouter_model_mix" />
    <view name="openrouter_provider_routing" />
    <view name="openrouter_key_governance" />
  </collection>
  <view name="search" />
</nav>
```

- [ ] **Step 5: Write the dashboard tests**

Create `TA_openrouter/tests/test_openrouter_dashboards.py`:

```python
# Uses stdlib ElementTree deliberately. The usual objection -- XXE and
# billion-laughs -- requires untrusted input; these are dashboard files
# authored in this repo and read only by this test. Switching to defusedxml
# would add a dependency to the repo-root requirements-dev.txt, which the
# no-modification-outside-TA_openrouter constraint forbids.
import re
import xml.etree.ElementTree as ET
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "package" / "default" / "data" / "ui"
VIEWS = UI / "views"
NAV = UI / "nav" / "default.xml"

EXPECTED = {
    "openrouter_model_mix.xml",
    "openrouter_provider_routing.xml",
    "openrouter_key_governance.xml",
}


def test_all_three_dashboards_exist_and_parse():
    assert {p.name for p in VIEWS.glob("*.xml")} == EXPECTED
    for path in VIEWS.glob("*.xml"):
        ET.parse(path)


def test_every_query_uses_the_index_macro():
    # Omitting the macro means the panel searches only the role's default
    # indexes and renders empty with nothing visibly broken.
    for path in VIEWS.glob("*.xml"):
        for query in ET.parse(path).iter("query"):
            assert "`openrouter_index`" in query.text, "{}: {}".format(
                path.name, query.text[:80])


def test_no_query_sums_a_rate_metric():
    rate_metrics = [
        "avg_latency", "p50_latency", "p90_latency", "p99_latency",
        "avg_throughput", "p50_throughput", "p90_throughput", "p99_throughput",
        "cache_hit_rate", "blended_cost_per_million_tokens",
        "guardrail_invoked_rate", "response_cached_rate",
    ]
    pattern = re.compile(r"sum\((" + "|".join(rate_metrics) + r")\)")
    for path in VIEWS.glob("*.xml"):
        for query in ET.parse(path).iter("query"):
            assert not pattern.search(query.text), "{}: {}".format(
                path.name, query.text[:80])


def test_nav_lists_every_dashboard():
    names = {v.get("name") for v in ET.parse(NAV).iter("view")}
    for stem in (p.stem for p in VIEWS.glob("*.xml")):
        assert stem in names, stem
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/pytest TA_openrouter/tests/test_openrouter_dashboards.py -v`
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add TA_openrouter/package/default/data/ TA_openrouter/tests/test_openrouter_dashboards.py
git commit -m "feat(openrouter): add model mix, provider routing, and key governance dashboards"
```

---

### Task 13: Build, package, and document

**Files:**
- Create: `TA_openrouter/scripts/build.sh`
- Create: `TA_openrouter/README.md`
- Create: `TA_openrouter/docs/architecture.md`
- Create: `TA_openrouter/docs/operations.md`
- Modify: `TA_openrouter/docs/schema-verification.md`

**Interfaces:**
- Consumes: everything
- Produces: `dist/TA_openrouter-0.1.0.tar.gz`

- [ ] **Step 1: Write build.sh**

Create `TA_openrouter/scripts/build.sh`:

```bash
#!/usr/bin/env bash
# Build TA_openrouter: ucc-gen build -> slim validate -> slim package
#
# Uses explicit .venv/bin/* paths from the repo root instead of activating the
# venv, so the script behaves identically in interactive shells, CI, and
# non-interactive agent shells. The repo-root toolchain is a read-only
# dependency; nothing outside TA_openrouter/ is modified.
set -euo pipefail
cd "$(dirname "$0")/.."

TA_DIR="$(pwd)"
ROOT="$(cd .. && pwd)"
VENV="$ROOT/.venv/bin"
UCC_GEN="$VENV/ucc-gen"
SLIM="$VENV/slim"
TA_VERSION="${TA_VERSION:-0.1.0}"

for tool in "$UCC_GEN" "$SLIM"; do
  if [ ! -x "$tool" ]; then
    echo "ERROR: $tool not found. Create the venv and install requirements-dev.txt first." >&2
    exit 1
  fi
done

echo "==> clean stale build artifacts"
rm -rf "$TA_DIR/output"
find "$TA_DIR/package" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$TA_DIR/package" -name '*.pyc' -delete 2>/dev/null || true

echo "==> ucc-gen build (version ${TA_VERSION})"
"$UCC_GEN" build --source package --ta-version "$TA_VERSION"

echo "==> strip build leftovers from output"
find "$TA_DIR/output/TA_openrouter" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$TA_DIR/output/TA_openrouter" -name '*.pyc' -delete 2>/dev/null || true
find "$TA_DIR/output/TA_openrouter" -name '.DS_Store' -delete 2>/dev/null || true

echo "==> slim validate"
"$SLIM" validate "$TA_DIR/output/TA_openrouter"

echo "==> slim package"
mkdir -p "$ROOT/dist"
rm -f "$ROOT"/dist/TA_openrouter-*.tar.gz
"$SLIM" package "$TA_DIR/output/TA_openrouter" -o "$ROOT/dist"

echo "==> done"
ls -lh "$ROOT/dist/"
```

```bash
chmod +x TA_openrouter/scripts/build.sh
```

- [ ] **Step 2: Run both test suites, then build**

```bash
.venv/bin/pytest TA_openrouter/tests/ -v
```

Expected: all tests pass.

```bash
.venv/bin/pytest tests/ -q
```

Expected: `58 passed` — `TA_anthropic` is unaffected.

```bash
./TA_openrouter/scripts/build.sh
```

Expected: `slim validate` reports 0 errors; a tarball appears in `dist/`.

- [ ] **Step 3: Verify the artifact is clean**

```bash
tar tzf dist/TA_openrouter-0.1.0.tar.gz | grep -E '__pycache__|\.pyc$|\.DS_Store' && echo "DIRTY" || echo "CLEAN"
```

Expected: `CLEAN`

- [ ] **Step 4: Run AppInspect precert**

```bash
.venv/bin/splunk-appinspect inspect dist/TA_openrouter-0.1.0.tar.gz \
  --mode precert --included-tags cloud 2>&1 | tail -30
```

Expected: 0 errors, 0 failures. Fix any reported failure and rebuild before continuing.

- [ ] **Step 5: Write the README**

Create `TA_openrouter/README.md`:

```markdown
# OpenRouter Add-on for Splunk (TA_openrouter)

Ingests OpenRouter's management API and reports on model-mix spend, provider
routing, and API key governance.

## Requirements

- Splunk 9.0 or later
- An OpenRouter **management API key**, created at
  <https://openrouter.ai/settings/management-keys>

> Management keys carry the same `sk-or-v1-` prefix as ordinary inference keys.
> They are distinguishable only by the page that created them. If the add-on
> reports 401, verify the key came from the management-keys page.

## Installation

1. Install the package from `dist/TA_openrouter-0.1.0.tar.gz`.
2. Restart Splunk.
3. **Configuration → Account** — add an account with your management key. Leave
   the base URL at `https://openrouter.ai/api/v1`.
4. **Inputs** — create one Analytics input and one API Keys input. Both default
   to a 3600-second interval, matching the hourly analytics granularity.
5. Run **OpenRouter - Build API Key Baseline** once from inside this app, then
   confirm `| inputlookup openrouter_key_baseline` returns your sanctioned keys.

## Inputs

| Input | Endpoint | Sourcetypes |
| --- | --- | --- |
| Analytics | `POST /analytics/query` (x2) | `openrouter:analytics`, `openrouter:providers` |
| API Keys | `GET /keys` | `openrouter:keys` |

The Analytics input issues two queries per run because OpenRouter caps
`dimensions` at two: one grouped by `[api_key_id, model]`, one by
`[model, provider]`. Each holds its own checkpoint.

## Dashboards

- **Model Mix & Spend** — spend and tokens by model, per-key model mix
- **Provider Routing** — which upstream served each model, and at what cost
- **Key Governance** — unrecognised keys, roster, spend by key

## Alerts

- **Unrecognised API Key Active** — a key producing traffic that is absent from
  the baseline lookup
- **Hourly Spend Spike by Key** — hourly spend above three standard deviations
  over a six-hour trailing mean

See `docs/operations.md` for tuning and `docs/architecture.md` for the code
layout.
```

- [ ] **Step 6: Write architecture.md**

Create `TA_openrouter/docs/architecture.md`:

```markdown
# Architecture

Four modules under `package/bin/`, split so everything except the two helpers
is testable without a Splunk runtime.

## openrouter_client.py

HTTP only. Bearer auth, `POST /analytics/query`, `GET /keys` with offset
pagination, one retry on 429 and 5xx. Returns response bodies verbatim — it
does not interpret them.

4xx other than 429 is **not** retried. A management key used against the wrong
endpoint, or an inference key used as a management key, fails permanently;
retrying burns an interval for nothing.

## openrouter_transform.py

Pure logic. No Splunk imports, no network, no clock beyond what is passed in.

- `compute_window` snaps to complete hours and clamps backfill to 30 days
- `flatten_analytics` is parameterised by the dimensions actually requested,
  because `/analytics/query` returns untyped rows shaped by the request
- `is_truncated` reads `metadata.truncated`
- Currency fields get a `_usd` suffix so the unit is never ambiguous

## openrouter_analytics_helper.py

The modular input. Iterates `QUERIES`, each with its own checkpoint key
suffixed by `checkpoint_suffix`.

**The checkpoint must not advance past a truncated response.** `collect_query`
returns `(events, None)` on truncation and discards the partial rows. Emitting
them while holding the checkpoint would duplicate them next run; advancing past
them would lose the missing rows permanently, with no error anywhere.

## openrouter_keys_helper.py

Snapshot input. No checkpoint: the roster is small, every run emits the whole
list stamped with `snapshot_at`, and searches dedup on it.

## Checkpoints

KV Store collection `TA_openrouter_checkpoints`, keys formed as
`<input_name>_<checkpoint_suffix>`. They are **not** under
`$SPLUNK_HOME/var/lib/splunk/modinputs/`; deleting anything there does nothing.
```

- [ ] **Step 7: Write operations.md**

Create `TA_openrouter/docs/operations.md`:

```markdown
# Operations

## Populating the baseline

`openrouter_key_baseline.csv` ships with headers only. Until you populate it,
the Unrecognised Key alert treats **every** key as unrecognised.

Run **OpenRouter - Build API Key Baseline** from inside the TA_openrouter app —
not from Search & Reporting. `outputlookup` writes to whichever app the search
runs in, so running it elsewhere silently creates the lookup in the wrong place.

Re-run it whenever keys are legitimately added.

## The index macro

All searches go through `openrouter_index`, shipping as `index=*`. To narrow
it, override the macro locally rather than editing the searches:

    [openrouter_index]
    definition = index=openrouter

## "No new events" is often correct

`compute_window` only releases complete hours. Re-running an input inside the
same clock hour is expected to collect nothing. Diagnose before resetting a
checkpoint.

## Truncation

`/analytics/query` caps rows and reports `metadata.truncated`. The add-on
requests 50,000 rows, well above the server default of 1,000. If truncation
still occurs the input holds its checkpoint and logs the fact rather than
advancing — so the window is retried rather than lost. Persistent truncation
means the interval is too long for your traffic volume; shorten it.

## Additive versus rate metrics

v0.1.0 ingests only additive metrics. OpenRouter also exposes rate metrics
(`avg_latency`, `p90_latency`, `cache_hit_rate`,
`blended_cost_per_million_tokens`, and others carrying `is_rate: true`).
**Never `sum()` those across time buckets or dimension values** — the result is
meaningless and Splunk will not warn you. If you add them, use `avg` or
`latest`.

`cache_hit_rate` is derivable in the meantime as
`cached_tokens / tokens_prompt`.

## Retention

`/activity` is capped at 30 completed UTC days. `/analytics/query` documents no
limit but it is unmeasured, so `backfill_days` clamps to 30. If you establish
the real figure, record it here and raise `MAX_BACKFILL_DAYS` in
`openrouter_transform.py`.

## Workspaces

`GET /keys` returns only the default workspace unless `workspace_id` is passed.
Multi-workspace enumeration is not implemented in v0.1.0; keys in other
workspaces will appear unrecognised.
```

- [ ] **Step 8: Update schema-verification.md**

Append this table, filling the Status column from what Task 1 actually recorded. `VERIFIED` means the field was observed in a live response; `ASSUMED` means it comes from the OpenAPI spec but no response contained it.

```markdown
## Field verification — 2026-07-31

| Endpoint | Field | Used for | Status |
| --- | --- | --- | --- |
| `/analytics/query` | `date` | event time, checkpoint | |
| `/analytics/query` | `api_key_id` | key attribution, baseline join | |
| `/analytics/query` | `model` | model mix, provider join | |
| `/analytics/query` | `provider` | provider routing | |
| `/analytics/query` | `total_usage` | spend (USD) | |
| `/analytics/query` | `request_count` | request volume | |
| `/analytics/query` | `tokens_total` | token volume | |
| `/analytics/query` | `tokens_prompt` | token split | |
| `/analytics/query` | `tokens_completion` | token split | |
| `/analytics/query` | `reasoning_tokens` | token split | |
| `/analytics/query` | `cached_tokens` | token split, cache rate | |
| `/analytics/query` | `byok_usage` | BYOK spend (USD) | |
| `/analytics/query` | `metadata.truncated` | checkpoint guard | |
| `/keys` | `hash` | roster key, baseline join | |
| `/keys` | `name` | roster display | |
| `/keys` | `label` | roster display | |
| `/keys` | `disabled` | roster display | |
| `/keys` | `created_at` | roster display | |
| `/keys` | `limit` | roster display (USD) | |
| `/keys` | `limit_remaining` | roster display (USD) | |
| `/keys` | `usage` | roster display (USD) | |

Anything marked ASSUMED is a live risk: the transform tolerates its absence,
but the dashboard column will be blank. Re-run
`scripts/record_fixtures.py` once real traffic exists and promote the row.
```

- [ ] **Step 9: Final gate — confirm nothing outside TA_openrouter changed**

```bash
git diff --stat main -- TA_anthropic scripts tests mockserver deck demo
```

Expected: no output — those trees are untouched.

```bash
git status --porcelain | grep -v 'TA_openrouter/' | grep -v 'HANDOFF.md' | grep -v 'splunk/.env.example'
```

Expected: no output.

- [ ] **Step 10: Commit**

```bash
git add TA_openrouter/
git commit -m "feat(openrouter): add build script, README, and operator documentation"
```

---

## Verification Record

After Task 13, fill this in the way `HANDOFF.md` does for `TA_anthropic`:

| Gate | Result |
| --- | --- |
| `pytest TA_openrouter/tests/` | |
| `pytest tests/` (TA_anthropic unaffected) | expect 58 passed |
| `slim validate` | |
| AppInspect precert | |
| Junk in artifact | |
| Python 3.9 compile, all `bin/` modules | |
| `TA_anthropic` / `scripts/` / `tests/` / `mockserver/` untouched | |

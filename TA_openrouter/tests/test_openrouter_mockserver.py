import copy
import re
from datetime import datetime, timezone
from importlib import reload
from urllib.parse import urlsplit

from fastapi.testclient import TestClient

import openrouter_mockserver.app as mockserver_app
from openrouter_mockserver.app import app
from openrouter_client import OpenRouterClient

client = TestClient(app)
AUTH = {"Authorization": "Bearer sk-or-v1-anything"}


class _TestClientSession:
    """Adapts fastapi.testclient.TestClient to the `session` interface
    OpenRouterClient expects (`.get`/`.post` returning `.status_code`,
    `.text`, `.json()`), so the *real* client's request/pagination logic
    runs against the mock instead of a hand-rolled request against the
    endpoint in isolation. OpenRouterClient builds absolute URLs from its
    own base_url; only the path matters to the in-process ASGI TestClient,
    so everything but the path is discarded."""

    def __init__(self, test_client):
        self._client = test_client

    @staticmethod
    def _path(url):
        return urlsplit(url).path

    def get(self, url, headers=None, params=None, timeout=None):
        return self._client.get(self._path(url), params=params, headers=headers)

    def post(self, url, headers=None, json=None, timeout=None):
        return self._client.post(self._path(url), json=json, headers=headers)

QUERY = {
    "metrics": ["total_usage", "request_count"],
    "dimensions": ["api_key_id", "model"],
    "granularity": "hour",
    "time_range": {"start": "2000-01-01T00:00:00Z", "end": "2100-01-01T00:00:00Z"},
    # Matches openrouter_client.DEFAULT_ROW_LIMIT / the live API's actual
    # maximum. Anything above this is rejected with 400 -- see
    # test_analytics_limit_above_api_maximum_returns_400.
    "limit": 10000,
}

# Real analytics rows are space-separated and offset-free, e.g.
# "2026-07-31 18:00:00" -- never RFC 3339. See docs/schema-verification.md.
SPACE_SEPARATED_TS = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")


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


def test_analytics_key_model_rows_use_date_hour_bucket_in_observed_format():
    # Real fixture (analytics_by_key_model.json) carries `date__hour`, not a
    # generic `date` column -- the plan's TIME_FIELD = "date" was falsified
    # by the live recording. The value itself is space-separated, not RFC
    # 3339.
    rows = client.post("/api/v1/analytics/query", json=QUERY,
                        headers=AUTH).json()["data"]["data"]
    assert rows
    for row in rows:
        assert "date__hour" in row
        assert "date" not in row
        assert "created_at__hour" not in row
        assert SPACE_SEPARATED_TS.match(row["date__hour"])
        assert "T" not in row["date__hour"]
        assert "Z" not in row["date__hour"]


def test_analytics_switches_shape_on_provider_dimensions():
    body = dict(QUERY, dimensions=["model", "provider"])
    rows = client.post("/api/v1/analytics/query", json=body,
                        headers=AUTH).json()["data"]["data"]
    assert rows
    for row in rows:
        assert "provider" in row
        assert "api_key_id" not in row


def test_analytics_model_provider_rows_use_created_at_hour_bucket():
    # Real fixture (analytics_by_model_provider.json) uses a *different*
    # bucket column than the key/model fixture for the same conceptual
    # value -- there is no single shared TIME_FIELD constant.
    body = dict(QUERY, dimensions=["model", "provider"])
    rows = client.post("/api/v1/analytics/query", json=body,
                        headers=AUTH).json()["data"]["data"]
    assert rows
    for row in rows:
        assert "created_at__hour" in row
        assert "date__hour" not in row
        assert SPACE_SEPARATED_TS.match(row["created_at__hour"])


def test_analytics_response_has_no_warnings_key():
    # Real /analytics/query responses never carry a "warnings" key --
    # VERIFIED absent in docs/schema-verification.md. The plan's mock
    # synthesized one; that would be testing a shape the live API never
    # sends.
    payload = client.post("/api/v1/analytics/query", json=QUERY,
                           headers=AUTH).json()
    assert "warnings" not in payload["data"]


def test_analytics_honours_time_range():
    body = dict(QUERY, time_range={"start": "2000-01-01T00:00:00Z",
                                    "end": "2000-01-02T00:00:00Z"})
    rows = client.post("/api/v1/analytics/query", json=body,
                        headers=AUTH).json()["data"]["data"]
    assert rows == []


def test_analytics_time_range_includes_bucket_at_start():
    # FIX 2 (93-agent audit): openrouter_transform.compute_window's resume
    # fix (checkpoint + one GRANULARITY step, instead of the checkpoint
    # reused verbatim) depends on the server's `start` bound being
    # INCLUSIVE -- otherwise stepping past the checkpoint would skip an
    # extra bucket instead of landing exactly on the next unemitted one.
    # The audit found this suite exercised the `end` bound (the test above)
    # but never pinned the `start` bound's inclusivity; this closes that
    # gap directly against the mock's actual filtering logic.
    full = client.post("/api/v1/analytics/query", json=QUERY,
                        headers=AUTH).json()["data"]["data"]
    oldest = min(row["date__hour"] for row in full)
    oldest_dt = datetime.strptime(oldest, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc)
    start_inclusive = oldest_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    body = dict(QUERY, time_range={"start": start_inclusive,
                                    "end": "2100-01-01T00:00:00Z"})
    rows = client.post("/api/v1/analytics/query", json=body,
                        headers=AUTH).json()["data"]["data"]
    assert any(row["date__hour"] == oldest for row in rows), (
        "start bound must be inclusive: a bucket exactly at `start` was "
        "excluded"
    )


def test_analytics_time_range_excludes_bucket_at_or_after_end():
    # The request's time_range arrives RFC 3339 while fixture rows are
    # space-separated; filtering must normalize both, not string-compare
    # them, or this boundary would never actually exclude anything.
    full = client.post("/api/v1/analytics/query", json=QUERY,
                        headers=AUTH).json()["data"]["data"]
    newest = max(row["date__hour"] for row in full)
    newest_dt = datetime.strptime(newest, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc)
    end_exclusive = newest_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    body = dict(QUERY, time_range={"start": "2000-01-01T00:00:00Z",
                                    "end": end_exclusive})
    rows = client.post("/api/v1/analytics/query", json=body,
                        headers=AUTH).json()["data"]["data"]
    assert rows
    assert all(row["date__hour"] != newest for row in rows)


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


def test_analytics_limit_above_api_maximum_returns_400():
    # This is the exact bug a live collection run hit: the client sent
    # limit=50000, the real API rejected it with a 400 in this shape, and
    # both analytics queries failed so the add-on ingested zero events. The
    # mock previously accepted any limit, which is why 198 passing tests
    # never caught it. Body shape is asserted, not just the status code, so
    # a mock that returns *a* 400 without matching the real error can't
    # silently pass this.
    body = dict(QUERY, limit=10001)
    resp = client.post("/api/v1/analytics/query", json=body, headers=AUTH)
    assert resp.status_code == 400
    payload = resp.json()
    assert payload == {
        "error": {
            "message": "limit: Too big: expected number to be <=10000",
            "code": 400,
        }
    }


def test_analytics_limit_at_api_maximum_is_accepted():
    # The boundary itself (10000) must still succeed -- only values above
    # it are rejected.
    body = dict(QUERY, limit=10000)
    resp = client.post("/api/v1/analytics/query", json=body, headers=AUTH)
    assert resp.status_code == 200


def test_analytics_anchors_newest_bucket_to_current_utc_hour():
    top_of_hour = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0)
    rows = client.post("/api/v1/analytics/query", json=QUERY,
                        headers=AUTH).json()["data"]["data"]
    newest = max(row["date__hour"] for row in rows)
    newest_dt = datetime.strptime(newest, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone.utc)
    assert newest_dt == top_of_hour


def test_keys_requires_authorization_header():
    assert client.get("/api/v1/keys").status_code == 401


def test_keys_returns_roster():
    data = client.get("/api/v1/keys", headers=AUTH).json()["data"]
    assert data
    assert "hash" in data[0]


def test_keys_roster_omits_shadow_key_present_in_analytics():
    # docs/schema-verification.md: analytics rows join to the roster's
    # `name`, never `hash`, and Shadow_Intern_Key deliberately produces
    # analytics traffic while having no roster entry at all.
    roster_names = {key["name"] for key in
                     client.get("/api/v1/keys", headers=AUTH).json()["data"]}
    assert "Shadow_Intern_Key" not in roster_names
    assert roster_names


def test_keys_offset_past_roster_end_returns_empty_page():
    # OpenRouterClient.list_keys() pages by advancing `offset` until it
    # receives an EMPTY page -- not a short one -- then stops. A mock that
    # ignores `offset` and always returns the full roster never emits that
    # empty page, so the real client's loop never terminates.
    full = client.get("/api/v1/keys", headers=AUTH).json()["data"]
    resp = client.get("/api/v1/keys", params={"offset": len(full)},
                       headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_keys_offset_mid_roster_returns_remaining_keys():
    full = client.get("/api/v1/keys", headers=AUTH).json()["data"]
    assert len(full) > 1
    resp = client.get("/api/v1/keys", params={"offset": 1}, headers=AUTH)
    assert resp.json()["data"] == full[1:]


def test_keys_include_disabled_false_filters_disabled_keys():
    # The client always sends include_disabled explicitly; a mock that
    # ignores it can't exercise the false path at all.
    resp = client.get("/api/v1/keys",
                       params={"include_disabled": "false"}, headers=AUTH)
    assert resp.status_code == 200
    assert all(not key.get("disabled") for key in resp.json()["data"])


def test_real_client_list_keys_terminates_against_mock():
    # Drives the REAL OpenRouterClient.list_keys() -- not a hand-rolled
    # request -- against the mock, using the TestClient as the HTTP
    # transport. This is the assertion that would have caught the mock
    # never emitting an empty page: against the unfixed mock this call
    # raises OpenRouterAPIError(500, "Pagination exceeded ...") once
    # KEYS_MAX_PAGES is hit, instead of returning.
    real_client = OpenRouterClient(
        "sk-or-v1-anything",
        base_url="http://testserver/api/v1",
        session=_TestClientSession(client),
        retry_backoff=0,
    )
    keys = real_client.list_keys()
    expected = client.get("/api/v1/keys", headers=AUTH).json()["data"]
    assert keys == expected
    assert keys


def test_restart_within_same_hour_reproduces_identical_timestamps():
    # Re-importing the module simulates a restart. Because the anchor is an
    # hour boundary rather than the literal import instant, two "restarts"
    # inside the same wall-clock hour must produce byte-identical buckets --
    # otherwise a restart could mint a second set of timestamps that slip
    # past a consumer's checkpoint.
    before = copy.deepcopy(
        mockserver_app.ANALYTICS[mockserver_app.BY_KEY_MODEL])
    reload(mockserver_app)
    after = mockserver_app.ANALYTICS[mockserver_app.BY_KEY_MODEL]
    assert before == after

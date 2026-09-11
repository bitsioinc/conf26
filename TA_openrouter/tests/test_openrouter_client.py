import pytest

from openrouter_client import DEFAULT_ROW_LIMIT, OpenRouterAPIError, OpenRouterClient

#: Verified against a live collection run: the real API rejects any
#: analytics `limit` above this with a 400. Mirrors
#: openrouter_mockserver.app.MAX_ANALYTICS_LIMIT.
API_MAX_ANALYTICS_LIMIT = 10000


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
    # overflows that easily, so we ask for more than the default. But the
    # API caps `limit` at 10000 and rejects anything above that with a 400
    # (see DEFAULT_ROW_LIMIT's docstring) -- so "more than the default"
    # must stay within the API's actual ceiling, not just exceed 1000.
    session = FakeSession([FakeResponse(200, {"data": {"data": []}})])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    client.query_analytics(["total_usage"], ["model"], "hour", "a", "b")
    _, _, _, _, body = session.calls[0]
    assert body["limit"] > 1000
    assert body["limit"] <= API_MAX_ANALYTICS_LIMIT


def test_default_row_limit_does_not_exceed_api_maximum():
    # This is the exact regression that broke live collection: the client
    # shipped with DEFAULT_ROW_LIMIT = 50000 against an API that rejects
    # anything above 10000 with a 400, so both live analytics queries
    # failed and the add-on ingested zero events. The mock never caught it
    # because it accepted any limit; this test pins the invariant directly
    # against the constant, independent of the mock's own behavior.
    assert DEFAULT_ROW_LIMIT <= API_MAX_ANALYTICS_LIMIT


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
    # Pagination terminates on empty page, not short page. An actual server
    # response may have any page size; old code guessing 100 could truncate.
    page1 = {"data": [{"hash": "a"}] * 100}
    page2 = {"data": [{"hash": "b"}]}
    page3 = {"data": []}  # Empty page signals end
    session = FakeSession([
        FakeResponse(200, page1),
        FakeResponse(200, page2),
        FakeResponse(200, page3),
    ])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    keys = client.list_keys()
    assert len(keys) == 101
    assert session.calls[0][3]["offset"] == 0
    assert session.calls[1][3]["offset"] == 100
    assert session.calls[2][3]["offset"] == 101
    # Three calls: two full pages, one empty
    assert len(session.calls) == 3


def test_list_keys_stops_on_empty_page():
    # Pagination terminates precisely when the server returns an empty page.
    page1 = {"data": [{"hash": "a"}]}
    page2 = {"data": []}  # Empty signals end
    session = FakeSession([FakeResponse(200, page1), FakeResponse(200, page2)])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    keys = client.list_keys()
    assert len(keys) == 1
    # Two calls: one with data, one empty to confirm end
    assert len(session.calls) == 2


def test_list_keys_small_page_size_no_truncation():
    # If OpenRouter's actual page size is smaller than 100, the old code
    # would truncate. Empty-page termination works for any page size.
    page1 = {"data": [{"hash": str(i)} for i in range(20)]}
    page2 = {"data": [{"hash": str(i)} for i in range(20, 40)]}
    page3 = {"data": []}  # Empty signals end
    session = FakeSession([
        FakeResponse(200, page1),
        FakeResponse(200, page2),
        FakeResponse(200, page3),
    ])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    keys = client.list_keys()
    assert len(keys) == 40
    # Three calls: two pages of 20, one empty
    assert len(session.calls) == 3


def test_list_keys_page_cap_raises_error():
    # Guard against runaway pagination (server bug or infinite iteration).
    # The cap is a safety valve, not a documented limit.
    def infinite_page_generator():
        # Yield full pages forever
        for i in range(10000):
            yield FakeResponse(200, {"data": [{"hash": str(j)} for j in range(100)]})

    session = FakeSession(list(infinite_page_generator()))
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    # Should raise on page cap, not return truncated list
    with pytest.raises(OpenRouterAPIError):
        client.list_keys()


def test_list_keys_includes_disabled_by_default():
    session = FakeSession([FakeResponse(200, {"data": []})])
    client = OpenRouterClient("k", session=session, retry_backoff=0)
    client.list_keys()
    assert session.calls[0][3]["include_disabled"] == "true"

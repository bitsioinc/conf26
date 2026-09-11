import pytest
from anthropic_client import AnthropicAdminClient, AnthropicAPIError


class StubResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class StubSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        return self.responses.pop(0)


PAGE1 = {"data": [{"starting_at": "2026-07-28T14:00:00Z", "ending_at": "2026-07-28T15:00:00Z",
                   "results": []}], "has_more": True, "next_page": "page_demo_2"}
PAGE2 = {"data": [], "has_more": False, "next_page": None}


def make(responses):
    session = StubSession(responses)
    return AnthropicAdminClient("sk-ant-admin01-x", base_url="http://mock", session=session), session


def test_sends_auth_headers_and_params():
    client, session = make([StubResponse(200, PAGE2)])
    list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z",
                                  group_by=["model", "workspace_id"]))
    call = session.calls[0]
    assert call["headers"]["x-api-key"] == "sk-ant-admin01-x"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["params"]["group_by[]"] == ["model", "workspace_id"]
    assert call["url"] == "http://mock/v1/organizations/usage_report/messages"


def test_follows_next_page():
    client, session = make([StubResponse(200, PAGE1), StubResponse(200, PAGE2)])
    pages = list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z"))
    assert len(pages) == 2
    assert session.calls[1]["params"]["page"] == "page_demo_2"


def test_retries_once_on_429_then_succeeds():
    client, _ = make([StubResponse(429, {}), StubResponse(200, PAGE2)])
    assert len(list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z"))) == 1


def test_raises_after_second_failure():
    client, _ = make([StubResponse(500, {}), StubResponse(500, {})])
    with pytest.raises(AnthropicAPIError):
        list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z"))


def test_list_api_keys_flattens_after_id_pages():
    keys_p1 = {"data": [{"id": "apikey_a"}], "has_more": True, "first_id": "apikey_a", "last_id": "apikey_a"}
    keys_p2 = {"data": [{"id": "apikey_b"}], "has_more": False, "first_id": "apikey_b", "last_id": "apikey_b"}
    client, session = make([StubResponse(200, keys_p1), StubResponse(200, keys_p2)])
    keys = client.list_api_keys()
    assert [k["id"] for k in keys] == ["apikey_a", "apikey_b"]
    assert session.calls[1]["params"]["after_id"] == "apikey_a"


# ---- Additional coverage beyond the plan's baseline five ------------------
# These lock down the invariants the orchestrator flagged as critical:
# report pagination uses `page`, directory pagination uses `after_id`,
# and cost reports hit the cost endpoint with a 1d bucket.


def test_cost_report_endpoint_and_bucket_width():
    client, session = make([StubResponse(200, PAGE2)])
    list(client.iter_cost_report("2026-07-27T00:00:00Z", "2026-07-29T00:00:00Z",
                                 group_by=["workspace_id", "description"]))
    call = session.calls[0]
    assert call["url"] == "http://mock/v1/organizations/cost_report"
    assert call["params"]["bucket_width"] == "1d"
    assert call["params"]["group_by[]"] == ["workspace_id", "description"]


def test_report_pagination_does_not_use_after_id():
    client, session = make([StubResponse(200, PAGE1), StubResponse(200, PAGE2)])
    list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z"))
    assert "after_id" not in session.calls[1]["params"]
    assert "page" not in session.calls[0]["params"]


def test_directory_pagination_does_not_use_page():
    users_p1 = {"data": [{"id": "user_a"}], "has_more": True, "last_id": "user_a"}
    users_p2 = {"data": [{"id": "user_b"}], "has_more": False, "last_id": "user_b"}
    client, session = make([StubResponse(200, users_p1), StubResponse(200, users_p2)])
    users = client.list_users()
    assert [u["id"] for u in users] == ["user_a", "user_b"]
    assert session.calls[0]["url"] == "http://mock/v1/organizations/users"
    assert "page" not in session.calls[1]["params"]
    assert session.calls[1]["params"]["after_id"] == "user_a"


def test_usage_report_default_bucket_width_is_hourly():
    client, session = make([StubResponse(200, PAGE2)])
    list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z"))
    assert session.calls[0]["params"]["bucket_width"] == "1h"
    assert "group_by[]" not in session.calls[0]["params"]


def test_error_carries_status_code():
    session = StubSession([StubResponse(503, {"error": "unavailable"}),
                           StubResponse(503, {"error": "unavailable"})])
    client = AnthropicAdminClient("sk-ant-admin01-x", base_url="http://mock",
                                  session=session, retry_backoff=0)
    with pytest.raises(AnthropicAPIError) as excinfo:
        list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z"))
    assert excinfo.value.status_code == 503
    assert len(session.calls) == 2  # original + exactly one retry


def test_non_retryable_4xx_is_not_retried():
    # 403 is not 429 and not >=500, so only ONE request should be made.
    session = StubSession([StubResponse(403, {"error": "forbidden"})])
    client = AnthropicAdminClient("sk-ant-admin01-x", base_url="http://mock", session=session)
    with pytest.raises(AnthropicAPIError):
        list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z"))
    assert len(session.calls) == 1

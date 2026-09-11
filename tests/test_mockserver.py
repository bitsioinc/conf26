from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from mockserver.app import app

client = TestClient(app)
AUTH = {"x-api-key": "sk-ant-admin01-fake", "anthropic-version": "2023-06-01"}

USAGE = "/v1/organizations/usage_report/messages"
COST = "/v1/organizations/cost_report"


def parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fmt(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def get(path, **params):
    resp = client.get(path, params=params, headers=AUTH)
    assert resp.status_code == 200, resp.text
    return resp.json()


def usage_buckets(**params):
    """Both usage pages, walked the way anthropic_client does."""
    p1 = get(USAGE, **params)
    p2 = get(USAGE, page=p1["next_page"], **params) if p1["has_more"] else {"data": []}
    return p1["data"] + p2["data"]


def test_rejects_missing_api_key():
    assert client.get(USAGE).status_code == 401


def test_usage_pagination_chain():
    p1 = client.get(USAGE, headers=AUTH).json()
    assert p1["has_more"] is True
    p2 = client.get(USAGE, params={"page": p1["next_page"]}, headers=AUTH).json()
    assert p2["has_more"] is False


def test_timestamps_are_shifted_to_now():
    newest = max(b["ending_at"] for b in usage_buckets())
    top_of_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    assert abs(parse(newest) - top_of_hour) <= timedelta(hours=1)


def test_directory_endpoints():
    keys = client.get("/v1/organizations/api_keys", headers=AUTH).json()
    users = client.get("/v1/organizations/users", headers=AUTH).json()
    assert {k["id"] for k in keys["data"]} == {"apikey_demo_ci", "apikey_demo_claudecode"}
    assert len(users["data"]) == 2


# ---- the re-anchor must be a process-lifetime constant --------------------


def test_repeated_polls_are_byte_identical():
    # The whole point: an hourly input polling the same window must not see the
    # data re-stamped to "now" each time, or the rogue key's tokens get
    # multiplied by the number of polls.
    window = {"starting_at": "2000-01-01T00:00:00Z", "ending_at": "2099-01-01T00:00:00Z"}
    first = client.get(USAGE, params=window, headers=AUTH)
    second = client.get(USAGE, params=window, headers=AUTH)
    assert first.content == second.content
    assert client.get(COST, params=window, headers=AUTH).content == \
        client.get(COST, params=window, headers=AUTH).content


def test_no_endpoint_reads_the_clock(monkeypatch):
    # Stronger than polling twice a second apart: move the module's clock a day
    # forward *after* import. If any handler still re-anchored per request the
    # buckets would jump; they must not budge.
    window = {"starting_at": "2000-01-01T00:00:00Z", "ending_at": "2099-01-01T00:00:00Z"}
    before = client.get(USAGE, params=window, headers=AUTH).content

    class FrozenLater(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=1, hours=3)

    monkeypatch.setattr("mockserver.app.datetime", FrozenLater)
    assert client.get(USAGE, params=window, headers=AUTH).content == before
    assert FrozenLater.now(timezone.utc) - datetime.now(timezone.utc) > timedelta(days=1)


def test_unbounded_request_still_returns_every_bucket():
    assert len(usage_buckets()) == 3
    assert len(get(COST)["data"]) == 2


# ---- starting_at / ending_at are honoured ---------------------------------


def test_narrow_window_returns_fewer_buckets():
    everything = usage_buckets()
    assert len(everything) == 3
    newest = max(parse(b["ending_at"]) for b in everything)
    narrow = usage_buckets(starting_at=fmt(newest - timedelta(hours=1)),
                           ending_at=fmt(newest))
    assert len(narrow) == 1
    assert narrow[0]["ending_at"] == fmt(newest)


def test_window_excludes_partially_overlapping_buckets():
    newest = max(parse(b["ending_at"]) for b in usage_buckets())
    # Window ends half an hour before the last bucket closes -> that bucket is
    # only partially covered and must not be returned.
    clipped = usage_buckets(starting_at=fmt(newest - timedelta(days=7)),
                            ending_at=fmt(newest - timedelta(minutes=30)))
    assert all(parse(b["ending_at"]) <= newest - timedelta(minutes=30) for b in clipped)
    assert len(clipped) == 2


def test_window_past_the_newest_bucket_is_empty():
    # This is the checkpoint case: once the input has ingested up to `newest`,
    # the next poll asks for (newest, newest+1h) and must get nothing back.
    newest = max(parse(b["ending_at"]) for b in usage_buckets())
    assert usage_buckets(starting_at=fmt(newest),
                         ending_at=fmt(newest + timedelta(hours=1))) == []
    assert get(COST, starting_at=fmt(newest), ending_at=fmt(newest + timedelta(days=1)))["data"] == []


def test_empty_page1_still_advertises_page2():
    # A window may empty page 1 while page 2 still matches; has_more must stay
    # true or the client stops early and loses the rogue key.
    newest = max(parse(b["ending_at"]) for b in usage_buckets())
    window = {"starting_at": fmt(newest - timedelta(hours=1)), "ending_at": fmt(newest)}
    p1 = get(USAGE, **window)
    assert p1["data"] == [] and p1["has_more"] is True
    p2 = get(USAGE, page=p1["next_page"], **window)
    assert len(p2["data"]) == 1


def test_bad_timestamp_is_a_400():
    assert client.get(USAGE, params={"starting_at": "not-a-date"},
                      headers=AUTH).status_code == 400


# ---- shape of the anchored data ------------------------------------------


def test_usage_lands_inside_the_dashboard_7d_window():
    now = datetime.now(timezone.utc)
    for b in usage_buckets():
        assert now - timedelta(days=7) < parse(b["starting_at"]) <= now


def test_cost_buckets_stay_midnight_aligned_and_inside_30d():
    now = datetime.now(timezone.utc)
    for b in get(COST)["data"]:
        for edge in (parse(b["starting_at"]), parse(b["ending_at"])):
            assert (edge.hour, edge.minute, edge.second) == (0, 0, 0), b
            assert now - timedelta(days=30) < edge <= now


def test_rogue_key_survives_the_shift_and_is_absent_from_the_directory():
    served = {r["api_key_id"] for b in usage_buckets() for r in b["results"]}
    directory = {k["id"] for k in get("/v1/organizations/api_keys")["data"]}
    assert "apikey_rogue_demo" in served
    assert "apikey_rogue_demo" not in directory

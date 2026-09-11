import json
from datetime import datetime, timezone
from pathlib import Path

from openrouter_keys_helper import SOURCETYPE, collect_keys

NOW = datetime(2026, 7, 31, 12, 0, 0, tzinfo=timezone.utc)

# Same fixtures directory the rest of the suite uses (see
# test_openrouter_fixtures.py) -- fixtures live under the renamed
# openrouter_mockserver package, not a top-level mockserver/.
FIXTURES = Path(__file__).resolve().parent.parent / "openrouter_mockserver" / "fixtures"

#: Currency fields per schema-verification.md's VERIFIED /keys field list.
#: Kept local to this test (rather than importing KEY_CURRENCY_FIELDS) so the
#: test still catches a drift between openrouter_transform's list and the
#: real fixture even if someone edits KEY_CURRENCY_FIELDS itself.
REAL_KEY_CURRENCY_FIELDS = (
    "usage", "usage_daily", "usage_weekly", "usage_monthly",
    "byok_usage", "byok_usage_daily", "byok_usage_weekly", "byok_usage_monthly",
    "limit", "limit_remaining",
)


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


def test_collect_against_real_keys_fixture():
    # The brief's own tests all use tiny hand-made dicts. This drives
    # collect_keys against the real, VERIFIED keys.json fixture (see
    # schema-verification.md's "/keys Field List" section: 21 fields per
    # key, currency fields already numeric on the wire) so a drift between
    # KEY_CURRENCY_FIELDS and what the API actually sends would fail a test
    # instead of shipping silently.
    real_keys = json.loads((FIXTURES / "keys.json").read_text())["data"]
    assert len(real_keys) >= 2, "fixture should hold at least the two real keys"

    client = StubClient(real_keys)
    events = collect_keys(client, NOW)

    assert len(events) == len(real_keys)
    for event in events:
        body = event["data"]
        # Every real key carries hash/name/label -- the roster's identity
        # fields -- untouched.
        assert body["hash"]
        assert body["name"]
        assert body["label"]
        # Every real key has usage -> usage_usd, numeric, not renamed away
        # to a string or dropped.
        assert "usage" not in body
        assert isinstance(body["usage_usd"], (int, float))
        # Every currency field observed on the real fixture must survive the
        # _usd rename, still numeric -- this is the drift check.
        for field in REAL_KEY_CURRENCY_FIELDS:
            usd_field = field + "_usd"
            assert usd_field in body, "missing {} on a real key".format(usd_field)
            assert isinstance(body[usd_field], (int, float)), (
                "{} was not coerced to numeric on the wire as expected".format(usd_field)
            )
        assert body["snapshot_at"] == "2026-07-31T12:00:00Z"

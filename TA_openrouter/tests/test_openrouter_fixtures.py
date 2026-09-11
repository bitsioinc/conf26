import json
from collections import defaultdict
from pathlib import Path

# The package was renamed to openrouter_mockserver to avoid a collision with
# the sibling TA_anthropic add-on's own `mockserver/` directory. Fixtures
# live under that renamed package, not under a top-level `mockserver/`.
FIXTURES = Path(__file__).resolve().parent.parent / "openrouter_mockserver" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def rows(name):
    return load(name)["data"]["data"]


def test_key_roster_has_at_least_two_sanctioned_keys():
    # Two, not three: the roster holds *only* sanctioned keys. The unrecognised
    # key is deliberately absent from it -- that absence is the shadow-AI
    # reveal, asserted by test_one_key_is_absent_from_the_roster below. The
    # earlier "two sanctioned plus one unrecognised" wording described a roster
    # that never existed.
    #
    # Two is the floor because a one-key baseline makes the Key Governance
    # lookup trivially satisfiable and stops exercising the join at all.
    data = load("keys.json")["data"]
    assert len(data) >= 2, "need at least two sanctioned keys in the roster"
    assert all("hash" in k for k in data)


def test_attribution_fixture_has_multiple_keys_and_models():
    data = rows("analytics_by_key_model.json")
    assert len({r["api_key_id"] for r in data}) >= 3
    assert len({r["model"] for r in data}) >= 2


def test_one_key_is_absent_from_the_roster():
    # The shadow-AI reveal needs a key that appears in analytics but not in
    # the roster the baseline search will be built from.
    #
    # VERIFIED (schema-verification.md): analytics rows' `api_key_id` joins
    # against the roster's `name` field, not its `hash` -- the analytics
    # endpoint never emits a hash value. Building the roster set from `hash`
    # would make this assertion pass vacuously (every api_key_id is trivially
    # absent from a set of hashes) even if the shadow key were removed, since
    # the sets could never intersect either way. The roster must be built
    # from `name` for this test to actually exercise the shadow-AI reveal.
    roster = {k["name"] for k in load("keys.json")["data"]}
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
    # VERIFIED (schema-verification.md): request_count comes back as a JSON
    # string (e.g. "3"), not a number -- `counts[combo] += row["request_count"]`
    # would concatenate strings instead of summing and then raise TypeError
    # the moment it's divided into a float. total_usage is already a float.
    usage = defaultdict(float)
    counts = defaultdict(int)
    for row in rows("analytics_by_model_provider.json"):
        combo = (row["model"], row["provider"])
        usage[combo] += row["total_usage"]
        counts[combo] += int(row["request_count"])
    per_request = defaultdict(list)
    for combo, spend in usage.items():
        if counts[combo]:
            per_request[combo[0]].append(round(spend / counts[combo], 8))
    assert any(len(set(v)) > 1 for v in per_request.values()), \
        "every provider costs the same; the comparison panel has no signal"


def test_analytics_fixtures_span_enough_buckets_for_the_alert():
    # streamstats window=6 needs a reference set before the spike.
    #
    # VERIFIED (schema-verification.md): the two analytics endpoints use
    # different time-bucket column names for the same conceptual value --
    # `date__hour` for the [api_key_id, model] query, `created_at__hour` for
    # the [model, provider] query. There is no `date` column in either
    # fixture; iterating with `r["date"]` raises KeyError on the first row.
    time_field_by_fixture = {
        "analytics_by_key_model.json": "date__hour",
        "analytics_by_model_provider.json": "created_at__hour",
    }
    for name, time_field in time_field_by_fixture.items():
        assert len({r[time_field] for r in rows(name)}) >= 7, name

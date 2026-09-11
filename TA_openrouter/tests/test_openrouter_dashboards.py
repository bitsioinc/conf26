# Uses stdlib ElementTree deliberately. The usual objection -- XXE and
# billion-laughs -- requires untrusted input; these are dashboard files
# authored in this repo and read only by this test. Switching to defusedxml
# would add a dependency to the repo-root requirements-dev.txt, which the
# no-modification-outside-TA_openrouter constraint forbids.
#
# This module also field-checks every panel query against the sourcetype it
# actually searches (see check_query_fields below). That check exists
# because of a documented near-miss: an earlier draft of the Key Governance
# dashboard's task brief joined the sanctioned-key baseline lookup on a
# field ("hash") that never matches analytics traffic, since
# openrouter:analytics' api_key_id carries the key's *name*, not its hash
# (verified against a live recording -- see docs/schema-verification.md and
# package/default/savedsearches.conf). That kind of bug parses fine, uses
# the index macro correctly, and sums no rate metric -- the first three
# tests below would all pass on the broken version. Only a field-existence
# check across the whole pipeline (stats aliases, eval, lookup outputs, by
# clauses) catches it.
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

from openrouter_analytics_helper import QUERIES
from openrouter_transform import flatten_analytics, flatten_keys

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "package" / "default" / "data" / "ui"
VIEWS = UI / "views"
NAV = UI / "nav" / "default.xml"
FIXTURES = ROOT / "openrouter_mockserver" / "fixtures"
BASELINE_LOOKUP = ROOT / "package" / "lookups" / "openrouter_key_baseline.csv"

EXPECTED = {
    "openrouter_model_mix.xml",
    "openrouter_provider_routing.xml",
    "openrouter_key_governance.xml",
}


def _load_fixture(name):
    with open(FIXTURES / name) as f:
        return json.load(f)


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


def test_every_dashboard_has_a_time_picker_and_no_panel_hardcodes_24h():
    # Regression guard for the time-range picker. Before this feature, every
    # panel hardcoded <earliest>-24h</earliest><latest>now</latest> with no
    # way for an operator to widen the range without editing XML -- and on
    # the live instance the only analytics events are ~79 hours old, so
    # Model Mix & Spend and Provider Routing rendered blank at -24h even
    # though the data was indexed and correct. Each dashboard must now
    # expose a dashboard-level <input type="time"> bound to a token, every
    # panel's <earliest>/<latest> must reference that token (not a literal),
    # and the default must be Last 7 days (-7d@d), not Last 24 hours.
    for path in VIEWS.glob("*.xml"):
        tree = ET.parse(path)
        root = tree.getroot()
        assert root.tag == "form", (
            "{}: classic Simple XML requires a <form> root (not <dashboard>) "
            "to host a <fieldset>/<input>".format(path.name)
        )

        time_inputs = [inp for inp in root.iter("input") if inp.get("type") == "time"]
        assert time_inputs, "{} has no <input type=\"time\">".format(path.name)
        token = time_inputs[0].get("token")
        assert token, "{}: time input has no token".format(path.name)

        default = time_inputs[0].find("default")
        assert default is not None, "{}: time input has no <default>".format(path.name)
        assert default.find("earliest").text == "-7d@d", (
            "{}: time picker must default to Last 7 days (-7d@d), not -24h -- "
            "the live account's traffic is sparse enough that -24h is "
            "routinely empty".format(path.name)
        )
        assert default.find("latest").text == "now"

        for query in root.iter("query"):
            assert "-24h" not in query.text, "{}: {}".format(path.name, query.text[:80])

        panel_searches = [
            search for panel in root.iter("panel") for search in panel.iter("search")
        ]
        assert panel_searches, "{}: no panel searches found".format(path.name)
        for search in panel_searches:
            earliest = search.find("earliest")
            latest = search.find("latest")
            assert earliest is not None and latest is not None, (
                "{}: a panel search is missing earliest/latest".format(path.name))
            assert earliest.text == "${}.earliest$".format(token), (
                "{}: panel earliest does not follow the time picker token: {}"
                .format(path.name, earliest.text))
            assert latest.text == "${}.latest$".format(token), (
                "{}: panel latest does not follow the time picker token: {}"
                .format(path.name, latest.text))


# ---------------------------------------------------------------------------
# Field-existence checking.
#
# Ground truth for "what fields actually exist on this sourcetype" is
# derived by running the shipped transform (openrouter_transform, via the
# real QUERIES table in openrouter_analytics_helper) over the committed
# fixtures -- the same fixtures the mock server replays and the same
# dimensions/time_field the modular input actually requests. This is not a
# hand-typed guess: if a future change to openrouter_transform.py or the
# fixtures adds, renames, or removes a field, this allowlist moves with it
# automatically instead of silently drifting out of sync with reality.
# ---------------------------------------------------------------------------

SPLUNK_INTERNAL_FIELDS = {
    "_time", "_raw", "_indextime", "host", "source", "sourcetype", "index",
    "linecount", "splunk_server",
}


def _event_fields(events):
    fields = set()
    for event in events:
        fields.update(event["data"].keys())
    return fields


def _fields_for_sourcetype(sourcetype):
    query = next(q for q in QUERIES if q["sourcetype"] == sourcetype)
    payload = _load_fixture({
        "openrouter:analytics": "analytics_by_key_model.json",
        "openrouter:providers": "analytics_by_model_provider.json",
    }[sourcetype])
    events = flatten_analytics(payload, query["dimensions"],
                                time_field=query["time_field"])
    return _event_fields(events) | SPLUNK_INTERNAL_FIELDS


_keys_events = flatten_keys(_load_fixture("keys.json")["data"],
                             "2026-08-01T00:00:00Z")

SOURCETYPE_FIELDS = {
    "openrouter:analytics": _fields_for_sourcetype("openrouter:analytics"),
    "openrouter:providers": _fields_for_sourcetype("openrouter:providers"),
    "openrouter:keys": _event_fields(_keys_events) | SPLUNK_INTERNAL_FIELDS,
}

# Sanity checks on the derivation itself -- if these ever fail, the
# allowlists below are no longer trustworthy and every downstream assertion
# in this module is meaningless.
assert "api_key_id" in SOURCETYPE_FIELDS["openrouter:analytics"]
assert "api_key_id" not in SOURCETYPE_FIELDS["openrouter:providers"], (
    "openrouter:providers must never carry api_key_id")
assert "total_usage_usd" in SOURCETYPE_FIELDS["openrouter:analytics"]
assert "total_usage" not in SOURCETYPE_FIELDS["openrouter:analytics"], (
    "currency fields are _usd-suffixed on the indexed event; the bare name "
    "does not exist")
assert "hash" in SOURCETYPE_FIELDS["openrouter:keys"]
assert "usage_usd" in SOURCETYPE_FIELDS["openrouter:keys"]

# Columns of the sanctioned-key baseline lookup (package/lookups/
# openrouter_key_baseline.csv's header), used to validate `lookup ...
# OUTPUT <field>` clauses against the lookup table's own schema, not just
# the searched sourcetype's.
LOOKUP_FIELDS = {
    "openrouter_key_baseline": set(
        BASELINE_LOOKUP.read_text().splitlines()[0].split(",")
    ),
}


# --- a small, deliberately narrow SPL field-flow checker -------------------
#
# This is not a general SPL parser. It understands exactly the commands used
# by the three dashboards shipped in this task (stats/timechart/eventstats,
# eval with a single assignment, where, lookup ... OUTPUT, table, sort) and
# nothing more. Its job is narrow: track which field names are valid to
# reference at each point in a pipeline (starting from the real sourcetype
# fields, growing as stats/eval/lookup introduce new aliases) and fail loudly
# on anything referenced before it exists. A query using a command this
# checker doesn't understand raises AssertionError with a clear message
# rather than silently passing -- see the `else` branch below.

_AGG_CALL = re.compile(r"\b([A-Za-z_]\w*)\(([A-Za-z_]\w*)\)(?:\s+[Aa][Ss]\s+(\w+))?")
_BY_CLAUSE = re.compile(r"\bby\s+(.+)$", re.IGNORECASE)
_EVAL_ASSIGN = re.compile(r"^eval\s+(\w+)\s*=\s*(.+)$", re.IGNORECASE | re.DOTALL)
_WHERE = re.compile(r"^where\s+(.+)$", re.IGNORECASE | re.DOTALL)
_SORT = re.compile(r"^sort\s+(.+)$", re.IGNORECASE)
_TABLE = re.compile(r"^table\s+(.+)$", re.IGNORECASE)
_LOOKUP = re.compile(
    r"^lookup\s+(\S+)\s+(\w+)\s+OUTPUT(?:NEW)?\s+(\w+)(?:\s+[Aa][Ss]\s+(\w+))?",
    re.IGNORECASE,
)
_STATSLIKE = re.compile(r"^(stats|timechart|eventstats|streamstats)\b", re.IGNORECASE)
_STATSLIKE_FUNCS = {"stats", "timechart", "eventstats", "streamstats"}
_SPL_NONFIELD_TOKENS = {
    "round", "isnull", "isnotnull", "coalesce", "if", "case", "abs", "len",
    "lower", "upper", "now",
}


def _identifiers(expr):
    return {
        tok for tok in re.findall(r"[A-Za-z_]\w*", expr)
        if tok not in _SPL_NONFIELD_TOKENS
    }


def check_query_fields(query):
    """Raise AssertionError if any field is referenced before it exists.

    Walks the pipeline left to right starting from the real fields of the
    sourcetype the query searches (per SOURCETYPE_FIELDS), adding aliases as
    stats/eval/lookup commands introduce them. This is what would have
    caught a join on the wrong lookup field, or a `sum(total_usage)` where
    only `total_usage_usd` exists on the event: both parse fine and neither
    sums a rate metric, so the other three tests in this module cannot see
    them.
    """
    segments = [seg.strip() for seg in query.split("|")]

    sourcetype = None
    for seg in segments:
        m = re.search(r"sourcetype=(openrouter:\w+)", seg)
        if m:
            sourcetype = m.group(1)
            break
    assert sourcetype in SOURCETYPE_FIELDS, (
        "query does not search a known openrouter sourcetype: {}".format(query[:80])
    )
    known = set(SOURCETYPE_FIELDS[sourcetype])

    for seg in segments:
        if "sourcetype=" in seg and "|" not in seg and seg == segments[0]:
            continue  # the macro + sourcetype-filter segment itself

        if _STATSLIKE.match(seg):
            by_match = _BY_CLAUSE.search(seg)
            agg_part = seg[:by_match.start()] if by_match else seg
            if by_match:
                for field in by_match.group(1).split():
                    assert field in known, (
                        "unknown field {!r} in by-clause: {}".format(field, seg))
            new_aliases = set()
            for func, field, alias in _AGG_CALL.findall(agg_part):
                if func.lower() in _STATSLIKE_FUNCS:
                    continue
                assert field in known, (
                    "unknown field {!r} in {}(...): {}".format(field, func, seg))
                if alias:
                    new_aliases.add(alias)
            known |= new_aliases

        elif re.match(r"^eval\b", seg, re.IGNORECASE):
            m = _EVAL_ASSIGN.match(seg)
            assert m, "eval clause not understood by this test's SPL checker: {}".format(seg)
            alias, expr = m.group(1), m.group(2)
            for ident in _identifiers(expr):
                assert ident in known, (
                    "unknown field {!r} in eval: {}".format(ident, seg))
            known.add(alias)

        elif re.match(r"^where\b", seg, re.IGNORECASE):
            m = _WHERE.match(seg)
            for ident in _identifiers(m.group(1)):
                assert ident in known, (
                    "unknown field {!r} in where: {}".format(ident, seg))

        elif re.match(r"^lookup\b", seg, re.IGNORECASE):
            m = _LOOKUP.match(seg)
            assert m, "lookup clause not understood by this test's SPL checker: {}".format(seg)
            lookup_name, key_field, out_field, alias = m.groups()
            assert key_field in known, (
                "unknown join field {!r} in lookup: {}".format(key_field, seg))
            if lookup_name in LOOKUP_FIELDS:
                assert out_field in LOOKUP_FIELDS[lookup_name], (
                    "lookup {} has no field {!r}: {}".format(lookup_name, out_field, seg))
            known.add(alias or out_field)

        elif re.match(r"^table\b", seg, re.IGNORECASE):
            m = _TABLE.match(seg)
            for field in m.group(1).split():
                assert field in known, (
                    "unknown field {!r} in table: {}".format(field, seg))

        elif re.match(r"^sort\b", seg, re.IGNORECASE):
            m = _SORT.match(seg)
            for tok in m.group(1).split():
                field = tok.lstrip("+-")
                if not field or field == "0":
                    continue
                assert field in known, (
                    "unknown field {!r} in sort: {}".format(field, seg))

        else:
            raise AssertionError(
                "SPL command not understood by this test's checker (extend "
                "check_query_fields if this is a legitimate new command): {}".format(seg)
            )


def test_every_field_reference_exists_on_its_sourcetype():
    for path in VIEWS.glob("*.xml"):
        for query in ET.parse(path).iter("query"):
            check_query_fields(query.text)


# ---------------------------------------------------------------------------
# The specific regression this task exists to prevent: a join between
# openrouter:analytics' api_key_id and the sanctioned-key baseline lookup
# that can never match because it's keyed on the roster's hash instead of
# its name. Two checks: a structural one (the query text still says what it
# should) and a behavioral one (simulating the join against the real
# committed fixtures produces exactly Shadow_Intern_Key and none of the
# three sanctioned keys).
# ---------------------------------------------------------------------------


def _key_governance_query():
    tree = ET.parse(VIEWS / "openrouter_key_governance.xml")
    for panel in tree.iter("panel"):
        title = panel.find("title")
        if title is not None and title.text == "Unrecognised keys (active but not in baseline)":
            return panel.find(".//query").text
    raise AssertionError("Unrecognised keys panel not found")


def test_unrecognised_key_panel_joins_baseline_lookup_on_name():
    query = _key_governance_query()
    assert "lookup openrouter_key_baseline api_key_id OUTPUT name as baseline_name" in query, (
        "the baseline join must be keyed on name (api_key_id on both sides), "
        "not hash -- analytics api_key_id carries the key's name, never its "
        "hash (docs/schema-verification.md); the join text was:\n{}".format(query)
    )


def test_unrecognised_key_panel_surfaces_only_shadow_intern_key():
    # Simulates the panel's pipeline against the real committed fixtures:
    #   1. openrouter:keys -> sanctioned baseline, keyed by name (mirrors
    #      the "OpenRouter - Build API Key Baseline" saved search).
    #   2. openrouter:analytics -> distinct api_key_id values that produced
    #      traffic.
    #   3. lookup ... OUTPUT name as baseline_name | where isnull(baseline_name)
    #      == analytics api_key_id values with no match in the baseline.
    query = _key_governance_query()
    check_query_fields(query)  # same field-flow check as the general test

    keys_events = flatten_keys(_load_fixture("keys.json")["data"],
                                "2026-08-01T00:00:00Z")
    baseline_names = {e["data"]["name"] for e in keys_events}

    analytics_query = next(q for q in QUERIES if q["sourcetype"] == "openrouter:analytics")
    analytics_events = flatten_analytics(
        _load_fixture("analytics_by_key_model.json"),
        analytics_query["dimensions"],
        time_field=analytics_query["time_field"],
    )
    analytics_key_ids = {e["data"]["api_key_id"] for e in analytics_events}

    unrecognised = analytics_key_ids - baseline_names

    assert baseline_names == {"Alpaca", "Nimbus_Prod_Key"}
    assert unrecognised == {"Shadow_Intern_Key"}


# ---------------------------------------------------------------------------
# The double-counting regression this task exists to prevent: the original
# "Token volume by model" panel stacked tokens_prompt, tokens_completion,
# reasoning_tokens, and cached_tokens as four independent series, but
# reasoning_tokens/cached_tokens are components of tokens_completion/
# tokens_prompt, not additional categories -- prompt+completion already
# equals tokens_total in every committed fixture row. Stacking all four
# inflated the bar to ~1.8x actual tokens and silently disagreed with the
# "Top models" panel's own sum(tokens_total). Two checks: a behavioral one
# (the real transform, over the real fixture, proves the arithmetic) and a
# structural one (the shipped panel queries actually reflect that fix).
# ---------------------------------------------------------------------------


def _panel_query(view_name, title):
    tree = ET.parse(VIEWS / view_name)
    for panel in tree.iter("panel"):
        panel_title = panel.find("title")
        if panel_title is not None and panel_title.text == title:
            return panel.find(".//query").text
    raise AssertionError("{!r} panel not found in {}".format(title, view_name))


def test_token_volume_panel_stacks_only_disjoint_components():
    analytics_query = next(q for q in QUERIES if q["sourcetype"] == "openrouter:analytics")
    events = flatten_analytics(
        _load_fixture("analytics_by_key_model.json"),
        analytics_query["dimensions"],
        time_field=analytics_query["time_field"],
    )

    totals = defaultdict(lambda: defaultdict(int))
    for e in events:
        d = e["data"]
        model = d["model"]
        totals[model]["prompt"] += d["tokens_prompt"]
        totals[model]["completion"] += d["tokens_completion"]
        totals[model]["total"] += d["tokens_total"]
        totals[model]["reasoning"] += d["reasoning_tokens"]
        totals[model]["cached"] += d["cached_tokens"]

    assert totals, "fixture produced no events -- this test would pass vacuously"
    for model, agg in totals.items():
        stacked = agg["prompt"] + agg["completion"]
        assert stacked == agg["total"], (
            "{}: stacked Prompt+Completion ({}) != tokens_total ({}) -- the "
            "chart would silently disagree with the Top models panel's own "
            "sum(tokens_total)".format(model, stacked, agg["total"]))
        assert agg["cached"] <= agg["prompt"], (
            "{}: cached_tokens ({}) exceeds tokens_prompt ({}) -- cached is "
            "no longer a subset of prompt, so it must not be presented as a "
            "component".format(model, agg["cached"], agg["prompt"]))
        assert agg["reasoning"] <= agg["completion"], (
            "{}: reasoning_tokens ({}) exceeds tokens_completion ({}) -- "
            "reasoning is no longer a subset of completion, so it must not "
            "be presented as a component".format(
                model, agg["reasoning"], agg["completion"]))

    query = _panel_query("openrouter_model_mix.xml",
                          "Token volume by model (prompt + completion)")
    assert "sum(tokens_prompt) as Prompt" in query
    assert "sum(tokens_completion) as Completion" in query
    # Regression guard: reasoning_tokens/cached_tokens must never come back
    # into the stacked total -- that is exactly the defect this test exists
    # to catch.
    assert "reasoning_tokens" not in query
    assert "cached_tokens" not in query


def test_reasoning_and_cached_shown_as_separate_unstacked_panel():
    # Reasoning and Cached are still worth showing (docs/operations.md uses
    # cached_tokens/tokens_prompt as a cache-hit-rate proxy), but they must
    # not be stacked -- stacking them (with each other or with the panel
    # above) would visually imply they are additive volume, which the
    # fixture arithmetic in the previous test disproves.
    query = _panel_query(
        "openrouter_model_mix.xml",
        "Reasoning & cached tokens by model (components, not additional volume)")
    assert "sum(reasoning_tokens) as Reasoning" in query
    assert "sum(cached_tokens) as Cached" in query

    tree = ET.parse(VIEWS / "openrouter_model_mix.xml")
    for panel in tree.iter("panel"):
        title = panel.find("title")
        if title is not None and title.text == (
                "Reasoning & cached tokens by model (components, not additional volume)"):
            chart = panel.find("chart")
            stack_opt = chart.find("option[@name='charting.chart.stackMode']")
            assert stack_opt is None, (
                "components panel must not set charting.chart.stackMode=stacked")
            return
    raise AssertionError("components panel not found")

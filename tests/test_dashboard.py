# Uses stdlib ElementTree deliberately, matching TA_openrouter's dashboard
# test. The usual objection -- XXE and billion-laughs -- requires untrusted
# input; this is a dashboard file authored in this repo and read only by this
# test.
#
"""Field-checks for every panel in ai_observability.xml.

The failure mode this guards is the one `slim validate` and AppInspect cannot
see: a panel whose query parses, sums a real-looking metric, and returns
nothing forever because it names a field the transforms never emit. See
CLAUDE.md, "Failure modes that no gate catches".

Every field name a panel references is checked against what `flatten_usage`
and `flatten_cost` actually produce from the shipped fixtures, and the
shadow-AI timechart is additionally executed in miniature to prove it returns
rows rather than an empty chart.
"""
import csv
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import pytest

from anthropic_transform import flatten_usage, flatten_cost

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "mockserver" / "fixtures"
PACKAGE = ROOT / "TA_anthropic" / "package"
DASHBOARD = PACKAGE / "default" / "data" / "ui" / "views" / "ai_observability.xml"
BASELINE_CSV = PACKAGE / "lookups" / "anthropic_api_key_baseline.csv"

USAGE_PAGES = ("usage_report_page1.json", "usage_report_page2.json")

SHADOW_PANEL = "Output Tokens Over Time by Baseline Status"
TIME_TOKEN = "time_range"
COST_PANEL = "Daily Cost by Workspace (USD)"

# Names that legitimately appear in a query without being event fields:
# values the searches themselves create, and lookup output columns.
NOT_EVENT_FIELDS = {
    "input_tokens", "output_tokens", "usd", "cached", "total", "ratio",
    "input", "output", "series", "workspace", "name", "id", "status",
}


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(scope="module")
def panels():
    root = ET.parse(DASHBOARD).getroot()
    out = []
    for panel in root.findall(".//panel"):
        out.append((panel.find("title").text,
                    " ".join(panel.find(".//query").text.split())))
    assert out, "dashboard has no panels"
    return out


@pytest.fixture(scope="module")
def usage_fields():
    fields = set()
    for page in USAGE_PAGES:
        for event in flatten_usage(load(page)):
            fields |= set(event["data"].keys())
    return fields


@pytest.fixture(scope="module")
def cost_fields():
    fields = set()
    for event in flatten_cost(load("cost_report.json")):
        fields |= set(event["data"].keys())
    return fields


@pytest.fixture(scope="module")
def baseline_ids():
    with BASELINE_CSV.open() as fh:
        return {row["id"] for row in csv.DictReader(fh)}


def test_shadow_timechart_panel_is_present(panels):
    titles = [t for t, _ in panels]
    assert SHADOW_PANEL in titles, (
        "the shadow-AI timechart panel is missing. Slide 18 shows this chart; "
        "without the panel the deck claims a visualisation the add-on does not "
        "ship. Panels present: %r" % titles)


def test_dashboard_is_a_form_with_one_time_picker():
    """A fieldset requires a <form> root; <dashboard> silently drops it.

    This dashboard used to ship as <dashboard> with per-panel hard-coded
    ranges, while the demo instance ran a <form> copy saved from the UI into
    local/. The two diverged. The picker now lives here, in default/, and
    local/ is gone -- keep it that way.
    """
    root = ET.parse(DASHBOARD).getroot()
    assert root.tag == "form", (
        "classic Simple XML requires a <form> root (not <dashboard>) for the "
        "time picker's <fieldset> to render; got <%s>" % root.tag)
    inputs = root.findall("./fieldset/input")
    assert len(inputs) == 1, "expected exactly one time picker, got %d" % len(inputs)
    assert inputs[0].get("type") == "time"
    assert inputs[0].get("token") == TIME_TOKEN, (
        "the token is named %r elsewhere in this repo; a UI-saved dashboard "
        "names it field1, which is the tell that local/ has come back"
        % TIME_TOKEN)
    assert inputs[0].findtext("./label", "").strip(), (
        "an empty <label> renders as a blank control")


def test_every_panel_honours_the_time_picker():
    """A panel with a hard-coded range ignores the control above it.

    One documented exception, COST_PANEL -- see the test below it.
    """
    root = ET.parse(DASHBOARD).getroot()
    for panel in root.findall(".//panel"):
        title = panel.find("title").text
        if title == COST_PANEL:
            continue
        for bound in ("earliest", "latest"):
            value = panel.findtext(".//search/%s" % bound, "").strip()
            assert value == "$%s.%s$" % (TIME_TOKEN, bound), (
                "panel %r pins %s=%r instead of the picker token, so moving "
                "the control leaves this panel showing a different window "
                "from every other panel" % (title, bound, value))


def test_cost_panel_keeps_its_own_multi_day_window():
    """cost_report only releases COMPLETED daily buckets.

    The newest cost bucket is therefore always yesterday. Bound to the time
    picker, this panel renders "No results found" on Today -- not sometimes,
    always -- and it is the second panel walked on slide 17. It keeps a fixed
    multi-day window instead, and says so on the panel.
    """
    root = ET.parse(DASHBOARD).getroot()
    panel = next(p for p in root.findall(".//panel")
                 if p.find("title").text == COST_PANEL)

    earliest = panel.findtext(".//search/earliest", "").strip()
    assert TIME_TOKEN not in earliest, (
        "the cost panel must not follow the time picker; on any sub-day range "
        "it goes permanently empty")
    match = re.fullmatch(r"-(\d+)d@d", earliest)
    assert match, "expected a fixed -Nd@d earliest, got %r" % earliest
    assert int(match.group(1)) >= 2, (
        "the window must span at least two days to be certain of catching a "
        "completed daily bucket; got %r" % earliest)

    assert (panel.findtext("description") or "").strip(), (
        "a panel that deliberately ignores the picker needs a description "
        "saying why, or the next person 'fixes' it back")


def test_every_panel_sources_a_real_sourcetype(panels):
    for title, query in panels:
        assert re.search(r"sourcetype=anthropic:(usage|cost|api_keys|users)", query), (
            "panel %r does not constrain a known sourcetype" % title)


def test_panel_fields_exist_in_transform_output(panels, usage_fields, cost_fields):
    """Every field a panel reads must be one the transforms actually emit."""
    failures = []
    for title, query in panels:
        emitted = usage_fields if "anthropic:usage" in query else cost_fields
        referenced = set(re.findall(
            r"(?:sum|avg|max|min|dc|values)\(([A-Za-z_]\w*)\)", query))
        referenced |= set(re.findall(r"\bby ([A-Za-z_]\w*)", query))
        for field in sorted(referenced):
            if field in NOT_EVENT_FIELDS or field in emitted:
                continue
            failures.append(
                "panel %r reads %r, which no transform emits" % (title, field))
    assert not failures, "\n".join(failures)


def test_shadow_panel_joins_the_baseline_on_id(panels):
    query = dict(panels)[SHADOW_PANEL]
    assert "lookup anthropic_api_key_baseline id as api_key_id OUTPUT name" in query, (
        "the shadow panel must join the baseline lookup on id -> api_key_id; "
        "any other join silently returns nothing")
    assert "isnull(name)" in query, (
        "unrecognised keys are identified by a null lookup result, not a list")
    assert "timechart span=1h" in query, "the panel is a per-hour time series"


def test_shadow_panel_renders_both_series_stacked():
    root = ET.parse(DASHBOARD).getroot()
    panel = next(p for p in root.findall(".//panel")
                 if p.find("title").text == SHADOW_PANEL)
    opts = {o.get("name"): (o.text or "") for o in panel.findall(".//option")}
    assert opts.get("charting.chart") == "column"
    assert opts.get("charting.chart.stackMode") == "stacked", (
        "unstacked, the quiet hours and the spike sit side by side and the "
        "one-bar-dwarfs-everything read is lost")
    colours = opts.get("charting.fieldColors", "")
    assert "Key not in the baseline" in colours and "Known API keys" in colours, (
        "both series need explicit colours or Splunk assigns them per render "
        "order, and the shadow bar stops being the magenta one")


def test_shadow_panel_actually_returns_rows(baseline_ids):
    """Run the panel's logic in miniature against the shipped fixtures.

    A panel that returns nothing is the exact defect this module exists for,
    so assert the shape the slide claims: quiet known-key hours, then one
    bucket where an unrecognised key dwarfs them.
    """
    buckets = defaultdict(lambda: defaultdict(int))
    for page in USAGE_PAGES:
        for event in flatten_usage(load(page)):
            data = event["data"]
            if not data.get("api_key_id"):
                continue
            series = ("Known API keys" if data["api_key_id"] in baseline_ids
                      else "Key not in the baseline")
            hour = datetime.fromtimestamp(
                event["time"], timezone.utc).strftime("%Y-%m-%dT%H")
            buckets[hour][series] += data.get("output_tokens") or 0

    assert buckets, "the panel returns no rows at all"

    shadow_hours = [h for h, s in buckets.items() if s["Key not in the baseline"]]
    assert len(shadow_hours) == 1, (
        "expected exactly one hour of unrecognised-key traffic (the planted "
        "rogue key); got %r" % shadow_hours)

    loudest_known = max(s["Known API keys"] for s in buckets.values())
    assert buckets[shadow_hours[0]]["Key not in the baseline"] > 5 * loudest_known, (
        "the rogue key no longer dwarfs the known keys, so the chart stops "
        "making the point the slide makes")

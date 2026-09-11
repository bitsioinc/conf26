"""Regression guards for the two shipped alerts in savedsearches.conf.

The demo's headline beat is the token-spike alert firing on the rogue key's
hour. Nothing else in the suite covers that arithmetic, so a one-digit edit to
a fixture could silently disarm the alert with every other test still green.

These tests therefore re-run the alerts' SPL *in Python*:

  * the search string is read out of the shipped savedsearches.conf, never
    copied here, so swapping the alert out from under the test fails loudly;
  * the window size, sigma multiplier, absolute floor, bin span, aggregation
    field and result aliases are all *derived* from that search string, so the
    test tracks the conf instead of encoding a private opinion about it;
  * the token arithmetic comes from anthropic_transform.flatten_usage, the
    same function the modular input uses -- it is imported, never reimplemented.

Python 3.9 compatible (Splunk's app runtime is CPython 3.9.25).
"""
import configparser
import csv
import json
import re
import statistics
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from anthropic_transform import flatten_usage

ROOT = Path(__file__).parent.parent
FIXTURES = ROOT / "mockserver" / "fixtures"
PACKAGE = ROOT / "TA_anthropic" / "package"
SAVEDSEARCHES = PACKAGE / "default" / "savedsearches.conf"
LOOKUPS = PACKAGE / "lookups"

USAGE_PAGES = ("usage_report_page1.json", "usage_report_page2.json")

SPIKE_ALERT = "Anthropic - Token Spike Anomaly"
ROGUE_ALERT = "Anthropic - Unrecognized API Key"

#: How much headroom the spike must keep over its own trigger threshold before
#: this test starts complaining. The shipped fixtures clear it by ~17.9%.
MIN_MARGIN_PCT = 10.0

SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


# --------------------------------------------------------------------------
# Reading the shipped artefacts
# --------------------------------------------------------------------------

def load_fixture(name):
    """Read a mockserver fixture. Resolves FIXTURES at call time so a test can
    repoint it at a perturbed scratch copy to prove this file has teeth."""
    return json.loads((FIXTURES / name).read_text())


def load_lookup(name):
    """Read a shipped CSV lookup by its `lookup <name>` reference."""
    stem = name if name.endswith(".csv") else name + ".csv"
    with (LOOKUPS / stem).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_savedsearches():
    parser = configparser.ConfigParser(
        strict=False, interpolation=None, delimiters=("=",)
    )
    parser.optionxform = str  # Splunk conf keys are case sensitive
    with SAVEDSEARCHES.open(encoding="utf-8") as handle:
        parser.read_file(handle)
    return parser


def search_string(stanza):
    """The stanza's `search =` value, whitespace-normalised to one line."""
    conf = read_savedsearches()
    assert conf.has_section(stanza), (
        "savedsearches.conf has no [%s] stanza -- the alert this test guards "
        "was renamed or removed. Sections found: %s" % (stanza, conf.sections())
    )
    assert conf.has_option(stanza, "search"), "[%s] has no search=" % stanza
    return " ".join(conf.get(stanza, "search").split())


def _require(match, what, spl):
    assert match, (
        "Could not derive %s from the [%s] SPL. The search changed shape; "
        "update this test to match the new alert (do not weaken the alert to "
        "match the test).\nSPL: %s" % (what, SPIKE_ALERT, spl)
    )
    return match


# --------------------------------------------------------------------------
# Minimal SPL primitives
# --------------------------------------------------------------------------

def splunk_round(value, digits=0):
    """Splunk's round() is half-away-from-zero; Python's round() is banker's."""
    quantum = Decimal(1).scaleb(-digits)
    rounded = Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP)
    return int(rounded) if digits == 0 else float(rounded)


def bin_and_sum(field, span_seconds):
    """`bin _time span=<span> | stats sum(<field>) by _time | sort 0 _time`.

    Token arithmetic is delegated to flatten_usage -- this only buckets and adds.
    """
    buckets = {}
    for page in USAGE_PAGES:
        for event in flatten_usage(load_fixture(page)):
            value = event["data"].get(field)
            assert value is not None, (
                "flatten_usage does not produce %r, but the alert sums it. "
                "The SPL and anthropic_transform have drifted apart." % field
            )
            slot = int(event["time"] // span_seconds) * span_seconds
            buckets[slot] = buckets.get(slot, 0) + value
    return sorted(buckets.items())


def streamstats(values, window, include_current, stdev_kind):
    """`streamstats window=<n> current=<t|f> avg(x) stdev[p](x)`.

    Returns one (avg, sd) pair per input row; either may be None where Splunk
    would emit a null (empty window, or sample stdev of a single event).
    """
    out = []
    for index in range(len(values)):
        if include_current:
            frame = values[max(0, index + 1 - window):index + 1]
        else:
            frame = values[max(0, index - window):index]
        if not frame:
            out.append((None, None))
            continue
        avg = statistics.fmean(frame)
        if stdev_kind == "stdevp":
            sd = statistics.pstdev(frame)
        else:
            sd = statistics.stdev(frame) if len(frame) > 1 else None
        out.append((avg, sd))
    return out


# --------------------------------------------------------------------------
# [Anthropic - Token Spike Anomaly]
# --------------------------------------------------------------------------

def parse_spike_search(spl):
    """Derive every tunable in the spike alert from its own search string."""
    span = _require(
        re.search(r"\bbin\s+_time\s+span=(\d+)([smhd])\b", spl), "the bin span", spl
    )
    stats = _require(
        re.search(r"\bstats\s+sum\((\w+)\)\s+as\s+(\w+)\s+by\s+_time\b", spl),
        "the stats aggregation", spl,
    )
    stream = _require(
        re.search(
            r"\bstreamstats\s+window=(\d+)\s+current=([ft])\s+"
            r"avg\((\w+)\)\s+as\s+(\w+)\s+(stdevp|stdev)\((\w+)\)\s+as\s+(\w+)",
            spl,
        ),
        "the streamstats baseline", spl,
    )
    tokens, baseline, sd = stats.group(2), stream.group(4), stream.group(7)
    assert stream.group(3) == tokens and stream.group(6) == tokens, (
        "streamstats baselines %r/%r but stats produced %r"
        % (stream.group(3), stream.group(6), tokens)
    )
    where = _require(
        re.search(
            r"\|\s*where\s+{sd}\s*>\s*0\s+AND\s+{tok}\s*>\s*(\d+)\s+AND\s+"
            r"{tok}\s*>\s*{base}\s*\+\s*([\d.]+)\s*\*\s*{sd}\b".format(
                sd=re.escape(sd), tok=re.escape(tokens), base=re.escape(baseline)
            ),
            spl,
        ),
        "the where clause (sd>0 AND tokens>floor AND tokens>baseline+N*sd)", spl,
    )
    return {
        "span_seconds": int(span.group(1)) * SECONDS[span.group(2)],
        "field": stats.group(1),
        "tokens": tokens,
        "baseline": baseline,
        "sd": sd,
        "window": int(stream.group(1)),
        "include_current": stream.group(2) == "t",
        "stdev_kind": stream.group(5),
        "floor": int(where.group(1)),
        "sigma": float(where.group(2)),
        "sorted": bool(re.search(r"\|\s*sort\s+0\s+_time\b", spl)),
    }


def run_spike_alert():
    """Execute the shipped spike SPL against the shipped fixtures.

    Returns (parsed_spl, all_rows, firing_rows). Each row is a dict shaped like
    the alert's final `table _time tokens baseline sd spike_ratio`, plus the
    threshold and the margin by which the row cleared (or missed) it.
    """
    spl = search_string(SPIKE_ALERT)
    spec = parse_spike_search(spl)
    assert spec["sorted"], "spike SPL lost `sort 0 _time`; streamstats order is undefined"

    series = bin_and_sum(spec["field"], spec["span_seconds"])
    stats = streamstats(
        [tokens for _, tokens in series],
        spec["window"], spec["include_current"], spec["stdev_kind"],
    )

    rows, firing = [], []
    for (epoch, tokens), (baseline, sd) in zip(series, stats):
        row = {
            "_time": epoch,
            "tokens": tokens,
            "baseline": baseline,
            "sd": sd,
            "threshold": None if (baseline is None or sd is None)
            else baseline + spec["sigma"] * sd,
            "fired": False,
        }
        if sd is not None and sd > 0 and tokens > spec["floor"] \
                and tokens > row["threshold"]:
            row["fired"] = True
            row["baseline"] = splunk_round(baseline)
            row["sd"] = splunk_round(sd)
            row["spike_ratio"] = splunk_round(tokens / baseline, 2)
        if row["threshold"]:
            row["margin_pct"] = 100.0 * (tokens - row["threshold"]) / row["threshold"]
        rows.append(row)
        if row["fired"]:
            firing.append(row)
    return spec, rows, firing


def spike_report(spec, rows):
    lines = [
        "[%s] replayed against the shipped fixtures" % SPIKE_ALERT,
        "  window=%d current=%s %s sigma=%s floor=%s span=%ss field=%s"
        % (spec["window"], "t" if spec["include_current"] else "f",
           spec["stdev_kind"], spec["sigma"], spec["floor"],
           spec["span_seconds"], spec["field"]),
    ]
    for row in rows:
        lines.append(
            "  %s tokens=%s baseline=%s sd=%s threshold=%s margin=%s fired=%s"
            % (datetime.fromtimestamp(row["_time"], timezone.utc).strftime(
                   "%Y-%m-%dT%H:%M:%SZ"), row["tokens"],
               "null" if row["baseline"] is None else "%.1f" % row["baseline"],
               "null" if row["sd"] is None else "%.1f" % row["sd"],
               "null" if row["threshold"] is None else "%.1f" % row["threshold"],
               "n/a" if "margin_pct" not in row else "%+.2f%%" % row["margin_pct"],
               row["fired"])
        )
    return "\n".join(lines)


def test_spike_alert_conf_is_parseable_and_tunables_are_derivable():
    spec = parse_spike_search(search_string(SPIKE_ALERT))
    # These are the values the demo narrative and the stanza description claim.
    assert spec["field"] == "total_input_tokens"
    assert spec["span_seconds"] == 3600
    assert spec["window"] == 6
    assert spec["include_current"] is False, (
        "current=t caps the z-score of n buckets at (n-1)/sqrt(n); the alert "
        "would become unfireable over a short window."
    )
    assert spec["stdev_kind"] == "stdevp"
    assert spec["sigma"] == 2.0
    assert spec["floor"] == 2000000


def test_spike_alert_fires_exactly_once_on_the_rogue_hour():
    spec, rows, firing = run_spike_alert()
    report = spike_report(spec, rows)

    assert len(firing) == 1, (
        "expected exactly ONE firing hour, got %d.\n%s" % (len(firing), report)
    )
    row = firing[0]

    # The hour the rogue key burns 3,900,000 uncached input tokens in.
    expected_hour = datetime(2026, 7, 28, 16, 0, tzinfo=timezone.utc).timestamp()
    assert row["_time"] == expected_hour, (
        "the firing hour moved off 2026-07-28T16:00:00Z.\n%s" % report
    )
    assert row["tokens"] == 4976800, (
        "the spiking hour's token total changed -- a usage fixture was edited "
        "and the demo's headline alert may no longer mean what the deck says."
        "\n%s" % report
    )
    assert row["baseline"] == 1962400, report   # mean of the 2 preceding hours
    assert row["sd"] == 1129900, report         # population stdev of those 2
    assert row["threshold"] == 4222200.0, report
    assert row["spike_ratio"] == 2.54, report


def test_spike_alert_keeps_a_comfortable_margin_over_its_threshold():
    spec, rows, firing = run_spike_alert()
    report = spike_report(spec, rows)
    assert firing, "the token-spike alert does not fire at all.\n%s" % report
    margin = firing[0]["margin_pct"]
    assert margin >= MIN_MARGIN_PCT, (
        "the spike clears its own trigger by only %+.2f%% (%d tokens vs a "
        "%.0f threshold); the demo is one fixture tweak from a silent no-op. "
        "Minimum tolerated margin is %.1f%%.\n%s"
        % (margin, firing[0]["tokens"], firing[0]["threshold"],
           MIN_MARGIN_PCT, report)
    )
    # Pin the shipped headroom so a fixture edit that *raises* the spike is
    # noticed too -- the deck quotes these numbers.
    assert round(margin, 2) == 17.87, report


def test_spike_alert_quiet_hours_stay_quiet():
    """The first two hours must not fire: hour 1 has no baseline at all and
    hour 2's single-sample population stdev is 0, which `sd > 0` rejects."""
    spec, rows, _ = run_spike_alert()
    report = spike_report(spec, rows)
    assert len(rows) == 3, "expected 3 hourly buckets in the fixtures.\n%s" % report
    assert rows[0]["baseline"] is None and rows[0]["sd"] is None, report
    assert rows[1]["sd"] == 0.0, report
    assert [r["fired"] for r in rows] == [False, False, True], report


# --------------------------------------------------------------------------
# [Anthropic - Unrecognized API Key]
# --------------------------------------------------------------------------

def parse_rogue_search(spl):
    stats = re.search(r"\bstats\s+((?:sum\(\w+\)\s+as\s+\w+\s*)+)by\s+(\w+)", spl)
    assert stats, "could not derive the stats clause from [%s]: %s" % (ROGUE_ALERT, spl)
    lookup = re.search(
        r"\blookup\s+(\S+)\s+(\w+)\s+as\s+(\w+)\s+OUTPUT\s+(\w+)", spl
    )
    assert lookup, "could not derive the lookup clause from [%s]: %s" % (ROGUE_ALERT, spl)
    isnull = re.search(r"\|\s*where\s+isnull\((\w+)\)", spl)
    assert isnull, "[%s] no longer filters on isnull(): %s" % (ROGUE_ALERT, spl)
    return {
        "aggs": re.findall(r"sum\((\w+)\)\s+as\s+(\w+)", stats.group(1)),
        "by": stats.group(2),
        "lookup": lookup.group(1),
        "lookup_key": lookup.group(2),
        "match_on": lookup.group(3),
        "output": lookup.group(4),
        "isnull_field": isnull.group(1),
        "requires_key": bool(re.search(r"\bapi_key_id=\*", spl)),
    }


def run_rogue_alert():
    """Execute the shipped unrecognized-key SPL against fixtures + lookup."""
    spl = search_string(ROGUE_ALERT)
    spec = parse_rogue_search(spl)
    assert spec["requires_key"], "[%s] dropped `api_key_id=*`" % ROGUE_ALERT
    assert spec["match_on"] == spec["by"], (
        "lookup joins on %r but stats groups by %r" % (spec["match_on"], spec["by"])
    )

    # ... | stats sum(...) as ... by <by>
    grouped = {}
    for page in USAGE_PAGES:
        for event in flatten_usage(load_fixture(page)):
            body = event["data"]
            key = body.get(spec["by"])
            if key in (None, ""):
                continue  # `api_key_id=*` requires the field to be present
            row = grouped.setdefault(key, {alias: 0 for _, alias in spec["aggs"]})
            for source, alias in spec["aggs"]:
                row[alias] += body.get(source) or 0

    # ... | lookup <lookup> <key> as <by> OUTPUT <output>
    baseline = load_lookup(spec["lookup"])
    names = {r[spec["lookup_key"]]: r[spec["output"]] for r in baseline}
    for key, row in grouped.items():
        row[spec["output"]] = names.get(key) or None

    # ... | where isnull(<output>)
    unrecognized = {
        key: row for key, row in grouped.items()
        if row.get(spec["isnull_field"]) is None
    }
    return spec, grouped, unrecognized


def test_rogue_key_alert_returns_exactly_the_rogue_key():
    spec, grouped, unrecognized = run_rogue_alert()
    report = "grouped=%s\nunrecognized=%s" % (sorted(grouped), sorted(unrecognized))

    assert set(unrecognized) == {"apikey_rogue_demo"}, (
        "[%s] must return apikey_rogue_demo and nothing else.\n%s"
        % (ROGUE_ALERT, report)
    )
    row = unrecognized["apikey_rogue_demo"]
    assert row["input_tokens"] == 3900000, row
    assert row["output_tokens"] == 2400000, row
    assert row["name"] is None, row


def test_rogue_key_alert_suppresses_every_known_key():
    """Proves the lookup actually joined -- an empty/unreadable baseline would
    make every key 'unrecognized' and the shadow-AI panel meaningless."""
    spec, grouped, unrecognized = run_rogue_alert()
    known = set(grouped) - set(unrecognized)
    assert known == {"apikey_demo_ci", "apikey_demo_claudecode"}, sorted(grouped)
    for key in known:
        assert grouped[key][spec["output"]], grouped[key]
    assert len(grouped) == 3, sorted(grouped)


def test_shipped_baseline_lookup_is_usable():
    spec = parse_rogue_search(search_string(ROGUE_ALERT))
    rows = load_lookup(spec["lookup"])
    assert rows, "%s.csv is empty; isnull(name) would flag every key" % spec["lookup"]
    for row in rows:
        assert spec["lookup_key"] in row and spec["output"] in row, row
        assert row[spec["lookup_key"]] and row[spec["output"]], row
    # The seeded baseline must mirror the directory fixture the scheduled
    # [Anthropic - Build API Key Baseline] search would rebuild it from.
    directory = {k["id"]: k["name"] for k in load_fixture("api_keys.json")["data"]}
    assert {r[spec["lookup_key"]]: r[spec["output"]] for r in rows} == directory


# --------------------------------------------------------------------------
# Alert plumbing (guards a deliberate, slim-validate-driven decision)
# --------------------------------------------------------------------------

def test_alert_stanzas_use_the_spec_supported_trigger_keys():
    conf = read_savedsearches()
    for stanza in (SPIKE_ALERT, ROGUE_ALERT):
        assert conf.get(stanza, "enableSched") == "1", stanza
        assert conf.get(stanza, "counttype") == "number of events", stanza
        assert conf.get(stanza, "relation") == "greater than", stanza
        assert conf.get(stanza, "quantity") == "0", stanza
        for rejected in ("alert_type", "alert_comparator", "alert_threshold"):
            assert not conf.has_option(stanza, rejected), (
                "%s is not in savedsearches.conf.spec on this Splunk build; "
                "slim validate rejects it. [%s]" % (rejected, stanza)
            )

import configparser
from datetime import datetime
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "package"
DEFAULT = PKG / "default"
LOOKUPS = PKG / "lookups"

# Real emitted timestamp values, as recorded by running the actual transform
# code over real fixtures (see task-10-brief.md). analytics/providers share
# "bucket_start", which comes straight from the API's space-separated,
# offset-free bucket column. keys uses "snapshot_at", which the add-on itself
# generates as RFC 3339.
REAL_TIMESTAMPS = {
    "openrouter:analytics": "2026-07-31 12:00:00",
    "openrouter:providers": "2026-07-31 12:00:00",
    "openrouter:keys": "2026-08-01T00:00:00Z",
}

EXPECTED_TIME_FORMAT = {
    "openrouter:analytics": "%Y-%m-%d %H:%M:%S",
    "openrouter:providers": "%Y-%m-%d %H:%M:%S",
    "openrouter:keys": "%Y-%m-%dT%H:%M:%SZ",
}


def read(name):
    # interpolation=None: TIME_FORMAT values contain strptime "%" directives
    # (e.g. "%Y-%m-%d %H:%M:%S"), which ConfigParser's default
    # BasicInterpolation misreads as interpolation syntax and rejects with
    # InterpolationSyntaxError. Splunk itself does not interpolate "%" in
    # conf files, so this parser should not either.
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(DEFAULT / name)
    return parser


def test_all_three_sourcetypes_declared():
    props = read("props.conf")
    assert set(props.sections()) == {
        "openrouter:analytics", "openrouter:providers", "openrouter:keys"}


def test_every_sourcetype_pins_utc_and_json():
    # Without TZ the indexer applies its local offset, because neither
    # TIME_FORMAT carries an explicit UTC offset (%Y-%m-%d %H:%M:%S has none
    # at all; %Y-%m-%dT%H:%M:%SZ treats the trailing Z as a literal).
    props = read("props.conf")
    for section in props.sections():
        assert props[section]["TZ"] == "UTC", section
        assert props[section]["KV_MODE"] == "json", section


def test_no_timestamp_fields_under_kv_mode_json():
    props = read("props.conf")
    for section in props.sections():
        assert "TIMESTAMP_FIELDS" not in props[section], section


def test_time_format_matches_actual_emitted_timestamp():
    # This is the regression test for the defect that reached this task: the
    # original brief set TIME_FORMAT = %Y-%m-%dT%H:%M:%SZ on all three
    # sourcetypes, but analytics/providers' bucket_start is space-separated
    # and offset-free ("2026-07-31 12:00:00"), not RFC 3339. A wrong
    # TIME_FORMAT still parses (props.conf is syntactically fine) -- it just
    # fails timestamp extraction at index time and silently falls back to
    # index time, so pinning TZ/KV_MODE alone does not catch it. Assert the
    # configured TIME_FORMAT both matches what we expect AND actually parses
    # the real recorded value via strptime.
    props = read("props.conf")
    for section, expected_format in EXPECTED_TIME_FORMAT.items():
        configured_format = props[section]["TIME_FORMAT"]
        assert configured_format == expected_format, section

        real_value = REAL_TIMESTAMPS[section]
        # Must not raise -- proves the configured format actually parses the
        # real emitted value, not just that the string matches expectations.
        datetime.strptime(real_value, configured_format)


def test_brief_original_time_format_fails_on_analytics_value():
    # Documents *why* the correction was necessary: the brief's original
    # TIME_FORMAT (%Y-%m-%dT%H:%M:%SZ, correct only for openrouter:keys)
    # cannot parse the real analytics/providers bucket_start value at all.
    brief_original_format = "%Y-%m-%dT%H:%M:%SZ"
    analytics_value = REAL_TIMESTAMPS["openrouter:analytics"]
    try:
        datetime.strptime(analytics_value, brief_original_format)
    except ValueError:
        pass
    else:
        raise AssertionError(
            "expected the brief's original TIME_FORMAT to fail on the real "
            "analytics bucket_start value; if it now parses, the fixture "
            "value or format changed and this test needs review"
        )


def test_index_macro_is_permissive_by_default():
    macros = read("macros.conf")
    assert macros["openrouter_index"]["definition"] == "index=*"


def test_baseline_lookup_ships_empty():
    # Shipping fixture keys would make every installer's first run flag every
    # real key as unrecognised.
    lines = (LOOKUPS / "openrouter_key_baseline.csv").read_text().strip().splitlines()
    assert len(lines) == 1, "baseline lookup must ship headers only"
    assert lines[0] == "api_key_id,name,first_seen"

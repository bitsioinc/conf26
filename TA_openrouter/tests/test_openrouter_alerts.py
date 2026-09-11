"""Derive and pin the spend-spike threshold against fixture data, and check
savedsearches.conf against the real (verified) schema.

Nothing is copied from TA_anthropic's test_alerts.py -- the shape is the same
(hourly buckets, streamstats window=6) but the multiplier is re-derived here
because the data is different.

The task-11 brief was written against a falsified schema and is wrong in
several ways the docs/schema-verification.md controller run corrected:

- Fixtures live under TA_openrouter/openrouter_mockserver/fixtures/, not
  TA_openrouter/mockserver/fixtures/ (the package was renamed to avoid
  colliding with the sibling TA_anthropic add-on's own mockserver/).
- The analytics bucket column is "date__hour", not "date".
- request_count is a JSON *string* in the raw fixture; it must be coerced
  before arithmetic.
- Most importantly: analytics rows' api_key_id carries the key's *name*
  (e.g. "Alpaca"), never its roster hash. Verified live: analytics
  api_key_id intersected with keys.name recovers all 3 sanctioned keys;
  intersected with keys.hash it is empty. Every join in savedsearches.conf
  must key off name, not hash -- that is what most of this file checks.
"""
import configparser
import json
import statistics
from collections import defaultdict
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "openrouter_mockserver" / "fixtures"
SAVEDSEARCHES = (Path(__file__).resolve().parent.parent
                  / "package" / "default" / "savedsearches.conf")

#: Standard deviations above the trailing mean before an hour counts as a
#: spike. Verified against the committed fixtures (see
#: docs/schema-verification.md): at sigma=3 exactly one key (Nimbus_Prod_Key)
#: is flagged; at sigma=0.5 more than one is (Nimbus_Prod_Key and
#: Shadow_Intern_Key). Must match the "3*trailing_stdev" literal in
#: savedsearches.conf's "Hourly Spend Spike by Key" search -- see
#: test_saved_search_uses_the_pinned_sigma below.
SPIKE_SIGMA = 3
WINDOW = 6

MACRO = "`openrouter_index`"

BASELINE_STANZA = "OpenRouter - Build API Key Baseline"
UNRECOGNISED_STANZA = "OpenRouter - Unrecognised API Key Active"
SPIKE_STANZA = "OpenRouter - Hourly Spend Spike by Key"
ALL_STANZAS = (BASELINE_STANZA, UNRECOGNISED_STANZA, SPIKE_STANZA)

BANNED_TRIGGER_KEYS = {"alert_type", "alert_comparator", "alert_threshold"}


# --------------------------------------------------------------------------
# Fixture loading
# --------------------------------------------------------------------------

def analytics_rows():
    payload = json.loads((FIXTURES / "analytics_by_key_model.json").read_text())
    return payload["data"]["data"]


def roster_keys():
    payload = json.loads((FIXTURES / "keys.json").read_text())
    return payload["data"]


def hourly_spend_by_key():
    """{api_key_id: [(date__hour, total_usage), ...]} sorted by bucket."""
    totals = defaultdict(float)
    for row in analytics_rows():
        totals[(row["api_key_id"], row["date__hour"])] += row["total_usage"]
    by_key = defaultdict(list)
    for (key, stamp), usage in sorted(totals.items(), key=lambda kv: kv[0][1]):
        by_key[key].append((stamp, usage))
    return by_key


def spikes(sigma, window=WINDOW):
    """Replicate Splunk's `streamstats window=6 current=f ... by api_key_id`
    exactly -- including its growing partial window and its stdev()
    semantics -- against the shipped search's own
    `spend_usd > trailing_avg + sigma*trailing_stdev` threshold.

    Two things a naive port gets wrong, both fixed here:

    1. `current=f` does not require a full N-point trailing window before it
       starts producing output. At the 2nd bucket in a series it already has
       a 1-point trailing window; at the 3rd, 2 points; and so on, growing
       until it reaches `window` points and staying there (a true trailing
       window) from then on. `series[max(0, index - window):index]` already
       yields this growing slice for free for every `index`, so evaluating
       every index (not skipping short ones) is what makes this a faithful
       replication rather than a "wait for 6, then evaluate the 7th only"
       approximation.

    2. SPL's `stdev()` is the *sample* standard deviation (divides by
       n-1) -- matched here by `statistics.stdev`, not
       `statistics.pstdev` (population, divides by n; that is Splunk's
       separately named `stdevp()`, never used by this search).
       `statistics.stdev` needs at least 2 data points and raises
       `StatisticsError` below that. Splunk itself yields a null
       trailing_stdev at n<2 (a single-point sample stdev is undefined),
       which the shipped search already filters out via
       `isnotnull(trailing_stdev) AND trailing_stdev>0`. Mirrored here by
       skipping any index whose trailing window has fewer than 2 points,
       rather than letting `statistics.stdev` raise.
    """
    found = []
    for key, series in hourly_spend_by_key().items():
        for index in range(len(series)):
            trailing = [v for _, v in series[max(0, index - window):index]]
            if len(trailing) < 2:
                continue
            mean = statistics.fmean(trailing)
            stdev = statistics.stdev(trailing)
            stamp, value = series[index]
            if stdev and value > mean + sigma * stdev:
                found.append((key, stamp))
    return found


# --------------------------------------------------------------------------
# Threshold derivation (Step 1/2 of the brief, field names corrected)
# --------------------------------------------------------------------------

def test_fixture_has_enough_history_for_the_window():
    for key, series in hourly_spend_by_key().items():
        assert len(series) >= WINDOW + 1, "{} has only {} buckets".format(
            key, len(series))


def test_every_key_has_seven_hourly_buckets():
    # docs/schema-verification.md: "every key has 7 hourly buckets".
    for key, series in hourly_spend_by_key().items():
        assert len(series) == 7, "{} has {} buckets, expected 7".format(
            key, len(series))


def test_threshold_isolates_exactly_one_key():
    detected = {key for key, _ in spikes(SPIKE_SIGMA)}
    assert detected == {"Nimbus_Prod_Key"}, \
        "expected only Nimbus_Prod_Key to spike at sigma={}, got {}".format(
            SPIKE_SIGMA, detected)


def test_threshold_is_not_trivially_permissive():
    # A sigma so low that everything fires would pass the test above by
    # accident on a fixture with only one key.
    detected = {key for key, _ in spikes(0.5)}
    assert len(detected) > 1, \
        "fixture cannot distinguish a real threshold from a permissive one"
    assert detected >= {"Nimbus_Prod_Key", "Shadow_Intern_Key"}, \
        "expected sigma=0.5 to also catch Shadow_Intern_Key, got {}".format(
            detected)


# --------------------------------------------------------------------------
# The name-based join, replicated in Python against the real fixtures
# --------------------------------------------------------------------------

def test_analytics_join_key_is_name_not_hash():
    # This is the controller-verified fact that drives the whole rewrite:
    # analytics rows never reference a roster hash, only a roster name.
    names = {key["name"] for key in roster_keys()}
    hashes = {key["hash"] for key in roster_keys()}
    ids = {row["api_key_id"] for row in analytics_rows()}

    sanctioned_ids_seen_in_analytics = ids & names
    assert sanctioned_ids_seen_in_analytics == {
        "Alpaca", "Nimbus_Prod_Key"}, sanctioned_ids_seen_in_analytics
    assert ids & hashes == set(), \
        "no analytics api_key_id should ever equal a roster hash"


def test_unrecognised_key_alert_would_flag_shadow_intern_only():
    # Simulates: stats ... by api_key_id | lookup ... api_key_id OUTPUT name
    # | where isnull(baseline_name), with the baseline keyed by name (the
    # rewrite this task makes) rather than hash (the brief's original,
    # unjoinable version).
    baseline_names = {key["name"] for key in roster_keys()}
    analytics_ids = {row["api_key_id"] for row in analytics_rows()}
    unrecognised = analytics_ids - baseline_names
    assert unrecognised == {"Shadow_Intern_Key"}, unrecognised


def test_hash_based_join_would_have_flagged_every_key():
    # Documents why the brief's original `rename hash as api_key_id` baseline
    # can never match: if the baseline's api_key_id had been the hash
    # instead of the name, every single analytics api_key_id -- including
    # the 3 sanctioned keys -- would come up unrecognised.
    baseline_hashes = {key["hash"] for key in roster_keys()}
    analytics_ids = {row["api_key_id"] for row in analytics_rows()}
    unrecognised = analytics_ids - baseline_hashes
    assert unrecognised == analytics_ids


# --------------------------------------------------------------------------
# savedsearches.conf structure
# --------------------------------------------------------------------------

def read_savedsearches():
    # interpolation=None: the baseline builder's strftime format string
    # ("%Y-%m-%dT%H:%M:%SZ") contains "%" characters that ConfigParser's
    # default BasicInterpolation misreads as interpolation syntax and
    # rejects with InterpolationSyntaxError. Splunk itself does not
    # interpolate "%" in conf files, so this parser should not either (same
    # convention as tests/test_openrouter_conf.py).
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(SAVEDSEARCHES)
    return parser


def test_conf_parses_with_exactly_the_three_expected_stanzas():
    parser = read_savedsearches()
    assert set(parser.sections()) == set(ALL_STANZAS)


def test_no_banned_rest_trigger_keys_on_any_stanza():
    # alert_type / alert_comparator / alert_threshold are REST API argument
    # names, absent from savedsearches.conf.spec; `slim validate` rejects
    # them if present.
    parser = read_savedsearches()
    for section in parser.sections():
        present = BANNED_TRIGGER_KEYS & set(parser[section])
        assert not present, (section, present)


def test_scheduled_stanzas_use_counttype_relation_quantity():
    parser = read_savedsearches()
    for section in parser.sections():
        if parser[section].get("enableSched") == "1":
            assert parser[section]["counttype"] == "number of events", section
            assert parser[section]["relation"] == "greater than", section
            assert "quantity" in parser[section], section


def test_baseline_builder_is_unscheduled():
    # It writes the lookup an operator re-runs deliberately; it must not
    # silently overwrite openrouter_key_baseline.csv on a cron.
    parser = read_savedsearches()
    assert parser[BASELINE_STANZA]["enableSched"] == "0"


def test_baseline_builder_cron_schedule_is_present_but_inert():
    # Known, accepted minor: the baseline stanza carries
    # `cron_schedule = 0 6 * * *` alongside `enableSched = 0`. Splunk only
    # consults cron_schedule when enableSched=1 (see savedsearches.conf.spec
    # -- cron_schedule is documented as "Set the scheduled time... for this
    # saved search", scoped to *scheduled* searches), so this value is
    # inert: no run is ever triggered by it, and it does not imply a 6am
    # cadence the search doesn't actually have. Left in place rather than
    # removed from savedsearches.conf in this fix round -- the production
    # conf file was reviewed clean on every load-bearing point (name-based
    # join, field names, trigger-key spelling, macro coverage) and is
    # explicitly out of scope for non-load-bearing edits here. Pinned as a
    # test so the inert value doesn't silently drift, and so a future editor
    # sees why it was left rather than rediscovering the question.
    parser = read_savedsearches()
    assert parser[BASELINE_STANZA]["enableSched"] == "0"
    assert "cron_schedule" in parser[BASELINE_STANZA]


def test_every_stanza_search_uses_the_index_macro():
    # A search that omits `openrouter_index` only searches the role's
    # default indexes and renders empty with nothing visibly broken -- this
    # add-on's characteristic failure mode. Not in the original brief.
    #
    # A raw substring check would also pass if the macro were merely
    # *mentioned* somewhere inert -- e.g. inside a backtick-fenced SPL
    # comment (SPL's own comment syntax is ```...```, which reuses the same
    # backtick character as macro invocation, so "`openrouter_index`" could
    # appear in a comment and still match `in`). The macro only actually
    # scopes the index if it is part of the *base* search, i.e. before the
    # first pipe -- so require that ordering rather than mere presence.
    parser = read_savedsearches()
    for section in ALL_STANZAS:
        search = parser[section]["search"]
        macro_pos = search.find(MACRO)
        first_pipe_pos = search.find("|")
        assert macro_pos != -1, "{} does not reference {}".format(section, MACRO)
        assert first_pipe_pos != -1, "{} has no pipe at all".format(section)
        assert macro_pos < first_pipe_pos, (
            "{} macro appears at or after the first pipe ({}), so it is not "
            "part of the base search and would not actually scope the "
            "index".format(section, MACRO))


def test_baseline_builder_keys_output_on_name_not_hash():
    parser = read_savedsearches()
    search = parser[BASELINE_STANZA]["search"]
    assert "sourcetype=openrouter:keys" in search
    assert "by name" in search
    assert "eval api_key_id=name" in search
    # Regression guard: the falsified original grouped by hash and renamed
    # hash into api_key_id, which can never match analytics' api_key_id.
    assert "by hash" not in search
    assert "rename hash as api_key_id" not in search


def test_unrecognised_key_search_joins_on_a_field_analytics_actually_emits():
    parser = read_savedsearches()
    search = parser[UNRECOGNISED_STANZA]["search"]
    assert "sourcetype=openrouter:analytics" in search
    # api_key_id is a field openrouter:analytics actually emits (verified in
    # docs/schema-verification.md); openrouter:providers, by contrast, never
    # carries api_key_id at all.
    assert "by api_key_id" in search
    assert "lookup openrouter_key_baseline api_key_id" in search
    assert "OUTPUT name as baseline_name" in search
    assert "isnull(baseline_name)" in search
    # Must not silently drift to joining on hash.
    assert "hash" not in search


def test_spike_search_uses_the_usd_suffixed_field():
    # total_usage_usd, not total_usage -- the _usd suffix is applied by
    # openrouter_transform.py, not present on the raw API field name.
    parser = read_savedsearches()
    search = parser[SPIKE_STANZA]["search"]
    assert "sourcetype=openrouter:analytics" in search
    assert "total_usage_usd" in search
    assert "total_usage)" not in search  # would mean the _usd suffix got lost


def test_saved_search_uses_the_pinned_sigma():
    # Keeps the conf file and the derived-and-verified SPIKE_SIGMA constant
    # from silently drifting apart.
    parser = read_savedsearches()
    search = parser[SPIKE_STANZA]["search"]
    assert "window={}".format(WINDOW) in search
    assert "{}*trailing_stdev".format(SPIKE_SIGMA) in search


def test_baseline_description_documents_the_rename_caveat():
    parser = read_savedsearches()
    description = parser[BASELINE_STANZA]["description"].lower()
    assert "rename" in description
    assert "unrecognised" in description or "unrecognized" in description


def test_baseline_builder_guards_against_deleting_lookup_on_empty_run():
    # outputlookup defaults to override_if_empty=true, which DELETES the
    # lookup file outright when a run returns zero rows (confirmed against
    # /opt/splunk/etc/system/default/searchbnf.conf). A deleted baseline
    # makes the downstream `lookup openrouter_key_baseline ...` clause in
    # "OpenRouter - Unrecognised API Key Active" fail the search outright,
    # not merely return it empty -- so that alert's
    # counttype=number of events / relation=greater than / quantity=0
    # trigger silently never fires again. A keys-input outage or
    # index-latency race outliving this search's -7d dispatch.earliest_time
    # is enough to hit this: the sibling TA_anthropic add-on already learned
    # this the hard way (see TA_anthropic/package/default/savedsearches.conf,
    # "override_if_empty=false" on its own baseline builder) -- this guard
    # must not regress back to the bare, unguarded form.
    parser = read_savedsearches()
    search = parser[BASELINE_STANZA]["search"]
    assert "outputlookup override_if_empty=false openrouter_key_baseline" in search


def test_baseline_description_documents_the_override_if_empty_guard():
    # The next person to touch this search needs to know the guard is
    # load-bearing, not decorative -- so it must be explained in the
    # stanza description, not just present in the search pipeline.
    parser = read_savedsearches()
    description = parser[BASELINE_STANZA]["description"].lower()
    assert "override_if_empty=false" in description
    assert "delete" in description

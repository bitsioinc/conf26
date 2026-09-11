# Operations

## Populating the baseline

`openrouter_key_baseline.csv` ships with headers only
(`api_key_id,name,first_seen`). Until you populate it, the Unrecognised Key
alert and the Key Governance dashboard's "unrecognised keys" panel treat
**every** key as unrecognised.

Run **OpenRouter - Build API Key Baseline** from inside the TA_openrouter
app — not from Search & Reporting. `outputlookup` writes to whichever app the
search runs in, so running it elsewhere silently creates the lookup in the
wrong place, and the alerts (which read the lookup from inside this app)
will keep seeing an empty baseline with no error surfaced anywhere.

The search is deliberately unscheduled (`enableSched = 0`) — it writes a
lookup an operator re-runs deliberately, not one that should silently
overwrite itself on a cron. Re-run it whenever keys are legitimately added
**or renamed**.

### Why renaming a key matters

OpenRouter's analytics endpoints identify a key by its human-readable `name`
in `api_key_id` — never by the roster's `hash` (verified against a live
account; see `docs/schema-verification.md`). The baseline builder therefore
keys its lookup output on `name`, and every alert/dashboard panel that joins
analytics traffic to the roster joins on that same field. There is no
`hash`-based fallback, because analytics never exposes the hash to join
against.

**Consequence:** renaming a key in OpenRouter is indistinguishable, from the
analytics feed's perspective, from retiring the old key and issuing a
brand-new one under a different name. The renamed key's new `api_key_id`
value has no row in `openrouter_key_baseline.csv` until you re-run the
baseline builder — until then it fires the Unrecognised Key alert and shows
up in the Key Governance dashboard's unrecognised-keys panel exactly like
genuine shadow usage. There is also no continuity of `first_seen` history
across the rename: the baseline builder recomputes `first_seen` per `name`
from `openrouter:keys`, so the old name's history is not carried forward.
This is inherent to what the API exposes, not a defect in the search.

## The index macro

All searches go through the `openrouter_index` macro, which ships as
`index=*`. To narrow it, override the macro locally rather than editing the
shipped searches:

    [openrouter_index]
    definition = index=openrouter

Omitting the macro from a custom search entirely is the add-on's most common
silent-failure shape: it searches only the role's default indexes and
renders an empty panel with nothing visibly broken.

## "No new events" is often correct

`compute_window` only releases complete hours, and it resumes exactly one
hour past the last checkpointed bucket — never re-requesting a bucket
already emitted. Re-running an input inside the same clock hour, or shortly
after its last run, is expected to collect nothing new. Diagnose (check the
per-input log at `$SPLUNK_HOME/var/log/splunk/ta_openrouter_<input>.log`)
before resetting a checkpoint or assuming the input is broken. The one
scenario where deliberately moving a checkpoint forward *is* the correct,
informed action — not a routine reset — is sustained truncation; see below.

## Truncation

`/analytics/query` caps the rows returned per request and reports the cap
via `metadata.truncated`. The add-on requests 50,000 rows per query — well
above the server's undocumented default of 1,000 — specifically so that
truncation is a tail case: it takes **more than 50,000 rows in a single
query window** to trigger.

**If it does trigger, there is no automatic recovery.** The affected query's
checkpoint is held (not advanced), so the same window is re-requested every
run. Because the window's start stays pinned to the last successful
checkpoint while its end keeps advancing with the clock, the window only
grows — it never shrinks on its own, and the affected input effectively
stalls: it keeps re-querying, keeps getting truncated, and keeps ingesting
nothing for that query, indefinitely, until an operator intervenes.

**How to recognize it:** a WARN-level line in the per-input log, naming the
input, the sourcetype, the queried window, and the row count the server
actually returned, e.g.:

    Truncated response for input=<name> sourcetype=openrouter:analytics
    window=(...) row_count=<n>; checkpoint held, no events ingested this
    run. This window has more rows than the server will return in one
    response; this will recur every run until an operator manually advances
    the checkpoint -- see the Truncation section in
    TA_openrouter/docs/operations.md.

This is distinguishable from a routine "nothing new to collect" run, which
logs at INFO and never mentions a window or row count.

**Shortening the input's interval looks like the obvious first fix here, but
it does not help.** The input's `interval` field only controls how often
Splunk invokes the collection script — it is never read by `compute_window`
or passed to `collect_query` at all. `openrouter_analytics_helper.GRANULARITY`
is hardcoded to `"hour"`; nothing about the query window is configurable per
input. The window is derived purely from the held checkpoint and the current
time snapped to the last complete hour, so polling more often produces the
*identical* window on every attempt — it does not shrink, and it cannot
shrink, no matter how frequently the input runs. Because the checkpoint is
held while the window's end keeps advancing with the clock, waiting longer
between polls only makes the window (and therefore the row count) larger,
never smaller.

**The only lever that actually shrinks the window is moving the checkpoint
forward.** Checkpoints live in the KV Store collection
`TA_openrouter_checkpoints` (see `docs/architecture.md`), one document per
`<input_name>_<checkpoint_suffix>` key, with the checkpoint value held
JSON-encoded in that document's `state` field — for example, the document with
`_key = "myanalytics_analytics_by_key_model"` holds a `state` of
`"2026-07-31 11:00:00"`, meaning the next window will start at
`2026-07-31 12:00:00`. Advancing that stored value to a later bucket start
(via the KV Store REST endpoint,
`.../storage/collections/data/TA_openrouter_checkpoints`, or any KV Store
management tooling available in your environment) makes the next run's
window start later, shrinking or eliminating the truncated window entirely.

**This is a deliberate trade, not a fix without cost:** every bucket between
the old checkpoint and wherever you advance it to is permanently skipped —
never collected, not now, not on a later run. Understand the size of that
gap before making the change; there is no way to recover the skipped rows
afterward once the checkpoint has moved past them (see "Truncation" in
`docs/architecture.md` — this is the same rationale that keeps `collect_query`
from ever advancing a checkpoint past a truncated window on its own).
Reducing the volume of distinct `(api_key_id, model)` or `(model, provider)`
combinations your account produces per hour (fewer active keys or models,
where that's actionable) lowers the row count of *future* windows, but does
nothing to un-stall a checkpoint that is already stuck — that still requires
the manual advance above.

There is no automatic window-bisection or retry-with-a-smaller-window
logic — that was considered and deliberately not implemented for v0.1.0. The
WARN log is the operator's signal to act manually, not a self-healing
mechanism — and, as noted above, shortening the input's interval will not
make it self-heal either.

## Additive versus rate metrics

v0.1.0 ingests only additive metrics (`request_count`, `total_usage`,
`tokens_total`, `tokens_prompt`, `tokens_completion`, `reasoning_tokens`,
`cached_tokens`, `byok_usage`). OpenRouter also exposes rate metrics
(`avg_latency`, `p90_latency`, `cache_hit_rate`,
`blended_cost_per_million_tokens`, and others carrying `is_rate: true` in
`/analytics/meta`) that this add-on does not request. **Never `sum()` a rate
metric across time buckets or dimension values** if you add one — the result
is meaningless and Splunk will not warn you. Use `avg` or `latest` instead.

`analytics_meta.json` documents `cache_hit_rate` only as a percent-formatted
rate metric (`display_label: "Cache Hit Rate"`) with no formula. A reasonable
proxy from the additive fields this add-on already ingests is
`cached_tokens / tokens_prompt`, but that ratio is not confirmed against
OpenRouter's own definition — treat it as an approximation, not a verified
equivalence.

## Retention

`/activity` (a separate, non-ingested OpenRouter endpoint) is capped at 30
completed UTC days. `/analytics/query` documents no limit, but that figure is
unmeasured against the live API, so `backfill_days` (which only matters on
an input's first run, before it has a checkpoint) is clamped to 30 regardless
of what is configured, via `MAX_BACKFILL_DAYS` in `openrouter_transform.py`.
If you establish the real figure for `/analytics/query`, record it here and
raise the constant.

## Workspaces

`GET /keys` returns only the default workspace's keys unless `workspace_id`
is passed; this add-on never passes it. Multi-workspace enumeration is not
implemented in v0.1.0 — keys created in a non-default workspace will never
appear in the roster, and any traffic they generate will look unrecognised
in the Key Governance dashboard and the Unrecognised Key alert, exactly like
genuine shadow usage.

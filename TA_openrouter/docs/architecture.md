# Architecture

Four modules under `package/bin/`. `openrouter_client.py` and
`openrouter_transform.py` have no Splunk imports and no network access beyond
what `openrouter_client.py` itself performs, so both are unit-testable
without a Splunk runtime; the two `*_helper.py` modules are the modular-input
entry points UCC wires up and import `solnlib`/`splunklib` (guarded by a
`try/except ImportError` so the test suite can still import them without a
Splunk install).

## openrouter_client.py

HTTP only, dependency-light (only `requests`, imported lazily). Bearer auth
(`Authorization: Bearer <management key>`) — not `x-api-key` as some other
provider APIs use. `POST /analytics/query` and `GET /keys` with offset
pagination (advances `offset` until an **empty** page comes back; a page
short of a full batch is not itself a stop condition). Exactly one retry on
429 or any 5xx, then give up. Returns response bodies verbatim — interpreting
them is `openrouter_transform`'s job.

4xx other than 429 is **not** retried. A management key used against the
wrong endpoint, or an inference key mistakenly used as a management key,
fails permanently; retrying would burn an interval for nothing.

The 50,000-row `limit` the client sends on every analytics query
(`DEFAULT_ROW_LIMIT`) does not apply to `list_keys` — pagination there is
guarded separately, by a `KEYS_MAX_PAGES = 10000` iteration cap that raises
loudly rather than looping forever against a server bug.

## openrouter_transform.py

Pure logic. No Splunk imports, no network, no clock beyond what the caller
passes in. Money throughout is USD with no cents divisor (unlike some other
provider APIs).

- **`compute_window`** snaps the window end to the last complete hour (a
  partially elapsed hour is never collected) and resumes the window start
  **one granularity step past the checkpoint**, not at the checkpoint value
  itself. The server's `time_range.start` bound is inclusive, and the
  checkpoint holds the *start* of the newest bucket already emitted
  (`max_bucket`'s return value) — reusing it verbatim as the next window's
  start would re-request, and re-ingest, that same bucket on every run,
  forever. This resume-past-checkpoint behavior is a fix from an earlier
  revision, not the original design: before it, the newest bucket was
  re-collected on every single run with no bound, silently inflating every
  summed metric. `backfill_days` (first run only, no checkpoint yet) is
  clamped to `MAX_BACKFILL_DAYS = 30`.
- **`flatten_analytics`** is parameterized by `dimensions` (recorded onto the
  event as `group_by`, so a search can tell the two queries apart) and by an
  explicit `time_field`, because `/analytics/query` returns untyped rows
  shaped by the request and **the bucket column is not the same name for
  both queries**: the `[api_key_id, model]` query's rows carry it in
  `date__hour`, the `[model, provider]` query's rows carry it in
  `created_at__hour`. Neither matches the function's own default parameter
  value of `"date"` — that default exists so an explicit-but-wrong caller
  fails loudly in a unit test rather than a caller forgetting the argument
  failing silently in production. Getting either query's `time_field` wrong
  causes `flatten_analytics` to skip every row (`if not stamp: continue`)
  with no error surfaced anywhere; the input reports success and ingests
  zero events forever. `openrouter_analytics_helper.QUERIES` carries the
  correct `time_field` per query for exactly this reason.

  Every emitted event also gets an add-on-injected `bucket_start` field, set
  to the row's own bucket value under whichever column it came from. This is
  the field to use for anything that needs to reason about analytics time
  buckets across **both** sourcetypes, because it is the only field present
  under the same name on both `openrouter:analytics` and
  `openrouter:providers` — `date__hour` only exists on the former,
  `created_at__hour` only on the latter. `props.conf`'s `TIME_PREFIX` for
  both sourcetypes extracts `_time` from `bucket_start` for this reason.

- **`is_truncated`** reads `metadata.truncated`, the server's own signal that
  it capped the result set for a request.
- Metric fields are coerced through an explicit `METRIC_FIELDS` allowlist
  (`_coerce_number`, tries `int` then `float`, leaves the value untouched if
  neither parses) because real responses mix already-numeric values with
  digit strings for the identical field, sometimes inconsistently *between*
  the two queries for the identical field name (`reasoning_tokens` arrives as
  a string from `[api_key_id, model]` and as an int from `[model, provider]`
  in the one live-recorded pair of rows — see `docs/schema-verification.md`).
  Only fields in the allowlist are touched, so a dimension value that happens
  to look numeric (a model name like `"4.1"`) can never be silently coerced.
  Currency fields additionally get a `_usd` suffix (`total_usage` →
  `total_usage_usd`) so the unit is never ambiguous in a search.

  **`/keys` currency fields are different.** `flatten_keys` renames currency
  fields with the same `_usd` suffix but does **not** run them through
  `_coerce_number` — because, unlike the analytics metrics, every currency
  field on a live `/keys` response (`limit`, `limit_remaining`, `usage`, and
  the `usage_daily`/`weekly`/`monthly`/`byok_usage*` family) was observed as
  a bare JSON number on both real keys recorded, never a string. The two
  endpoints do not share a numeric-typing convention, and the transform code
  does not pretend they do.

## Timestamp formats (two genuinely different shapes)

Real `/analytics/query` row timestamps (`date__hour`, `created_at__hour`,
and the `bucket_start` field derived from them) are **space-separated and
carry no UTC offset**: `"2026-07-31 18:00:00"`. This is not RFC 3339. The
add-on's own generated `snapshot_at` field on `openrouter:keys` events *is*
genuine RFC 3339: `"2026-08-01T00:00:00Z"`. `openrouter_transform._parse`
handles both shapes (attaching UTC to whichever one arrives with no offset),
and `props.conf` gives each sourcetype its own `TIME_FORMAT` accordingly —
`%Y-%m-%d %H:%M:%S` for `openrouter:analytics`/`openrouter:providers`,
`%Y-%m-%dT%H:%M:%SZ` for `openrouter:keys`. Using the wrong format on either
sourcetype parses fine as conf syntax and fails only at index time, silently
falling back to index time — see `tests/test_openrouter_conf.py` for the
regression test that pins this.

## openrouter_analytics_helper.py

The modular input for the Analytics service. Iterates the two entries in
`QUERIES`, each with its own KV Store checkpoint keyed by
`<input_name>_<checkpoint_suffix>`, and its own `try/except` so a transient
failure on one query (a network blip, an `OpenRouterAPIError`) is logged and
the loop moves on to the other rather than aborting the whole input for the
interval.

### Truncation

`collect_query` returns `(events, None)` on a truncated
response and **discards the partial rows** — emitting them while holding the
checkpoint would duplicate them next run; advancing the checkpoint past them
would lose the missing rows permanently and silently. This guard has **no
automatic recovery path**: as long as a window keeps coming back truncated,
the checkpoint never advances, the window only grows (each run's start stays
fixed at the last successful checkpoint while `end` keeps moving forward with
the clock), and the input stalls indefinitely on that query. There is no
retry-with-a-smaller-window logic; the only remedy is operator intervention.
See `docs/operations.md` for how to recognize this in the logs and the
manual fix.

Truncation, an empty window (no complete bucket has elapsed yet, so no API
call is even made), and a genuinely zero-row window are otherwise
indistinguishable — all three make `collect_query` return `(events, None)`.
An optional `diagnostics` dict (not part of the return tuple, so every
existing 5-positional-argument caller keeps working unmodified) is populated
with a `"reason"` of `"no_window"`, `"empty"`, `"truncated"`, or `"ok"`, plus
`"window"` and `"row_count"` when truncated. `stream_events` uses this to log
truncation at **WARN** (naming the input, sourcetype, window, and row count)
instead of the routine INFO level the other two conditions get — this is the
one signal that distinguishes "nothing new happened" from "data is being
silently withheld."

Truncation requires more than 50,000 rows to come back for a single window
(the add-on requests `limit=50000` per query, well above the server's
undocumented default of 1,000, specifically so this is a tail case) — see
`openrouter_client.DEFAULT_ROW_LIMIT`.

`logger_for_input()` runs *inside* the per-input `try`, not before it: a
failure constructing the per-input logger used to raise outside every
try/except in the input loop, aborting the whole run and skipping every
other configured input. The `except` handler falls back to a module-level
logger (named after the add-on only, so it never depends on the input name
that may not be safely available) whenever the per-input logger itself was
never created.

## openrouter_keys_helper.py

Snapshot input for the API Keys service. No checkpoint: the roster is small,
every run emits the whole list stamped with `snapshot_at`, and searches dedup
on it (typically `stats latest(...) by hash`). Same per-input failure
isolation and logger-fallback shape as `openrouter_analytics_helper.py`, for
consistency between the two modules. Only the default workspace's keys are
returned (`GET /keys` without `workspace_id`); multi-workspace enumeration is
not implemented in v0.1.0, so keys living in another workspace will appear
unrecognised.

## Checkpoints

KV Store collection `TA_openrouter_checkpoints`, keys formed as
`<input_name>_<checkpoint_suffix>` (e.g. `myanalytics_analytics_by_key_model`,
`myanalytics_analytics_by_model_provider`). They are **not** files under
`$SPLUNK_HOME/var/lib/splunk/modinputs/`; deleting anything there does
nothing to this add-on's checkpoint state.

## Build and packaging

Three pitfalls in the build pipeline are specific to this add-on and worth
understanding even though `TA_openrouter/scripts/build.sh` already guards
against the first two.

### The `inputHelperModule` trap

`globalConfig.json` must declare `inputHelperModule` on **every** input
service. Without it, `ucc-gen build` silently generates a self-contained stub
modular input that dumps the input's own stanza settings into events and
never calls the real helper module — never contacts OpenRouter, never
checkpoints. `openrouter_client.py`, `openrouter_transform.py`, and both
helper modules would ship as dead code. **Nothing in the release toolchain
catches this**: `slim validate` reports 0 errors against the stub, and
AppInspect precert reports 0 errors / 0 failures against it too — both gates
are validating the stub's own internal consistency, not whether it does
anything useful. `build.sh` now greps the *built* output
(`output/TA_openrouter/bin/openrouter_analytics.py` and
`openrouter_keys.py`) for a reference to each helper module and fails the
build loudly if either is missing; `tests/test_openrouter_globalconfig.py`
additionally guards the source `globalConfig.json` itself so the defect is
caught even before a build is attempted.

### The `python3` pin

`ucc-gen build` vendors the wheels listed in `package/lib/requirements.txt`
using whatever `python3` the build shell resolves to. An interactive or CI
shell very often resolves that to a newer interpreter (this repo's own
`.venv` is 3.11) — the build succeeds and `slim validate` says nothing about
it, but Splunk's runtime is CPython 3.9.25, and a 3.11-built wheel only fails
once Splunk actually tries to import it, which is a much worse place to
discover the mismatch. `build.sh` pins `--python-binary-name` explicitly:
first to `$TA_OPENROUTER_PYTHON39` if set, else to `/opt/splunk/bin/python3.9`
if present, else to a `python3.9` found on `PATH`; it exits with an error if
none resolves. **CI environments without a Splunk install** (no
`/opt/splunk/bin/python3.9`) must set `TA_OPENROUTER_PYTHON39` to a 3.9
interpreter path, or the build will refuse to run rather than silently
vendor the wrong wheels.

### `globalConfig.json` gets rewritten by the build

`ucc-gen build` rewrites `TA_openrouter/globalConfig.json` in place as a side
effect of building — it reformats indentation and adds a schema-default
`"required": true` to entries that did not explicitly specify it. This is a
cosmetic, semantically inert change, but left unhandled it would show up as a
`git status` diff after every build even though nothing about the add-on's
behavior changed.

`scripts/build.sh` handles this automatically, with no manual step required.
Before invoking `ucc-gen build`, it copies `globalConfig.json` to a temp file
with a plain file copy, then restores that copy over `globalConfig.json` via
a bash `EXIT` trap — which fires on success, on a normal error exit under
`set -e`, and on a signal, so the restore happens regardless of how the
build ends. The restore prints `==> restored
TA_openrouter/globalConfig.json to its pre-build state` when it runs. A
plain file copy is used deliberately instead of `git checkout`: it restores
exactly the bytes that were on disk before the build ran (rather than
jumping to git HEAD), so it works in a non-git checkout and — more
importantly — so it can't clobber a deliberate uncommitted edit an operator
made to `globalConfig.json` themselves. Do not run `git checkout
TA_openrouter/globalConfig.json` after building: that would discard exactly
the kind of uncommitted edit this restore mechanism exists to protect.

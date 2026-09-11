# TA_openrouter — Design

**Date:** 2026-07-31
**Status:** Approved design, revised against the verified OpenAPI spec
**Relates to:** `TA_anthropic` (same repo, independent code)

---

## Goal

A second, shippable Splunk Technology Add-on — `TA_openrouter` — that ingests
OpenRouter's management API and reports on model-mix spend attribution and API
key governance. Shippable means the same bar `TA_anthropic` met: AppInspect
precert clean, `slim`-packaged, documented, and installable by someone who did
not build it.

It shares a repository with `TA_anthropic` and shares nothing else.

## Decisions

Settled during brainstorming; not open questions.

| Decision | Choice |
| --- | --- |
| Purpose | Second shippable add-on — not a scratch experiment |
| Use case | **Both** model-mix spend attribution *and* shadow-AI key governance |
| Credential situation | Management key obtained; account is **individual**, not an organization |
| Repository | Same `conf26` repo, sibling to `TA_anthropic` |
| Code sharing | **None.** Independent fork — no shared modules, no shared tooling |

Nothing outside `TA_openrouter/` is modified: not `scripts/build.sh`, not
`tests/conftest.py`, not `mockserver/`. `TA_anthropic` is six weeks from a
conference, at 58 passing tests and AppInspect-clean, and the demo depends on it.
Restructuring shared scaffolding to save ~50 lines of boilerplate is a bad trade
against that.

## API surface — verified

Verified against `https://openrouter.ai/openapi.json` (OpenAPI 3.1.0, 69 paths)
plus a live `GET /analytics/meta` call. Earlier revisions of this document were
based on documentation-page summaries and were wrong in three places; those
corrections are noted inline.

**Base URL:** `https://openrouter.ai/api/v1` (from the spec's `servers` block).

**Auth:** `Authorization: Bearer <management key>`. Management keys are
self-serve from `https://openrouter.ai/settings/management-keys` and carry the
`sk-or-v1-` prefix — the *same* prefix as ordinary inference keys. The two are
distinguishable only by which page created them, so "wrong key type" stays a live
hypothesis for any 401.

### Endpoint reachability (probed with a live management key)

| Endpoint | Status | Use |
| --- | --- | --- |
| `POST /analytics/query` | 200 | **Primary usage input** |
| `GET /analytics/meta` | 200 | Vocabulary discovery |
| `GET /keys` | 200 | Key roster input |
| `GET /activity` | 200 | Not used — see below |
| `GET /credits` | 200 | Out of scope for v0.1.0 |
| `GET /organization/members` | **404** | Individual account — input dropped |

### Why `/analytics/query` and not `/activity`

`GET /activity` returns rows of `date`, `model`, `model_permaslug`,
`endpoint_id`, `provider_name`, `usage`, `byok_usage_inference`, `requests`,
`prompt_tokens`, `completion_tokens`, `reasoning_tokens` — daily only, last 30
completed UTC days, unpaginated.

**It cannot attribute usage to keys.** `api_key_hash` is accepted as a *filter*
but is not present in the response, so per-key attribution would require one call
per key. `POST /analytics/query` exposes `api_key_id` as a groupable dimension
that accepts the 64-char hash and resolves it server-side.

`/activity` is also daily-only, whereas `/analytics/query` supports
minute/hour/day/week/month.

### `POST /analytics/query`

| Field | Notes |
| --- | --- |
| `metrics` | **Required**, min 1 |
| `dimensions` | **Max 2** |
| `granularity` | `minute` / `hour` / `day` / `week` / `month` |
| `time_range` | `{start, end}`, ISO 8601 date-time |
| `filters` | `{field, operator, value}`; operators `eq neq in not_in gt gte lt lte` |
| `limit` | Total rows, **default 1000** |
| `group_limit` | Max rows per dimension combination |
| `order_by` | `{field, direction}` |

Response: `data.data` is an array of **untyped** row objects whose shape depends
on the requested metrics and dimensions; `data.metadata` carries `row_count`,
`query_time_ms`, and **`truncated`**; `data.warnings` reports unresolvable filter
values.

**Dimensions available:** `model`, `variant`, `api_key_id`, `provider`, `origin`,
`country`, `data_region`, `finish_reason`, `workspace`, `app`, `user`,
`external_user`, `context_length_bucket`, `generation_id`, `session_id`.

### `GET /keys`

Params `include_disabled`, `offset`, `workspace_id`. **Returns only the default
workspace's keys unless `workspace_id` is supplied** — multi-workspace orgs need
enumeration. Per key: `hash`, `name`, `label`, `disabled`, `created_at`,
`updated_at`, `expires_at`, `limit`, `limit_remaining`, `limit_reset`,
`include_byok_in_limit`, `creator_user_id`, `workspace_id`, `usage`,
`usage_daily`, `usage_weekly`, `usage_monthly`, `byok_usage`, and its daily,
weekly, and monthly variants.

### How it differs from the Anthropic Admin API

| | `TA_anthropic` | `TA_openrouter` |
| --- | --- | --- |
| Auth header | `x-api-key` | `Authorization: Bearer` |
| Usage endpoint | `GET`, fixed schema | **`POST` with a query body** |
| Response shape | Fixed | **Untyped rows, query-dependent** |
| Granularity | 1m / 1h / 1d — uses hourly | minute / hour / day / week / month |
| Cost units | **cents** (needs `/100`) | **USD** (no divisor) |
| Metric semantics | All additive | **14 of 35 are rates — must not be summed** |
| Key directory | `/v1/organizations/api_keys` | `/keys`, default workspace only |
| Pagination | `has_more`→`next_page` / `→last_id` | `offset` on `/keys`; row `limit` + `truncated` on analytics |
| Join key | `api_key_id` | SHA-256 key hash via `api_key_id` dimension |

## Layout

Self-contained under one directory. Nothing outside it changes.

```
TA_openrouter/
  README.md                        product README
  globalConfig.json                UCC: account + 2 inputs
  package/
    bin/
      openrouter_client.py         pure HTTP — Bearer auth, POST query, retry
      openrouter_transform.py      pure logic — windows, row flattening, metrics
      openrouter_analytics_helper.py
      openrouter_keys_helper.py
    default/
      props.conf  transforms.conf  savedsearches.conf  macros.conf
      data/ui/    views + nav
    lookups/
      openrouter_key_baseline.csv  headers only
  mockserver/                      own FastAPI replay + fixtures
  tests/                           own tests + conftest
  scripts/build.sh                 own ucc-gen → slim validate → slim package
  docs/                            architecture, operations, schema verification
```

`ucc-gen build --source package` reads only `package/`, so sibling directories
cannot leak into the artifact. `TA_openrouter/scripts/build.sh` invokes the
repo-root `.venv/bin/ucc-gen` and `.venv/bin/slim` as a read-only dependency.
Extraction into its own repository later is a `git mv` of one directory.

## Ingest

### Inputs

| Input | Call | Sourcetype | Checkpoint |
| --- | --- | --- | --- |
| `openrouter_analytics` | `POST /analytics/query` x2 | `openrouter:analytics`, `openrouter:providers` | last completed hour, **one key per query** |
| `openrouter_keys` | `GET /keys` | `openrouter:keys` | none — snapshot |

The analytics input issues **two** queries per interval — the dimension cap of 2
prevents combining them — and writes each to its own sourcetype. They share an
input (one interval, one index, one operator-facing configuration) but hold
**separate checkpoint keys**, suffixed per query.

Separate keys matter because of truncation. Both queries cover the same window,
but if one truncates and the other does not, a shared checkpoint would either
stall the healthy query or force it to re-ingest a window it already emitted.
Independent keys let each recover on its own without drifting the configuration.

`openrouter_keys` follows `anthropic_directory_helper.py`: no checkpoint, emit
with a `snapshot_at`, let downstream searches dedup.

Checkpoints use the KV Store keyed per input instance name, mirroring
`anthropic_usage_helper.py:87`, so multiple configured inputs never collide.

The account config carries an `api_base_url` field so the mock server can be
substituted for the live API.

### The two queries

Both use the same metric list and granularity, differing only in `dimensions`.

**Q1 — attribution.** Sourcetype `openrouter:analytics`.

```json
{
  "metrics": ["request_count", "total_usage", "tokens_total", "tokens_prompt",
              "tokens_completion", "reasoning_tokens", "cached_tokens",
              "byok_usage"],
  "dimensions": ["api_key_id", "model"],
  "granularity": "hour",
  "time_range": {"start": "<checkpoint_q1>", "end": "<last complete hour>"}
}
```

Hour x key x model. Aggregating over `model` gives per-key spend for the
governance dashboard and the spend-spike alert; aggregating over `api_key_id`
gives the model mix; keeping both gives per-key model mix.

**Q2 — provider routing.** Sourcetype `openrouter:providers`. Identical except:

```json
  "dimensions": ["model", "provider"],
  "time_range": {"start": "<checkpoint_q2>", "end": "<last complete hour>"}
```

Hour x model x provider. This is the distinctly OpenRouter view: the same model
slug is served by different upstream providers, at different prices and different
latencies, and routing shifts without the caller doing anything. No
single-vendor add-on can show it.

The two are joined on `model` at search time. They are separate calls because
`dimensions` caps at 2 and all three of `api_key_id`, `model`, and `provider` are
needed; `model` is the shared axis, so it appears in both.

The truncation rule below applies **per query**: a truncated response blocks its
own checkpoint only. A failure in Q2 must never stall Q1, and vice versa.

### Metric selection: additive only in v0.1.0

Fourteen of the 35 metrics carry `is_rate: true` — `avg_latency`,
`p50_latency`, `p90_latency`, `p99_latency`, `avg_throughput`, `p50_throughput`,
`p90_throughput`, `p99_throughput`, `cache_hit_rate`,
`blended_cost_per_million_tokens`, `guardrail_invoked_rate`,
`response_cached_rate`. Summing a rate across time buckets or dimension values
produces a meaningless number, and Splunk will do it silently.

v0.1.0 ingests **only additive metrics**, which removes the hazard entirely.
Rate metrics are deferred until the additive path is proven; when added, each
must be marked in `props.conf` and the dashboards must use `avg` or `latest`
rather than `sum`. `cache_hit_rate` is derivable from `cached_tokens` /
`tokens_prompt` in the meantime.

### Transform rules

`openrouter_transform.py` imports nothing from Splunk and is fully unit-testable,
following the discipline of `anthropic_transform.py`.

**1. Cost is USD. There is no divisor.** Every currency metric is documented in
USD, and `/analytics/meta` reports `display_format: "currency"` for each.
`TA_anthropic`'s `flatten_cost` divides by 100 because the Anthropic Admin API
documents *cents* — that is correct there and wrong here. Fields are emitted as
`*_usd`. An earlier revision of this document asserted the opposite; it was based
on a documentation summary reading "credits" and was incorrect.

**2. Rows are untyped and query-driven.** `data.data` contains whatever the
request asked for. `flatten_analytics` is parameterised by the metric and
dimension lists actually sent, not by a hardcoded schema, and tolerates missing
keys rather than raising.

**3. Truncation is a correctness failure, not a warning.** With `limit`
defaulting to 1000, an hourly window across many keys and models can overflow —
24 hours x 5 keys x 10 models is 1,200 rows. If `metadata.truncated` is true the
helper **must not advance the checkpoint**; it narrows the window and retries.
Advancing past a truncated response loses data permanently and silently. This is
the highest-consequence rule in the module.

**4. Complete buckets only.** `compute_window` releases an hour only once it has
closed, mirroring `TA_anthropic`'s bucket-boundary logic. Re-running inside the
same hour correctly yields no new events.

## Dashboards, alerts, lookups

### Dashboards

**Model Mix & Spend.** Spend by model over time
(`timechart span=1h sum(total_usage_usd) by model`); token volume by model split
across prompt, completion, reasoning, and cached; a top-models table with
requests, tokens, spend, and spend-per-request; and per-key model mix — the panel
that answers "we issued one key per team, where is the money going."

**Provider Routing.** Built on `openrouter:providers`. Spend and request share by
provider over time; a provider-by-model matrix showing which upstreams served each
model slug; and spend-per-request by provider for the same model, which is where
a routing shift becomes visible as a cost change nobody requested.

This dashboard has no analogue in `TA_anthropic` and could not have one — it
reports on a routing layer that a single-vendor API does not have.

**Key Governance.** Keys observed in analytics but absent from the baseline
lookup; the key roster from `openrouter:keys` with name, label, creation date,
disabled flag, spending limit, remaining limit, and rolling usage; spend by key
over time; and a drill-down on an unrecognised key showing which models it
reaches and at what cost.

Both join on the SHA-256 key hash — `api_key_id` in analytics rows, `hash` on
`/keys`.

### Lookup

`openrouter_key_baseline.csv` ships with **headers only**, populated by an
operator-run baseline saved search via `outputlookup`. This deliberately differs
from `TA_anthropic`, which ships pre-populated with two demo key IDs — correct
for a demo artifact, wrong for a product, where it would make every installer's
first run flag every real key as unrecognised.

### Alerts

**Unrecognised key active.** A key hash present in analytics and absent from the
baseline. A set-membership test.

**Hourly spend spike by key.** Because `granularity: "hour"` is available, the
`TA_anthropic` shape ports: `-48h` search window, `streamstats window=6` over
hourly buckets, per key hash. The earlier revision of this document redesigned
this around daily buckets on the belief that OpenRouter was daily-only; that
belief came from `/activity` and does not apply to `/analytics/query`.

The deviation multiplier is still derived from scratch against fixture data in
`TA_openrouter/tests/test_openrouter_alerts.py` and recorded there. Nothing from
`tests/test_alerts.py` is copied, but the shape is now the same.

### Configuration lessons carried from TA_anthropic

- Alert triggers use `counttype` / `relation` / `quantity`. The names
  `alert_type`, `alert_comparator`, and `alert_threshold` are REST API arguments,
  absent from `savedsearches.conf.spec`, and rejected by `slim validate`.
- `TZ = UTC` is load-bearing: `TIME_FORMAT = %Y-%m-%dT%H:%M:%SZ` treats the
  trailing `Z` as a literal, so without `TZ` the indexer applies its local offset.
- No `TIMESTAMP_FIELDS` under `KV_MODE = json`. It applies only with
  `INDEXED_EXTRACTIONS`, so it is a no-op that invites deletion of the
  `TIME_PREFIX` / `TIME_FORMAT` / `TZ` lines doing the real work.

### One bug not carried

Every dashboard panel and saved search goes through an `openrouter_index` macro
rather than relying on the role's default indexes. The macro is defined in
`macros.conf` shipping as `index=*`, so the add-on works regardless of which index
the operator configured; narrowing is a local override.

`HANDOFF.md`'s Session 2 log records a latent bug in `TA_anthropic` where saved
searches filtered `sourcetype=anthropic:*` with no `index=`, so data landing
outside the role's default indexes produced a silently empty dashboard with
nothing visibly broken. A hardcoded index would fail Splunkbase review; the
implicit-default-index version fails quietly, which is worse.

## Mock server and testing

### Mock

`TA_openrouter/mockserver/` — FastAPI serving `POST /analytics/query` and
`GET /keys`. The analytics mock must honour `metrics`, `dimensions`,
`granularity`, and `time_range` from the request body and shape its rows
accordingly, since the real endpoint's response is query-driven. It must also be
able to return `metadata.truncated: true` on demand so the truncation path is
testable. Auth checking verifies only that an `Authorization` header is present.

**Anchored to the UTC hour, not to import time.** `TA_anthropic`'s mock freezes
its anchor at import so repeated polls are byte-identical, which produces its
worst documented demo hazard: restarting mints a second set of buckets at new
timestamps that slip past the checkpoint, visibly doubling the rogue key's
totals. Anchoring to the current UTC hour means a restart within the same hour
regenerates identical buckets, so there is nothing to double-ingest.

### Fixtures

Three key hashes: two sanctioned, one absent from the baseline. Rows spread
across several models at varying cost so the model-mix dashboard has real shape.
A spend spike on the unrecognised key in a recent hour, sized to clear the
`streamstats window=6` threshold. A fixture case that overflows the row limit so
truncation handling is exercised.

For Q2, at least one model slug must be served by **two different providers** at
different per-request cost, and at least one model must show a routing shift
across the window — one provider dominant early, another later. Without that the
provider dashboard renders but demonstrates nothing, and the panel that matters
most (spend-per-request by provider for the same model) has no signal.

`test_openrouter_fixtures.py` asserts all of these properties so fixtures cannot
drift from the scenario they support.

### Test file naming

Test files are named `test_openrouter_*.py`. Running `pytest` from the repository
root collects both trees; the existing `tests/` already contains
`test_client.py`, `test_transform.py`, `test_alerts.py`, `test_mockserver.py`, and
`test_fixtures.py`. With no `__init__.py` in either tree, duplicate basenames
raise *import file mismatch*. Adding `__init__.py` to both would mean editing
`TA_anthropic`'s, which the code-sharing decision forbids.

### Coverage, weighted by consequence

**Truncation handling gets the most tests.** A truncated response with an advanced
checkpoint loses data permanently and silently — the same class of failure as a
mis-scheduled window, and the only one here that cannot be recovered by
re-running.

Then: complete-bucket-only window computation; no-complete-bucket returning
`None`; checkpoint resume; Bearer header construction; the `offset` paging loop on
`/keys`; retry on 429 and 5xx; row flattening against metric and dimension lists;
and the alert threshold arithmetic derived against fixture data.

### Gates

- Unit tests pass
- `slim validate` — 0 errors
- AppInspect precert — 0 errors, 0 failures
- No `__pycache__`, `.pyc`, or `.DS_Store` in the packaged artifact
- **Every module in `package/bin/` compiles under Python 3.9.** Splunk's app
  runtime here is 3.9.25 while the development venv is 3.11. Easy to skip,
  expensive to discover after packaging.

## Documentation

Separate from the conf26 documentation set, under `TA_openrouter/`:

- `README.md` — installation, configuration, inputs, dashboards
- `docs/architecture.md` — the client / transform / helper split for a
  maintainer, why the transform has no Splunk imports, where the checkpoint
  lives. Not a copy of this document, which is a decision record.
- `docs/operations.md` — baseline lookup population, the index macro, complete
  bucket semantics, truncation behaviour, the additive-versus-rate metric
  distinction
- `docs/schema-verification.md` — which fields are confirmed against a live
  management key versus taken from the OpenAPI spec

## Known unknowns

The two original unknowns are resolved: `/activity` is unpaginated and its `date`
parameter is optional. Both are moot now that `/analytics/query` is the primary
input. Two remain, neither blocking:

1. **Retention window of `/analytics/query`.** `/activity` is capped at 30
   completed UTC days. `/analytics/query` accepts an arbitrary `time_range` and
   the spec states no limit. Until measured, `backfill_days` defaults
   conservatively to 30 and `docs/operations.md` records the real figure once
   observed.
2. **Row-limit behaviour under `group_limit`.** The interaction between `limit`,
   `group_limit`, and auto-computed defaults on time-series queries is described
   but not precisely specified. The truncation rule above is written to be safe
   regardless of the exact semantics.

## Out of scope

- `GET /activity` — superseded by `/analytics/query`
- `GET /credits` and any low-balance alert
- `GET /organization/members` — returns 404 on this individual account; the
  `user` and `external_user` dimensions go with it
- Rate metrics — deferred until the additive path is proven
- `generation_id` and `session_id` dimensions — cardinality would overflow the
  row limit immediately
- Multi-workspace key enumeration via `workspace_id`
- Any modification to `TA_anthropic`, `scripts/`, root `tests/`, or root
  `mockserver/`
- Splunkbase submission

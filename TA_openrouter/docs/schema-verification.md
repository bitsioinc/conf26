# OpenRouter API Response Schema Verification

## Status

**VERIFIED (partial)** — `TA_openrouter/scripts/record_fixtures.py` was executed
against the **live** OpenRouter Management API on 2026-07-31. It wrote four
response bodies, verbatim, to `TA_openrouter/openrouter_mockserver/fixtures/`:

- `analytics_meta.json` — the full metric/dimension catalogue (1 response, untouched since)
- `keys.json` — 2 real keys as recorded
- `analytics_by_key_model.json` — 1 real row as recorded
- `analytics_by_model_provider.json` — 1 real row as recorded

The account under test has almost no traffic, so each analytics query
returned exactly one row and the roster held exactly two keys. Those four
recordings are the entirety of what has been **observed**. Everything below
is marked **VERIFIED** only for what appears in an actual response body, and
**ASSUMED** for anything sourced from `analytics_meta.json` (the API's own
catalogue of possible fields) or the OpenAPI spec but never seen on the wire
in a row.

Because the live account had almost no data, the three replayable fixtures
(`keys.json`, `analytics_by_key_model.json`, `analytics_by_model_provider.json`)
were subsequently **enriched with hand-authored synthetic rows**, added
around the real recorded ones, to encode the demo scenario the dashboards
and alerts need (Task 5 Step 4 / Task 6 Step 3's documented fallback). The
real rows and keys were preserved byte-for-byte, with the single exception
recorded under "Credential sanitization" below. See "Real vs. synthetic data"
at the end of this document for the exact accounting. `analytics_meta.json`
was left completely untouched.

### Credential sanitization — 2026-08-05

`Alpaca`'s recorded `hash` and `label`, and the `creator_user_id` /
`workspace_id` shared by both roster entries, were replaced with synthetic
values. They were real account identifiers, they are projected in public on
slide 20 of the conference deck, and this repository is intended to be
published. What replaced them:

| Field | Now holds |
| --- | --- |
| `Alpaca.hash` | `sha256(b"synthetic-fixture-alpaca-demo-key-conf26-dev1194")` |
| `Alpaca.label` | `sk-or-v1-syn...001` |
| `creator_user_id` (both keys) | `user_SYNTHETICFIXTUREUSER0000001` |
| `workspace_id` (both keys) | `00000000-0000-4000-8000-000000000001` |

Every other recorded value — names, timestamps, limits, usage figures — is
still byte-for-byte what the live call returned, so no dashboard number moved.
The deck's slide-20 capture predates this change and had the old hash and label
repainted in place to match it. No copy of the pre-sanitization capture was
kept in the repository — re-capturing the dashboard from the sanitized fixtures
regenerates the same image from scratch, so there was no reason to keep an
image of the old values around.

## THE SINGLE MOST SURPRISING FINDING

> **`api_key_id` in analytics rows carries the key's `name` (e.g. `"Alpaca"`),
> not its `hash`.** The roster returned by `/keys` identifies each key
> primarily by `hash` (a 64-hex-char digest) with `name` as a separate,
> human-readable field. The analytics endpoints join on `name`, not `hash`.
>
> This is VERIFIED directly: the real `analytics_by_key_model.json` row has
> `"api_key_id": "Alpaca"`, and the real `keys.json` roster has an entry with
> `"name": "Alpaca"` and a completely different-looking `"hash"` field. There
> is no row anywhere with `api_key_id` equal to any recorded `hash` value.
>
> **This drives the alert logic.** Any saved search or lookup that joins
> `openrouter:analytics` traffic against the `openrouter:keys` roster (e.g.
> the "Unrecognised API Key Active" alert in Task 11, or the Key Governance
> dashboard) MUST join `api_key_id` to the roster's `name` field. Joining
> against `hash` instead would make every single key look "unrecognised",
> since no `api_key_id` value will ever equal a `hash` value.

## Time-Bucket Column

**Plan assumption falsified.** The plan (and the pre-recording placeholder
version of this document) assumed a single column named `date`. That is
wrong. VERIFIED, per endpoint:

| Query dimensions | Time-bucket column | Status |
|---|---|---|
| `[api_key_id, model]` | **`date__hour`** | VERIFIED (observed in `analytics_by_key_model.json`) |
| `[model, provider]` | **`created_at__hour`** | VERIFIED (observed in `analytics_by_model_provider.json`) |

These are two **different** column names for what is conceptually the same
kind of value (the bucket start timestamp), and the `__hour` suffix tracks
the requested `granularity` request parameter (`hour` in both recordings;
ASSUMED — not verified — that a `granularity` of `day`/`week`/`month`/`minute`
would produce `date__day` / `date__week` / etc. by the same pattern, since
`analytics_meta.json` lists those granularities but no query at a different
granularity was ever recorded).

Left uncorrected (hardcoded `time_field="date"`), `flatten_analytics()`
would silently skip every row (`if not stamp: continue`), and both modular
inputs would ingest zero events with no error surfaced anywhere. The
transform module (`TA_openrouter/package/bin/openrouter_transform.py`) has
already been updated in a prior task to accept a caller-supplied
`time_field` and to parse the space-separated timestamp shape described
below — this document exists to pin down the facts that fix depends on.

## Timestamp Format

VERIFIED: timestamps inside analytics rows (`date__hour`, `created_at__hour`)
are **space-separated and carry no UTC offset**:

```
"2026-07-31 18:00:00"
```

This is **not** RFC 3339 (`2026-07-31T18:00:00Z`). The plan's fixture
recorder (`record_fixtures.py`) sends `time_range.start` / `time_range.end`
as RFC 3339 strings in the *request* — that request-side format is ASSUMED
correct only because the live call succeeded (200 response), not because a
request-format error case was ever observed. The *response* row timestamps
are the space-separated shape above, confirmed on both recorded rows. All
observed values are UTC wall-clock time with no explicit indicator saying so
— this is an inference from context (the account's configured window and
`analytics_meta.json`'s framing), not a field the API labels.

## Dimension Columns

### Query 1: `[api_key_id, model]` → fixture `analytics_by_key_model.json`
- `api_key_id` — VERIFIED. Value observed: `"Alpaca"` (a roster **name**, not a `hash` — see above).
- `model` — VERIFIED. Value observed: `"moonshotai/kimi-k2.6-20260420"`.

### Query 2: `[model, provider]` → fixture `analytics_by_model_provider.json`
- `model` — VERIFIED. Same value as above.
- `provider` — VERIFIED. Value observed: `"Baidu"`.

### Other dimensions (never queried, never observed in a row)
`analytics_meta.json` (ASSUMED catalogue only — ASSUMED, not observed in a
row) also lists: `variant`, `origin`, `country`, `data_region`,
`finish_reason`, `workspace`, `app`, `user`, `external_user`,
`context_length_bucket`, `generation_id`, `session_id`. None of these were
requested or returned by the two recorded queries.

## Metric Columns and Observed JSON Types

Both recorded rows carry the same 8 metric fields (the recorder requests
exactly this set). VERIFIED types, per response:

| Field | `analytics_by_key_model.json` (real row) | `analytics_by_model_provider.json` (real row) |
|---|---|---|
| `request_count` | `"3"` — **string** | `"3"` — **string** |
| `total_usage` | `0.012887` — **float** | `0.012887` — **float** |
| `tokens_total` | `"10800"` — **string** | `"10800"` — **string** |
| `tokens_prompt` | `"6278"` — **string** | `"6278"` — **string** |
| `tokens_completion` | `"4522"` — **string** | `"4522"` — **string** |
| `reasoning_tokens` | `"4373"` — **string** | `4373` — **int** |
| `cached_tokens` | `"4131"` — **string** | `"4131"` — **string** |
| `byok_usage` | `0` — **int** | `0` — **int** |

**`reasoning_tokens` is the one field whose JSON type is inconsistent
between the two endpoints** — string in `[api_key_id, model]`, int in
`[model, provider]` — for what is otherwise an identical row (same model,
same hour, same underlying value 4373). This is VERIFIED, not a
transcription artifact: both fixture files were read directly. Every other
metric field's type is consistent across both recorded rows.

Because only one row per endpoint was ever observed, whether this
string/int split is a genuine per-endpoint contract (every row from
`[model, provider]` always sends `reasoning_tokens` as int) or coincidental
to this one pair of rows is ASSUMED, not proven — there is exactly one data
point per endpoint. The synthetic rows added to each fixture (see below)
mirror the per-endpoint pattern observed in that fixture's own real row
(string throughout `analytics_by_key_model.json`, int throughout
`analytics_by_model_provider.json`), which is a deliberate authoring choice
to stay consistent with the one example available, not an additional
verified fact.

`total_usage` being a bare float (not a string, unlike every other numeric
metric) and `byok_usage` being a bare int are both VERIFIED and consistent
across both recorded rows.

The remaining metrics in `analytics_meta.json`'s catalogue (`avg_latency`,
`p50_latency`, `p90_latency`, `p99_latency`, `cache_hit_rate`,
`blended_cost_per_million_tokens`, `avg_throughput`, `p50/p90/p99_throughput`,
`guardrail_invoked_count`, `guardrail_invoked_rate`, `response_cached_count`,
`response_cached_rate`, `credits_usage`, `openrouter_usage`, `byok_fees`,
`byok_request_count`, `usage_upstream`, `usage_cache`, `usage_data`,
`usage_web`, `usage_upstream_web`, `usage_file`, `usage_upstream_file`,
`usage_web_fetch`, `usage_upstream_web_fetch`) are ASSUMED only — none were
requested by the recorder and none appear in a response row.

## `metadata` Object

VERIFIED, identical shape on both recorded responses:

```json
"metadata": {
  "query_time_ms": 161,
  "row_count": 1,
  "truncated": false
}
```

Keys: `query_time_ms` (int), `row_count` (int), `truncated` (bool).

**No `warnings` key is present.** The plan's mock server implementation
(`TA_openrouter/openrouter_mockserver/app.py`, Task 5) synthesizes `"warnings": []` on
every response — that is the mock's own invention for schema completeness,
not something the live API ever sent. Real responses have no `warnings` key
at all. This is VERIFIED absent, not merely unobserved.

The full response envelope also carries a `cachedAt` key (VERIFIED,
epoch-milliseconds int, e.g. `1785527648977`) as a sibling of `data` and
`metadata` inside the outer `"data"` object. It is not documented anywhere
in the plan or `analytics_meta.json` and is not read by
`openrouter_transform.py`; it is recorded here only because it was observed
on the wire, not because anything depends on it.

## `/keys` Field List

VERIFIED — both recorded keys return exactly these 21 fields, in this
order:

```
hash, name, label, disabled, limit, limit_remaining, limit_reset,
include_byok_in_limit, usage, usage_daily, usage_weekly, usage_monthly,
byok_usage, byok_usage_daily, byok_usage_weekly, byok_usage_monthly,
created_at, updated_at, expires_at, creator_user_id, workspace_id
```

Notable VERIFIED facts:
- `hash` is a 64-character lowercase hex string (looks like a SHA-256 digest of the full API key). This is the roster's primary identifier — **but analytics rows do not reference it** (see above).
- `name` is the human-readable identifier that analytics rows' `api_key_id` actually matches.
- `label` is an already-truncated display form of the key (e.g. `"sk-or-v1-57e...5fe"`), never the full secret.
- `limit_reset`, `expires_at` were `null` on both real keys.
- `updated_at` was `null` on one recorded key (`Alpaca`, which had never been updated since creation) and a timestamp on the other — so `null` is a legitimate, VERIFIED value for this field, not just an unset-field placeholder.
- Currency-shaped fields (`usage`, `usage_daily`, `usage_weekly`, `usage_monthly`, `byok_usage*`, `limit`, `limit_remaining`) are bare JSON numbers (int or float), never strings — unlike the analytics endpoint's metric fields, which are frequently strings. The two endpoints do **not** share a numeric-typing convention.
- The response envelope is `{"data": [ ...keys... ]}` — a bare list, no `metadata` object, unlike `/analytics/query`.

The top-level response is a GET with no request body; the recorder calls
`/keys?include_disabled=true`, and both real keys returned had
`"disabled": false` — so whether `include_disabled=true` actually changes
the result set (vs. always returning all keys) was never exercised, and is
therefore UNVERIFIED / not applicable to what's recorded.

## API Endpoints Captured

1. `GET /analytics/meta` — metadata definitions (fixture: `analytics_meta.json`) — VERIFIED shape, recorded verbatim, never modified afterward.
2. `GET /keys?include_disabled=true` — key inventory (fixture: `keys.json`) — VERIFIED shape; enriched with 1 synthetic key (see below).
3. `POST /analytics/query` with `dimensions: [api_key_id, model]` (fixture: `analytics_by_key_model.json`) — VERIFIED shape; enriched with 27 synthetic rows (see below).
4. `POST /analytics/query` with `dimensions: [model, provider]` (fixture: `analytics_by_model_provider.json`) — VERIFIED shape; enriched with 18 synthetic rows (see below).

## Real vs. Synthetic Data — Exact Accounting

The live account had almost no traffic (1 analytics row per query, 2 keys),
nowhere near enough to exercise the dashboards or alert-threshold logic.
Per the plan's documented fallback (Task 5 Step 4, Task 6 Step 3, ratified
by the maintainer as "enrich, keeping real shapes"), each of the three
replayable fixtures below was enriched with hand-authored synthetic rows
that match the real, VERIFIED wire format exactly (space-separated
offset-free timestamps, string-typed metric fields where the real rows use
strings, float `total_usage`, int `byok_usage`, the same 21-field key shape,
the same `metadata` shape). `analytics_meta.json` was **not** touched.

### `keys.json`

> **Amended 2026-08-05 — the second recorded key was removed from the
> committed fixtures.** It carried a real key hash, a personal key name and
> real lifetime spend, all of which were being projected on a conference
> slide, so its roster entry and its seven analytics rows were deleted. The verification statements below
> still describe what the live `/keys` call returned at recording time — that
> history is not rewritten — but the roster now holds **two** keys, `Alpaca`
> and `Nimbus_Prod_Key`. The closing section of this document is amended to
> match.

- **Real (recorded; `hash`, `label` and the two account IDs sanitized on 2026-08-05, everything else byte-for-byte):** `Alpaca` — one of the two keys returned by the live `/keys` call; the other was removed (see above).
- **Synthetic (authored):** `Nimbus_Prod_Key` — a second sanctioned key in the roster, so downstream tests have more than one key to join against, and so it can appear as the spending key in the spend-spike scenario below.
- Total (after the 2026-08-05 removal): 2 keys, all 21 fields, all with a `hash`. It was 3 as originally recorded and authored.
- **Deliberately absent from the roster:** `Shadow_Intern_Key` — this name appears in `analytics_by_key_model.json`'s `api_key_id` column but has **no** corresponding roster entry anywhere. This is the shadow-AI reveal the Key Governance dashboard needs: a key producing real traffic that was never issued through (or has since been scrubbed from) the sanctioned roster.

### `analytics_by_key_model.json`
- **Real (recorded, byte-for-byte unchanged, present exactly once):**
  ```json
  {
    "date__hour": "2026-07-31 18:00:00", "api_key_id": "Alpaca",
    "model": "moonshotai/kimi-k2.6-20260420", "request_count": "3",
    "total_usage": 0.012887, "tokens_total": "10800", "tokens_prompt": "6278",
    "tokens_completion": "4522", "reasoning_tokens": "4373",
    "cached_tokens": "4131", "byok_usage": 0
  }
  ```
- **Synthetic (authored), 27 additional rows** spanning 7 consecutive hourly
  buckets from `2026-07-31 12:00:00` through `2026-07-31 18:00:00` (the real
  row's own bucket is the newest, so the series ends on the real, recorded
  hour):
  - `Alpaca` — 6 more rows (12:00-17:00), same model as the real row, mild variation, so the real 18:00 row sits inside a plausible trailing series rather than standing alone.
  - ~~The second recorded key — 7 rows, model `openai/gpt-5.2-codex`, heavier "power user" traffic.~~ **Removed 2026-08-05** along with its roster entry; see the amendment under `keys.json` above.
  - `Shadow_Intern_Key` — 7 rows, model `openai/gpt-5.2-codex`, creeping unsanctioned usage, absent from the roster (see above).
  - `Nimbus_Prod_Key` — 7 rows, model `moonshotai/kimi-k2.6-20260420`; the trailing 6 buckets are stable/mildly varying, and the final (18:00) bucket carries a dramatic spend spike (a runaway reasoning loop: request count and token volume both balloon) — this is the one key the sigma=3 spend-spike alert is designed to isolate.
  - Every metric field on every synthetic row matches the real row's per-field JSON type exactly (`request_count`/`tokens_*`/`cached_tokens` as digit strings, `reasoning_tokens` as a string throughout this fixture, `total_usage` as float, `byok_usage` as int `0`).
- Total (after the 2026-08-05 removal): 21 rows, 3 distinct `api_key_id` values, 2 distinct `model` values, 7 distinct `date__hour` buckets, every key present in all 7 buckets. It was 28 rows across 4 keys as originally recorded and authored.

### `analytics_by_model_provider.json`
- **Real (recorded, byte-for-byte unchanged, present exactly once):**
  ```json
  {
    "created_at__hour": "2026-07-31 18:00:00", "model": "moonshotai/kimi-k2.6-20260420",
    "provider": "Baidu", "request_count": "3", "total_usage": 0.012887,
    "tokens_total": "10800", "tokens_prompt": "6278", "tokens_completion": "4522",
    "reasoning_tokens": 4373, "cached_tokens": "4131", "byok_usage": 0
  }
  ```
- **Synthetic (authored), 18 additional rows**, same 7-bucket window
  (`2026-07-31 12:00:00` through `18:00:00`):
  - `moonshotai/kimi-k2.6-20260420` via `Baidu` — 6 more rows (12:00-17:00), mild variation, alongside the real 18:00 row (7 buckets total for this model/provider pair).
  - `moonshotai/kimi-k2.6-20260420` via `Fireworks` — 5 rows, a cheaper per-token route for the same model (about 23% less per request than Baidu in this fixture) — this is the provider cost-comparison signal the Provider Routing dashboard panel needs.
  - `openai/gpt-5.2-codex` via `OpenAI` — 7 rows, a second model/provider pair for variety.
  - Every synthetic row's `reasoning_tokens` is an **int**, matching the real row's type in this specific fixture (see the reasoning_tokens caveat above).
- Total: 19 rows, 7 distinct `created_at__hour` buckets, one model (`moonshotai/kimi-k2.6-20260420`) served by two providers with differing spend-per-request.

None of the synthetic data represents real OpenRouter traffic, real spend,
or a real credential. Model names (`moonshotai/kimi-k2.6-20260420` real,
`openai/gpt-5.2-codex` invented) and provider names (`Baidu` real,
`Fireworks`/`OpenAI` invented but modeled on real OpenRouter provider
routing options) were chosen to be plausible in `vendor/model` form; dollar
amounts were derived from token counts at a fixed per-model rate so spend
stays proportional to usage rather than being picked arbitrarily. The
synthetic `Nimbus_Prod_Key` roster entry's `hash` is a SHA-256 digest of an
arbitrary internal string (`hashlib.sha256(b"synthetic-fixture-nimbus-prod-key-...")`)
— it is not derived from, and does not correspond to, any real API key.

## Next Steps

- Task 6 (fixture integrity tests) and Task 11 (alert-threshold derivation)
  must be written against the field names and types recorded here — in
  particular `date__hour` / `created_at__hour` (not `date`), and joining
  `api_key_id` to the roster's `name` (not `hash`).
- Any test or search that assumes `reasoning_tokens` has a single fixed JSON
  type across all analytics responses is not supported by what has been
  observed; code that consumes it should coerce rather than assume.

## Field verification table (Task 13, Step 8)

The task brief this table was drafted from listed a single time-bucket
column named `date`. That was already falsified above — the real bucket
columns are `date__hour` and `created_at__hour`, and they differ per query —
so the row below replaces the brief's `date` row with both real columns
rather than repeating the error. `VERIFIED` means the field was observed in
one of the four live-recorded response bodies described at the top of this
document; `ASSUMED` means it comes from `analytics_meta.json`'s catalogue or
the OpenAPI spec but no recorded response body actually contained it. Every
row below happens to be `VERIFIED`, because every field this table lists is
one the shipped code actually reads, and this account's one real row per
analytics query and two real keys happened to populate all of them — the
fields that are genuinely `ASSUMED` (rate metrics such as `avg_latency`, and
unused dimensions such as `variant`/`origin`/`country`) are catalogued in
"Metric Columns and Observed JSON Types" and "Dimension Columns" above, and
are intentionally not repeated in this table since the code does not consume
them.

| Endpoint | Field | Used for | Status |
| --- | --- | --- | --- |
| `/analytics/query` | `date__hour` | event time, checkpoint — `[api_key_id, model]` query only | VERIFIED |
| `/analytics/query` | `created_at__hour` | event time, checkpoint — `[model, provider]` query only | VERIFIED |
| `/analytics/query` | `bucket_start` (add-on-injected, not a wire field) | copy of whichever bucket column the row carried, under one consistent name; the field to use for anything spanning both analytics sourcetypes | N/A — set by `openrouter_transform.flatten_analytics`, not returned by the API |
| `/analytics/query` | `api_key_id` | key attribution; the baseline/alert join key is the roster's `name`, **not** `hash` — see "THE SINGLE MOST SURPRISING FINDING" above | VERIFIED |
| `/analytics/query` | `model` | model mix, provider join | VERIFIED |
| `/analytics/query` | `provider` | provider routing | VERIFIED |
| `/analytics/query` | `total_usage` | spend (USD) | VERIFIED |
| `/analytics/query` | `request_count` | request volume | VERIFIED |
| `/analytics/query` | `tokens_total` | token volume | VERIFIED |
| `/analytics/query` | `tokens_prompt` | token split | VERIFIED |
| `/analytics/query` | `tokens_completion` | token split | VERIFIED |
| `/analytics/query` | `reasoning_tokens` | token split | VERIFIED — JSON type inconsistent across the two recorded queries (string in `[api_key_id, model]`, int in `[model, provider]`); see "Metric Columns" above |
| `/analytics/query` | `cached_tokens` | token split, cache rate | VERIFIED |
| `/analytics/query` | `byok_usage` | BYOK spend (USD) | VERIFIED |
| `/analytics/query` | `metadata.truncated` | checkpoint guard | VERIFIED — the only value ever observed live was `false`; `true` has only been exercised via the mock server and unit tests, never seen on an actual live response |
| `/keys` | `hash` | roster identity — **not** the field anything downstream joins on | VERIFIED |
| `/keys` | `name` | roster display, and the actual baseline/alert join key | VERIFIED |
| `/keys` | `label` | roster display | VERIFIED |
| `/keys` | `disabled` | roster display | VERIFIED — only value observed on either real key was `false` |
| `/keys` | `created_at` | roster display | VERIFIED |
| `/keys` | `limit` | roster display (USD) | VERIFIED |
| `/keys` | `limit_remaining` | roster display (USD) | VERIFIED |
| `/keys` | `usage` | roster display (USD) | VERIFIED |

## Provenance of the committed fixtures

These fixtures mix a small number of byte-for-byte recorded rows with a larger
number of authored ones; "Real vs. Synthetic Data" above gives the exact
accounting, row by row.

**No usable credential appears anywhere in this repository.** No recorded
fixture ever contained a full API key — `/keys` returns only an
already-truncated `label` (e.g. `"sk-or-v1-syn...001"`) — and a `hash` value
grants nothing without a management credential presented alongside it.

**Account identifiers were nevertheless replaced before publication.** The
recorded rows originally carried live account identifiers: key hashes, a
`creator_user_id`, and a `workspace_id`. Every one of them is now synthetic —
see "Credential sanitization" near the top of this document for the exact
replacements. One recorded key was removed from the fixtures outright rather
than sanitized, because its name and spend history were personal rather than
merely account-identifying.

What remains recorded is the part that carries the engineering value and
identifies nobody: field names, JSON types, timestamp formats, response
envelope shapes, and the metric figures the dashboards and alert thresholds are
derived from. That is why no dashboard number moved when the identifiers were
replaced.

`Alpaca` is a key *name*, and it stays. Every shipped dashboard, alert and test
joins on the key **name** rather than the hash, so renaming it is a change with
a silent-no-data failure mode — rename it only together with the fixtures,
tests and dashboards that reference it.

# Anthropic Add-on for Splunk (TA_anthropic)

Ingests organization-level AI usage telemetry from the Anthropic Admin API:
token usage by model, workspace and API key; daily cost; and the organization's
user and API-key directory. Built with Splunk's UCC framework.

Published as conference demo material for `.conf2026` session DEV1194. It is a
worked example, not a supported product.

## Requirements

- A Splunk instance with KV Store enabled — the usage and cost inputs keep
  their checkpoints in the KV Store collection `TA_anthropic_checkpoints`,
  **not** in `$SPLUNK_HOME/var/lib/splunk/modinputs/`.
- Splunk's modular-input runtime is CPython 3.9. Everything under
  `package/bin/` must stay 3.9-compatible.
- An Anthropic **Admin API key** (`sk-ant-admin01-…`), created at
  <https://platform.claude.com/settings/admin-keys>.

> An ordinary Console/workspace key (`sk-ant-api03-…`) authenticates
> `/v1/messages` and will fail here with
> `401 {"type":"authentication_error","message":"invalid x-api-key"}`. This
> add-on calls `/v1/organizations/*`, which is a different surface. The Admin
> API is also unavailable to individual (non-organization) accounts, and
> creating an Admin key requires the `admin` org role — `developer` is not
> enough. If the Admin keys page is empty or 404s, that is why.
>
> **You do not need a key to run this.** The offline mock below reproduces the
> full ingest path, and it is what the conference demo runs on.

## Build

`dist/*.tar.gz` is git-ignored, so a fresh clone has no `dist/` until you build
one. This repository shares a single `.venv` and a single `requirements-dev.txt`
at the root across both of its add-ons. All commands run from the repo root.

1. Create the venv and install the dev dependencies:

       python3 -m venv .venv
       .venv/bin/pip install -r requirements-dev.txt

   That provides `ucc-gen`. It does **not** provide `slim`, which the build
   also needs:

       .venv/bin/pip install splunk-packaging-toolkit

2. Build, validate and package:

       ./scripts/build.sh

   The script runs `ucc-gen build` → `slim validate` → `slim package` and
   writes `dist/TA_anthropic-0.1.0.tar.gz`. Override the version with
   `TA_VERSION=0.2.0 ./scripts/build.sh`.

Two caveats this script does **not** protect you from, which
`TA_openrouter/scripts/build.sh` does:

- **It does not pin the Python interpreter.** `ucc-gen build` vendors
  `package/lib/requirements.txt` wheels using whatever `python3` your shell
  resolves. Splunk's runtime is CPython 3.9; wheels built by a newer
  interpreter install cleanly and fail only when Splunk imports them. This
  add-on's `package/lib/requirements.txt` is deliberately minimal, which
  limits the blast radius, but the hazard is real.
- **`ucc-gen build` rewrites `globalConfig.json` in place** (reformatting, and
  adding schema-default `"required": true` entries). The change is cosmetically
  noisy but semantically inert. Never run `git checkout` on it after a build to
  "clean up" — that discards any real uncommitted edit along with the noise.

## Installation

1. Install the package via Splunk Web → **Apps → Manage Apps → Install app
   from file**, or extract it into `$SPLUNK_HOME/etc/apps/`. Use either
   `dist/TA_anthropic-0.1.0.tar.gz` (built above) or the committed
   `prebuilt/TA_anthropic-0.1.0.tar.gz`, which needs no build toolchain —
   note the macOS caveat in `prebuilt/README.md`.
2. Restart splunkd.
3. Splunk Web → **Anthropic Add-on for Splunk → Configuration → Account** →
   add an account: a name, the Admin API key, and the API base URL
   (`https://api.anthropic.com`, or the mock's address — see below).
4. **Inputs** → create the three inputs described next.

## Inputs

| Input | Helper | Settings | Notes |
| --- | --- | --- | --- |
| `anthropic_usage` | `anthropic_usage_helper` | account, interval, `backfill_days`, index | Hourly buckets (`1h`). `backfill_days` defaults to **7** — 168 hourly buckets, which is the API's per-page cap at `1h` granularity. |
| `anthropic_cost` | `anthropic_cost_helper` | account, interval, `backfill_days`, index | Daily buckets (`1d`). `backfill_days` defaults to **30**. |
| `anthropic_directory` | `anthropic_directory_helper` | account, interval, index | Snapshot of API keys and users. No checkpoint. |

**Set the index explicitly, and enable all three inputs.**

- The shipped searches and dashboard panels filter on `sourcetype=anthropic:*`
  with no `index=` clause, so they only see the role's default indexes. Data
  sent to an index outside those (`application`, say) leaves every panel empty
  with nothing visibly broken.
- With `anthropic_directory` disabled, the API-key baseline lookup is never
  populated, and the shadow-AI panel then flags **every** key instead of the
  unrecognized one — a confidently wrong result with no error anywhere.

**"No new events" is often correct.** The window computation only releases
completed buckets, so re-running the usage input inside the same clock hour is
expected to produce nothing. Diagnose before resetting a checkpoint.

## Sourcetypes

`anthropic:usage`, `anthropic:cost`, `anthropic:api_keys`, `anthropic:users` —
all JSON, all `KV_MODE = json` (search-time extraction).

The two directory sourcetypes (`anthropic:api_keys`, `anthropic:users`)
additionally carry `TIME_PREFIX`/`TIME_FORMAT`/`TZ = UTC`, pinned to
`snapshot_at` rather than `created_at`/`added_at` — the timestamp heuristic
would otherwise pick a months-old creation date and back-date the snapshot out
of the baseline window. Those settings are a **fallback** path only: the
modular input stamps each event's time explicitly, so they engage for a
forwarded copy, a replayed file, or a oneshot.

`TZ = UTC` is load-bearing there: `TIME_FORMAT = %Y-%m-%dT%H:%M:%SZ` consumes
the trailing `Z` as a literal character rather than as a UTC designator, so
without it the indexer would apply its own local offset.

## Money is in cents

`flatten_cost` divides `amount` by 100. This is correct and deliberate: the
Admin API reference states that cost amounts are in the lowest currency unit —
`"123.45"` in `"USD"` represents $1.23. The divisor has been reported as a 100×
bug before. Do not remove it.

## Dashboard and alerts

**AI Observability** (`package/default/data/ui/views/ai_observability.xml`) — token usage over
time by model, daily cost by workspace, cache hit ratio, top API keys by output
tokens, and unrecognized API keys.

Three saved searches:

| Saved search | Purpose |
| --- | --- |
| `Anthropic - Build API Key Baseline` | Every 30 min, rebuilds the `anthropic_api_key_baseline` lookup
(`package/lookups/anthropic_api_key_baseline.csv`) from the newest `anthropic:api_keys` snapshot, keyed on the key's `id`. Uses `override_if_empty=false`, because `outputlookup` *deletes* the lookup when a run returns no rows. |
| `Anthropic - Unrecognized API Key` | Every 5 min, fires on any `api_key_id` billing tokens that the baseline lookup does not know (shadow AI). Character-identical to the dashboard panel of the same name, and its `-7d` window must stay matched to it. |
| `Anthropic - Token Spike Anomaly` | Fires on an hour whose **org-wide** input tokens clear an absolute floor of 2,000,000 *and* exceed the mean of the 6 preceding hours by more than 2 population standard deviations (`streamstats window=6 current=f`, `stdevp`). Not per-key. |

`current=f` keeps the spiking hour out of its own baseline: with `current=t`
the z-score over n buckets is capped at (n-1)/√n, so a multi-sigma test over a
short window could never fire at all.

The spike alert's window is deliberately `-48h`, and the two alerts'
windows deliberately differ. Widening the spike window admits more mock
re-anchor generations into the `streamstats window=6` reference set, which
lifts the derived threshold above the spike and silently stops the alert
firing. `tests/test_alerts.py` parses the shipped search and re-derives that
arithmetic rather than trusting the prose.

Run the baseline search once **from inside this app** before relying on the
shadow-AI panel — `outputlookup` writes into whichever app context you run it
from.

## Running offline

`mockserver/` replays synthetic fixtures with the same response shapes as the
real Admin API, time-shifted so data lands in the recent past:

    .venv/bin/python -m uvicorn mockserver.app:app --port 8081

Then set the account's **API base URL** to `http://127.0.0.1:8081`. The mock
only checks that an API key header is present, so any non-empty value works.

**The mock freezes its time anchor at import.** Restarting it and immediately
re-running an input mints a second set of buckets at new timestamps that slip
past the checkpoint, double-ingesting the same data. If the mock restarts, wait
for the next natural interval. `demo/runbook.md` has the full explanation.

## Architecture

| Layer | Files | Imports Splunk? |
| --- | --- | --- |
| HTTP | `package/bin/anthropic_client.py` | no |
| Pure logic | `package/bin/anthropic_transform.py` | no |
| Modular inputs | `package/bin/anthropic_*_helper.py` | yes, behind `try/except ImportError` |
| Config / search | `package/default/*.conf`, `data/ui/views/*.xml`, `globalConfig.json` | n/a |

The client and transform have no Splunk imports and no clock the caller did not
pass in, and the helpers guard their Splunk imports — which is why the test
suite runs in seconds with no Splunk installed:

    .venv/bin/pytest -q tests

Note the two pagination schemes differ: the report endpoints use `has_more` →
`next_page`, while the directory endpoints use `has_more` → `last_id`.

## Known limitations

- v0.1.0. Single organization, single account per input.
- The live Admin API path is implemented and request-shape-correct but was
  never exercised against a real organization — the demo it was built for runs
  on the mock. See `docs/superpowers/plans/2026-07-30-anthropic-addon-demo.md`
  for that history.
- No CIM mapping.

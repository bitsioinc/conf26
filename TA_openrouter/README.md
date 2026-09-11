# OpenRouter Add-on for Splunk (TA_openrouter)

Ingests OpenRouter's management API (analytics and API-key inventory) and
reports on model-mix spend, provider routing, and API key governance.

## Requirements

- A Splunk instance with KV Store enabled (the analytics input's checkpoints
  live in a KV Store collection, `TA_openrouter_checkpoints`).
- Splunk's modular-input Python runtime must be CPython 3.9 — that is what
  `TA_openrouter/scripts/build.sh` vendors wheels for (see
  `docs/architecture.md`'s "Build and packaging" section). The add-on has not
  been validated against any other runtime.
- An OpenRouter **management API key**, created at
  <https://openrouter.ai/settings/management-keys>.

> Management keys carry the same `sk-or-v1-` prefix as ordinary inference
> keys. They are distinguishable only by the page that created them. If the
> add-on reports 401, verify the key came from the management-keys page, not
> the regular API-keys page.

## Build

The installable package is a build artifact, not something committed to the
repo: `dist/*.tar.gz` is git-ignored (`.gitignore:11`), so a fresh clone has
no `dist/` directory until you build one. This repo shares one `.venv` and
one `requirements-dev.txt` at the repo root across both of its add-ons —
there is no `TA_openrouter`-local venv or requirements file. All commands
below run from the repo root.

1. Create the venv and install the dev dependencies:

       python3 -m venv .venv
       .venv/bin/pip install -r requirements-dev.txt

   `requirements-dev.txt` installs `splunk-add-on-ucc-framework` (which
   provides the `ucc-gen` CLI) along with `pytest` and the mock server's
   dependencies. It does **not** install `slim` — `TA_openrouter/scripts/build.sh`
   also requires the `slim` CLI, from the separate `splunk-packaging-toolkit`
   package, and no `requirements*.txt` in this repo lists it:

       .venv/bin/pip install splunk-packaging-toolkit

   `scripts/build.sh` checks for both `ucc-gen` and `slim` on `.venv/bin`
   before doing anything else and exits with a clear error naming whichever
   is missing.

2. `scripts/build.sh` vendors `TA_openrouter/package/lib/requirements.txt`'s
   wheels using a **Python 3.9** interpreter specifically — Splunk's own
   modular-input runtime is CPython 3.9.25, and wheels built with any other
   `python3` (this repo's own dev `.venv` is 3.11) install silently but only
   fail once Splunk itself tries to import them. The script looks for
   `/opt/splunk/bin/python3.9` first, then a `python3.9` on `PATH`. On a
   machine with neither — no local Splunk install, most CI runners — set
   `TA_OPENROUTER_PYTHON39` to a Python 3.9 interpreter's path before
   building, or the script exits with an error rather than silently vendor
   the wrong wheels:

       export TA_OPENROUTER_PYTHON39=/path/to/python3.9   # only needed if step 3 errors without it

3. Run the build:

       TA_openrouter/scripts/build.sh

   This runs `ucc-gen build`, checks that the generated modular inputs
   actually reference their helper modules (not a stub — see
   `docs/architecture.md`), runs `slim validate`, and packages the result
   into `dist/TA_openrouter-<version>.tar.gz` — `0.1.0` by default,
   overridable via `TA_VERSION=x.y.z`. See `docs/architecture.md`'s "Build
   and packaging" section for what each step guards against.

## Installation

1. Install the package built above, `dist/TA_openrouter-0.1.0.tar.gz`. To skip
   the build entirely, use the committed `prebuilt/TA_openrouter-0.1.0.tar.gz`
   instead — see `prebuilt/README.md`, which documents the macOS-built native
   dependencies and when you should build on your own platform rather than use
   it.
2. Restart Splunk.
3. **Configuration → Account** — add an account with your management key.
   Leave the base URL at `https://openrouter.ai/api/v1` (point it at a local
   mock server instead only for offline testing).
4. **Inputs** — create one Analytics input and one API Keys input. Both
   default to a 3600-second interval, matching the hourly analytics
   granularity the add-on requests.
5. Run **OpenRouter - Build API Key Baseline** once from inside this app
   (not from Search & Reporting — see `docs/operations.md`), then confirm
   `| inputlookup openrouter_key_baseline` returns your sanctioned keys.
   Until this runs, the Unrecognised Key alert and the Key Governance
   dashboard's "unrecognised keys" panel will flag **every** key as
   unrecognised, because the lookup ships with headers only.

## Inputs

| Input | Endpoint | Sourcetypes |
| --- | --- | --- |
| Analytics | `POST /analytics/query` (issued twice per run) | `openrouter:analytics`, `openrouter:providers` |
| API Keys | `GET /keys` | `openrouter:keys` |

The Analytics input issues two queries per run because OpenRouter caps
`dimensions` at two per request, and three dimensions are needed across the
dashboards (`api_key_id`, `model`, `provider`): one query grouped by
`[api_key_id, model]` (sourcetype `openrouter:analytics`), one grouped by
`[model, provider]` (sourcetype `openrouter:providers`). `model` is the axis
that appears in both — it is what lets a reader correlate the Model Mix and
Provider Routing dashboards by eye (the same model name shows up in both).
No shipped panel or saved search actually joins the two sourcetypes
together: every panel in `openrouter_provider_routing.xml` queries only
`openrouter:providers`, and every panel in `openrouter_model_mix.xml`
queries only `openrouter:analytics`. Each of the two queries holds its
**own** KV Store checkpoint, so a problem with one (a transient API error, a
truncated response) never stalls the other.

The API Keys input takes no `backfill_days` field: it is a snapshot, not a
windowed query. Every run re-emits the full current roster stamped with a
`snapshot_at` time; there is no checkpoint, and downstream searches dedup on
`snapshot_at` (typically via `stats latest(...) by hash`, as the Key
Governance dashboard does).

`backfill_days` on the Analytics input only matters on an input's first run
(before it has a checkpoint) and is clamped to 30 days regardless of what is
configured — see "Retention" in `docs/operations.md`.

## A fact that shapes almost everything downstream

OpenRouter's analytics endpoints identify an API key by its **name**
(`api_key_id`, e.g. `"Alpaca"`), never by the roster's `hash`. This was
verified against a live account (see `docs/schema-verification.md`) — no
analytics row anywhere carries a `hash` value in `api_key_id`. Every search
and lookup shipped in this add-on (the baseline builder, both alerts, the Key
Governance dashboard) joins on `name`, not `hash`, because of this.

**Consequence:** renaming a key in OpenRouter changes the value analytics
traffic will report for it, and that new name has no entry in
`openrouter_key_baseline.csv` until the baseline builder is re-run. Until
then, a renamed (but perfectly legitimate) key looks exactly like
unsanctioned shadow usage. Re-run **OpenRouter - Build API Key Baseline**
after renaming or adding a key.

## Dashboards

- **OpenRouter - Model Mix & Spend** — spend and token volume by model,
  top models by spend, model mix per API key
- **OpenRouter - Provider Routing** — spend share by upstream provider, and
  cost-per-request comparisons when the same model is served by more than
  one provider
- **OpenRouter - Key Governance** — unrecognised keys (active but absent
  from the baseline), the key roster, and spend by key over time

## Alerts

- **OpenRouter - Unrecognised API Key Active** — a key producing traffic in
  `openrouter:analytics` whose `api_key_id` has no match in
  `openrouter_key_baseline`. Runs hourly, severity 4, suppressed 6h per
  result — per `api_key_id`, specifically (`alert.suppress.fields =
  api_key_id` with `alert.digest_mode = 0`), so one noisy key firing does not
  suppress a second, unrelated key's genuine hit for the next 6 hours.
- **OpenRouter - Hourly Spend Spike by Key** — hourly spend for a key more
  than 3 standard deviations above its own six-hour trailing mean
  (`streamstats window=6 current=f`). The sigma is pinned by
  `tests/test_openrouter_alerts.py` against the committed fixtures — at
  sigma=3 it isolates exactly one synthetic key's runaway final bucket; at
  sigma=0.5 it also flags a second. Runs hourly, severity 3, suppressed 2h
  per result — per `api_key_id`, same as above. The search itself only
  considers buckets from roughly the last 2 hours as triggerable (older
  buckets still feed the trailing baseline but cannot fire), so a single
  spike produces one Triggered Alert per key rather than re-firing on every
  hourly run for the 48 hours it used to stay inside the search window.
- **OpenRouter - Build API Key Baseline** — not an alert (no trigger
  condition), but a saved search that must be run manually. See
  `docs/operations.md`.

## More

- `docs/architecture.md` — code layout, the checkpoint/truncation
  contract, and build-time pitfalls that no automated gate catches.
- `docs/operations.md` — populating the baseline, reading truncation
  warnings, retention limits, and other day-2 operator notes.
- `docs/schema-verification.md` — exactly which API response fields were
  observed live versus assumed from the OpenAPI spec, and why the fixtures
  contain real account data.

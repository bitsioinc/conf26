# Anthropic Add-on for Splunk (DEV1194 Demo) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the "Anthropic Add-on for Splunk" (UCC-framework TA ingesting the Anthropic Admin API usage/cost endpoints), its fallback assets (FastAPI mock server + fixtures, a local Splunk install, dashboard, alert), and the demo runbook for .conf2026 session DEV1194.

**Architecture:** A UCC-generated TA (`TA_anthropic`) whose modular inputs call a pure-Python Admin API client and transform layer (unit-testable without Splunk). The TA's API base URL is configurable, so the same TA runs against `api.anthropic.com` or a local FastAPI mock replaying recorded fixtures. Packaging goes through `ucc-gen build` then SLIM (`slim validate` + `slim package`). The built package is installed into a **local Splunk 10.2.1 at `/opt/splunk`** (see the deviation note below — the plan originally called for Docker).

**Tech Stack:** Python 3.12 for the dev toolchain (must be ≥3.9 and <3.14 for SLIM), `splunk-add-on-ucc-framework` (ucc-gen), vendored `splunk_packaging_toolkit-1.2.8` (SLIM), FastAPI + uvicorn (mock), pytest, requests, solnlib/splunklib (inside TA), and a local **Splunk Enterprise 10.2.1** install at `/opt/splunk`.

> **Runtime Python is 3.9, not 3.12.** Splunk 10.2.1's app runtime on this box is CPython **3.9.25**. Everything under `TA_anthropic/package/bin/` must stay 3.9-compatible — no f-strings in the shipped modules, `.format()` only. The dev venv (3.12) only runs ucc-gen/SLIM/pytest.

## Global Constraints

- Python interpreter: ≥3.9 and <3.14 (SLIM 1.2.8 `Requires-Python`). Verify before creating the venv.
- SLIM is installed from the vendored directory: `pip install ./splunk_packaging_toolkit-1.2.8` (do not fetch from network).
- Add-on identity: name `TA_anthropic`, display name `Anthropic Add-on for Splunk`, version `0.1.0`, restRoot `TA_anthropic`.
- Sourcetypes (exact strings): `anthropic:usage`, `anthropic:cost`, `anthropic:api_keys`, `anthropic:users`.
- Admin API auth headers on every request: `x-api-key: <admin key>` and `anthropic-version: 2023-06-01`.
- Usage report: `GET /v1/organizations/usage_report/messages`, `bucket_width=1h`, backfill 7 days (the API caps 1h responses at 168 buckets/page), `group_by[]=model&group_by[]=workspace_id&group_by[]=api_key_id&group_by[]=service_tier`.
- Cost report: `GET /v1/organizations/cost_report`, `bucket_width=1d` (the only width it supports), backfill 30 days, `group_by[]=workspace_id&group_by[]=description`.
- Both report endpoints paginate via `has_more` + `next_page` (echo as `page` param). Org directory endpoints (`/v1/organizations/api_keys`, `/v1/organizations/users`) paginate via `has_more` + `last_id` (echo as `after_id`).
- Cost `amount` is a decimal **string in cents** (`"123.45"` = $1.23). Events must carry a computed `amount_usd` float.
- Mock server listens on `127.0.0.1:8081`; the local Splunk reaches it at `http://127.0.0.1:8081` (same host — no `host.docker.internal` indirection).
- Local Splunk 10.2.1 at `/opt/splunk`: web `:8000`, management `:8089`. Admin credentials are held by whoever owns the box and are **not** in the repo; the build/verify path is designed so no authenticated REST call is required.
- The rogue API key ID used for the alert demo is exactly `apikey_rogue_demo` — present in usage fixtures, absent from `api_keys.json`.
- Never commit real Admin API keys, recorded un-sanitized fixtures, or `splunk/.env`.

## Deviation from spec (approved rationale)

The spec said "~30 days backfill". The usage API caps `1h` granularity at 168 buckets per response page, so the usage input backfills **7 days hourly** and the cost input backfills **30 days daily**. The dashboard is still instantly rich; the spec's intent (no waiting for data) is preserved.

**Splunk runtime: local install, not Docker.** This plan was written against a Docker `splunk/splunk` container (Task 10). The demo box already had **Splunk Enterprise 10.2.1 installed at `/opt/splunk`**, so the container was dropped: the TA is extracted straight into `/opt/splunk/etc/apps/` and the mock is reached at `127.0.0.1:8081` instead of `host.docker.internal:8081`. This removes a whole failure surface from the stage (no daemon, no image pull, no port mapping, no 2–4 minute first boot) and is why `splunk/docker-compose.yml` was never created — `splunk/` holds only `.env.example`. Task 10 below has been rewritten to match.

## Post-build corrections

The add-on built clean (41 unit tests, `slim validate` 0 errors, AppInspect precert 0 errors / 0 failures) and was then put through three adversarial reviews. Each finding was checked against the Splunk 10.2.1 spec files on this box and the published Anthropic API reference. Six were confirmed and fixed; two were investigated and **rejected**. The tasks above have been edited in place to match the corrected build — this section records *why*, so nobody "re-fixes" a rejected finding.

### Fixed (6)

| # | Severity | Finding | Fix |
|---|---|---|---|
| 1 | Critical | Runbook pre-stage enabled only **2 of 3** inputs ("usage + cost"). `anthropic_directory` is the sole source of `anthropic:api_keys`, the sole input to the baseline saved search. Disabled ⇒ empty baseline ⇒ the shadow-AI reveal flags **every** key instead of the one planted rogue one — wrong answer, silently, on the centrepiece of the talk. | Pre-stage enables all three inputs, then builds the baseline by hand and asserts 2 lookup rows / 1 unrecognized row before leaving the room. |
| 2 | High | Runbook told the presenter to clear checkpoints under `/opt/splunk/var/lib/splunk/modinputs/`. The add-on checkpoints in the **KV store** (solnlib `KVStoreCheckpointer`, collection `TA_anthropic_checkpoints`), so that deletion is a no-op — stage time burned for nothing. | Replaced with `\| rest …/storage/collections/data/TA_anthropic_checkpoints` to inspect and a DELETE to the same endpoint to clear (needs admin creds, now a pre-stage item). Also documents that a same-clock-hour no-op is *expected*, not a fault. |
| 3 | High | `savedsearches.conf` carried `alert_type` / `alert_comparator` / `alert_threshold`. These are **REST argument names**, not conf settings: `grep -c` against `/opt/splunk/etc/system/README/savedsearches.conf.spec` returns **0**, and `slim validate` rejects them. | Removed. Kept `counttype` / `relation` / `quantity`, which *are* in the spec (lines 284 / 290 / 294). |
| 4 | High | Baseline builder used a bare `outputlookup`. Per `searchbnf.conf`, the default `override_if_empty=true` "deletes the lookup file if it exists" when the search returns no rows — so one quiet directory input wipes the baseline and every key then reads as unrecognized. | `\| outputlookup override_if_empty=false anthropic_api_key_baseline.csv`, and the dispatch window widened `-24h` → `-7d`. |
| 5 | Medium | `anthropic_api_key_baseline.csv` shipped header-only, so a fresh install flags every key until the `*/30` cron has fired *after* the directory input has run — precisely the window the demo occupies. | Pre-populated with the two known keys (`apikey_demo_ci`, `apikey_demo_claudecode`); the scheduled search keeps it current. |
| 6 | High | The directory helper wrote events with no explicit `time=`. Splunk's timestamp heuristics then pulled `created_at` / `added_at` out of the JSON body, back-dating snapshots by **months** — far outside the baseline search's lookback window, which therefore found nothing. | One `snapshot_dt` per run feeds both the `snapshot_at` body field and an explicit `time=`, truncated to whole seconds so the two agree exactly. |

**Also landed, from a parallel workstream** (recorded here so the plan does not drift from the shipped file again): `[Anthropic - Token Spike Anomaly]` was rewritten. The original `streamstats window=24 avg/stdev` with the default `current=t` includes the spiking hour in its own baseline, which caps the z-score of *n* buckets at `(n-1)/√n` — so a `3*sd` test can never fire over a short window. The shipped search uses `current=f`, `stdevp`, a 6-hour window, a `2*sd` threshold, and an absolute 2,000,000-token floor. The config block in Task 8 above reflects the shipped version.

### Investigated and rejected (2) — do not "fix" these

**A. "Cost `amount` is dollars, not cents — remove the `/100`." REJECTED.**
The reviewer reasoned from our own synthetic fixture values rather than from the contract. The Anthropic API reference for `GET /v1/organizations/cost_report` states verbatim: *"amount: string — Cost amount in lowest currency units (e.g. cents) as a decimal string. For example, `"123.45"` in `"USD"` represents $1.23."* `anthropic_transform.flatten_cost` **must** keep `round(float(amount) / 100.0, 6)`. Changing it would introduce a 100× cost error.

**B. "`counttype = number of events` counts raw events, so the alerts always fire — switch to `alert_condition`." REJECTED.**
Splunk's own shipped alerts use exactly this idiom on transforming searches. `/opt/splunk/etc/apps/splunk_monitoring_console/default/savedsearches.conf`, stanza `[DMC Alert - Total License Usage Near Daily Quota]` — a `| rest … | eval | mvexpand | join | stats | eval | where` search, i.e. fully transforming — carries `counttype = number of events` (line 4), `quantity = 0` (line 15), `relation = greater than` (line 16). `counttype` counts *result rows* for a transforming search, which is exactly the semantics we want. Keep `counttype` / `relation` / `quantity` as they are, and do **not** reintroduce the finding-3 keys while doing so.

## File Structure

```
conf26/
├── TA_anthropic/                    # ucc-gen init output (addon source)
│   ├── globalConfig.json            # UCC config: account (api_key encrypted, base_url), 3 inputs
│   └── package/
│       ├── app.manifest
│       ├── bin/
│       │   ├── anthropic_client.py        # pure REST client (no Splunk imports) — unit tested
│       │   ├── anthropic_transform.py     # window/flatten/checkpoint logic (pure) — unit tested
│       │   ├── anthropic_usage.py         # generated input wiring (ucc-gen)
│       │   └── *_helper.py                # stream_events implementations (Splunk-coupled)
│       ├── default/
│       │   ├── props.conf                 # sourcetype definitions
│       │   ├── transforms.conf            # baseline lookup definition
│       │   ├── savedsearches.conf         # baseline builder + alerts
│       │   └── data/ui/views/ai_observability.xml  # payoff dashboard
│       ├── lookups/
│       │   └── anthropic_api_key_baseline.csv  # PRE-POPULATED with the 2 known keys
│       └── lib/requirements.txt           # solnlib etc. (bundled by ucc-gen build)
├── mockserver/
│   ├── app.py                       # FastAPI mock of the Admin API (timestamp-shifting replay)
│   └── fixtures/
│       ├── usage_report_page1.json  # has_more=true → page2
│       ├── usage_report_page2.json
│       ├── cost_report.json
│       ├── api_keys.json            # does NOT contain apikey_rogue_demo
│       └── users.json
├── tests/
│   ├── conftest.py                  # sys.path shim to import TA_anthropic/package/bin modules
│   ├── test_fixtures.py
│   ├── test_mockserver.py
│   ├── test_client.py
│   └── test_transform.py
├── splunk/
│   └── .env.example                 # SPLUNK_PASSWORD template (local install; no compose file)
├── scripts/
│   ├── build.sh                     # ucc-gen build → slim validate → slim package
│   ├── record_fixtures.py           # capture + sanitize real Admin API responses
│   └── preflight.sh                 # day-of checklist automation
├── demo/
│   ├── demo_prompt.md               # the exact prompt fed to Claude Code on stage
│   └── runbook.md                   # run of show + failure decision tree
└── requirements-dev.txt
```

Responsibilities: `anthropic_client.py` knows HTTP and pagination only. `anthropic_transform.py` knows time windows and event shaping only. The `*_helper.py` files are the only Splunk-coupled code and stay thin. The mock serves fixtures byte-shape-identical to the real API so the TA cannot tell the difference.

---

### Task 1: Dev environment and repo scaffolding

**Files:**
- Create: `requirements-dev.txt`
- Modify: `.gitignore`
- Create: `splunk/.env.example`

**Interfaces:**
- Produces: a `.venv` with `ucc-gen`, `slim`, `pytest`, `fastapi` on PATH; later tasks assume `source .venv/bin/activate`.

- [ ] **Step 1: Verify Python version is <3.14**

Run: `python3 --version`
Expected: `Python 3.9.x`–`3.13.x`. If 3.14+, locate an older interpreter (`ls /usr/local/bin/python3.1[0-3]* /opt/homebrew/bin/python3.1[0-3]*`) and use it for the venv; stop and report if none exists.

- [ ] **Step 2: Write requirements-dev.txt**

```text
splunk-add-on-ucc-framework
fastapi
uvicorn
httpx
pytest
requests
```

- [ ] **Step 3: Create venv and install (SLIM from the vendored dir)**

Run:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pip install ./splunk_packaging_toolkit-1.2.8
```

- [ ] **Step 4: Verify the toolchain**

Run: `slim --version && ucc-gen --version && pytest --version`
Expected: three version strings, no errors. (`slim --version` should print `1.2.8`.)

- [ ] **Step 5: Extend .gitignore and add env template**

Append to `.gitignore`:

```text
.venv/
__pycache__/
*.pyc
output/
*.tar.gz
*.spl
splunk/.env
splunk_packaging_toolkit-1.2.8/
mockserver/fixtures/raw_*
```

Create `splunk/.env.example`:

```text
SPLUNK_PASSWORD=ChangeMe-Demo1!
```

(The vendored toolkit stays on disk but untracked — the repo README will point at dev.splunk.com / `pip install splunk-packaging-toolkit` for reproducers.)

- [ ] **Step 6: Commit**

```bash
git add requirements-dev.txt .gitignore splunk/.env.example
git commit -m "chore: dev environment scaffolding (ucc-gen + vendored SLIM)"
```

---

### Task 2: Shared Admin API fixtures

**Files:**
- Create: `mockserver/fixtures/usage_report_page1.json`
- Create: `mockserver/fixtures/usage_report_page2.json`
- Create: `mockserver/fixtures/cost_report.json`
- Create: `mockserver/fixtures/api_keys.json`
- Create: `mockserver/fixtures/users.json`
- Test: `tests/test_fixtures.py`

**Interfaces:**
- Produces: fixture JSON files whose shapes match the real Admin API exactly (field names below are verbatim from the API reference). Consumed by the mock server (Task 3) and as test data for client/transform tests (Tasks 4–5).

- [ ] **Step 1: Write the failing shape test**

`tests/test_fixtures.py`:

```python
import json
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "mockserver" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_usage_pages_paginate():
    p1 = load("usage_report_page1.json")
    p2 = load("usage_report_page2.json")
    assert p1["has_more"] is True and p1["next_page"] == "page_demo_2"
    assert p2["has_more"] is False and p2["next_page"] is None
    for page in (p1, p2):
        for bucket in page["data"]:
            assert set(bucket) == {"starting_at", "ending_at", "results"}
            for r in bucket["results"]:
                assert {"uncached_input_tokens", "cache_read_input_tokens",
                        "cache_creation", "output_tokens", "model",
                        "workspace_id", "api_key_id", "service_tier"} <= set(r)


def test_rogue_key_in_usage_but_not_directory():
    p2 = load("usage_report_page2.json")
    usage_keys = {r["api_key_id"] for b in p2["data"] for r in b["results"]}
    directory_keys = {k["id"] for k in load("api_keys.json")["data"]}
    assert "apikey_rogue_demo" in usage_keys
    assert "apikey_rogue_demo" not in directory_keys


def test_cost_amounts_are_decimal_strings():
    cost = load("cost_report.json")
    for b in cost["data"]:
        for r in b["results"]:
            assert isinstance(r["amount"], str) and float(r["amount"]) > 0
            assert r["currency"] == "USD"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fixtures.py -v`
Expected: FAIL (FileNotFoundError — fixtures don't exist).

- [ ] **Step 3: Write the fixtures**

`mockserver/fixtures/usage_report_page1.json` — 2 hourly buckets, grouped results (synthetic IDs, no real data):

```json
{
  "data": [
    {
      "starting_at": "2026-07-28T14:00:00Z",
      "ending_at": "2026-07-28T15:00:00Z",
      "results": [
        {
          "account_id": null,
          "api_key_id": "apikey_demo_ci",
          "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 41200},
          "cache_read_input_tokens": 611000,
          "context_window": null,
          "inference_geo": null,
          "model": "claude-opus-5",
          "output_tokens": 92400,
          "server_tool_use": {"web_search_requests": 3},
          "service_account_id": null,
          "service_tier": "standard",
          "uncached_input_tokens": 84100,
          "workspace_id": "wrkspc_demo_platform"
        },
        {
          "account_id": null,
          "api_key_id": "apikey_demo_claudecode",
          "cache_creation": {"ephemeral_1h_input_tokens": 120000, "ephemeral_5m_input_tokens": 0},
          "cache_read_input_tokens": 2140000,
          "context_window": null,
          "inference_geo": null,
          "model": "claude-fable-5",
          "output_tokens": 188000,
          "server_tool_use": {"web_search_requests": 0},
          "service_account_id": null,
          "service_tier": "standard",
          "uncached_input_tokens": 96000,
          "workspace_id": "wrkspc_demo_eng"
        }
      ]
    },
    {
      "starting_at": "2026-07-28T15:00:00Z",
      "ending_at": "2026-07-28T16:00:00Z",
      "results": [
        {
          "account_id": null,
          "api_key_id": "apikey_demo_ci",
          "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 20000},
          "cache_read_input_tokens": 300500,
          "context_window": null,
          "inference_geo": null,
          "model": "claude-haiku-4-5",
          "output_tokens": 41000,
          "server_tool_use": {"web_search_requests": 0},
          "service_account_id": null,
          "service_tier": "batch",
          "uncached_input_tokens": 512000,
          "workspace_id": "wrkspc_demo_platform"
        }
      ]
    }
  ],
  "has_more": true,
  "next_page": "page_demo_2"
}
```

`mockserver/fixtures/usage_report_page2.json` — final page; contains the rogue key spike:

```json
{
  "data": [
    {
      "starting_at": "2026-07-28T16:00:00Z",
      "ending_at": "2026-07-28T17:00:00Z",
      "results": [
        {
          "account_id": null,
          "api_key_id": "apikey_demo_claudecode",
          "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 15500},
          "cache_read_input_tokens": 995000,
          "context_window": null,
          "inference_geo": null,
          "model": "claude-fable-5",
          "output_tokens": 120500,
          "server_tool_use": {"web_search_requests": 1},
          "service_account_id": null,
          "service_tier": "standard",
          "uncached_input_tokens": 66300,
          "workspace_id": "wrkspc_demo_eng"
        },
        {
          "account_id": null,
          "api_key_id": "apikey_rogue_demo",
          "cache_creation": {"ephemeral_1h_input_tokens": 0, "ephemeral_5m_input_tokens": 0},
          "cache_read_input_tokens": 0,
          "context_window": null,
          "inference_geo": null,
          "model": "claude-opus-5",
          "output_tokens": 2400000,
          "server_tool_use": {"web_search_requests": 0},
          "service_account_id": null,
          "service_tier": "standard",
          "uncached_input_tokens": 3900000,
          "workspace_id": null
        }
      ]
    }
  ],
  "has_more": false,
  "next_page": null
}
```

`mockserver/fixtures/cost_report.json` — 2 daily buckets grouped by workspace + description:

```json
{
  "data": [
    {
      "starting_at": "2026-07-27T00:00:00Z",
      "ending_at": "2026-07-28T00:00:00Z",
      "results": [
        {
          "amount": "1834.20",
          "context_window": "0-200k",
          "cost_type": "tokens",
          "currency": "USD",
          "description": "Claude Fable 5 Usage - Output Tokens",
          "inference_geo": null,
          "model": "claude-fable-5",
          "service_tier": "standard",
          "token_type": "output_tokens",
          "workspace_id": "wrkspc_demo_eng"
        },
        {
          "amount": "411.90",
          "context_window": "0-200k",
          "cost_type": "tokens",
          "currency": "USD",
          "description": "Claude Opus 5 Usage - Input Tokens",
          "inference_geo": null,
          "model": "claude-opus-5",
          "service_tier": "standard",
          "token_type": "uncached_input_tokens",
          "workspace_id": "wrkspc_demo_platform"
        }
      ]
    },
    {
      "starting_at": "2026-07-28T00:00:00Z",
      "ending_at": "2026-07-29T00:00:00Z",
      "results": [
        {
          "amount": "2210.75",
          "context_window": "0-200k",
          "cost_type": "tokens",
          "currency": "USD",
          "description": "Claude Fable 5 Usage - Output Tokens",
          "inference_geo": null,
          "model": "claude-fable-5",
          "service_tier": "standard",
          "token_type": "output_tokens",
          "workspace_id": "wrkspc_demo_eng"
        }
      ]
    }
  ],
  "has_more": false,
  "next_page": null
}
```

`mockserver/fixtures/api_keys.json` (note: no `apikey_rogue_demo`):

```json
{
  "data": [
    {"id": "apikey_demo_ci", "type": "api_key", "name": "ci-pipeline",
     "workspace_id": "wrkspc_demo_platform", "created_at": "2026-01-15T10:00:00Z",
     "partial_key_hint": "sk-ant-api03-R2D...igAA", "status": "active"},
    {"id": "apikey_demo_claudecode", "type": "api_key", "name": "claude-code-team",
     "workspace_id": "wrkspc_demo_eng", "created_at": "2026-03-02T09:30:00Z",
     "partial_key_hint": "sk-ant-api03-C3P...o0AA", "status": "active"}
  ],
  "has_more": false,
  "first_id": "apikey_demo_ci",
  "last_id": "apikey_demo_claudecode"
}
```

`mockserver/fixtures/users.json`:

```json
{
  "data": [
    {"id": "user_demo_admin", "type": "user", "email": "admin@example.com",
     "name": "Demo Admin", "role": "admin", "added_at": "2026-01-10T08:00:00Z"},
    {"id": "user_demo_dev", "type": "user", "email": "dev@example.com",
     "name": "Demo Developer", "role": "developer", "added_at": "2026-02-20T12:00:00Z"}
  ],
  "has_more": false,
  "first_id": "user_demo_admin",
  "last_id": "user_demo_dev"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fixtures.py -v`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add mockserver/fixtures tests/test_fixtures.py
git commit -m "feat: Admin API fixtures matching real response schemas"
```

---

### Task 3: Mock Admin API server

**Files:**
- Create: `mockserver/app.py`
- Test: `tests/test_mockserver.py`

**Interfaces:**
- Consumes: fixture files from Task 2.
- Produces: HTTP endpoints `GET /v1/organizations/usage_report/messages` (honors `page=page_demo_2`), `GET /v1/organizations/cost_report`, `GET /v1/organizations/api_keys`, `GET /v1/organizations/users`. All require a non-empty `x-api-key` header (401 otherwise). All report timestamps are shifted so the newest usage bucket ends at the top of the current hour ("live" data on stage). Run with `uvicorn mockserver.app:app --port 8081`.

- [ ] **Step 1: Write the failing tests**

`tests/test_mockserver.py`:

```python
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from mockserver.app import app

client = TestClient(app)
AUTH = {"x-api-key": "sk-ant-admin01-fake", "anthropic-version": "2023-06-01"}


def test_rejects_missing_api_key():
    assert client.get("/v1/organizations/usage_report/messages").status_code == 401


def test_usage_pagination_chain():
    p1 = client.get("/v1/organizations/usage_report/messages", headers=AUTH).json()
    assert p1["has_more"] is True
    p2 = client.get("/v1/organizations/usage_report/messages",
                    params={"page": p1["next_page"]}, headers=AUTH).json()
    assert p2["has_more"] is False


def test_timestamps_are_shifted_to_now():
    p1 = client.get("/v1/organizations/usage_report/messages", headers=AUTH).json()
    p2 = client.get("/v1/organizations/usage_report/messages",
                    params={"page": "page_demo_2"}, headers=AUTH).json()
    newest = max(b["ending_at"] for b in p1["data"] + p2["data"])
    newest_dt = datetime.fromisoformat(newest.replace("Z", "+00:00"))
    top_of_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    assert abs(newest_dt - top_of_hour) <= timedelta(hours=1)


def test_directory_endpoints():
    keys = client.get("/v1/organizations/api_keys", headers=AUTH).json()
    users = client.get("/v1/organizations/users", headers=AUTH).json()
    assert {k["id"] for k in keys["data"]} == {"apikey_demo_ci", "apikey_demo_claudecode"}
    assert len(users["data"]) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_mockserver.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mockserver.app'`. Add an empty `mockserver/__init__.py` if needed for imports.

- [ ] **Step 3: Implement the mock server**

`mockserver/app.py`:

```python
"""Local replay of the Anthropic Admin API for offline demos.

Serves recorded fixtures with all report timestamps shifted so the newest
usage bucket ends at the top of the current hour — the TA sees "live" data.
"""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query

FIXTURES = Path(__file__).parent / "fixtures"
app = FastAPI(title="Anthropic Admin API (mock)")


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _newest_usage_ending() -> datetime:
    pages = [_load("usage_report_page1.json"), _load("usage_report_page2.json")]
    return max(_parse(b["ending_at"]) for p in pages for b in p["data"])


def _shift(report: dict) -> dict:
    """Shift every bucket timestamp by (top of current hour - newest usage bucket)."""
    top_of_hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    delta = top_of_hour - _newest_usage_ending()
    shifted = copy.deepcopy(report)
    for bucket in shifted["data"]:
        bucket["starting_at"] = _fmt(_parse(bucket["starting_at"]) + delta)
        bucket["ending_at"] = _fmt(_parse(bucket["ending_at"]) + delta)
    return shifted


def _require_key(x_api_key) -> None:
    if not x_api_key:
        raise HTTPException(status_code=401, detail={"type": "authentication_error"})


@app.get("/v1/organizations/usage_report/messages")
def usage_report(page: str = Query(default=None),
                 x_api_key: str = Header(default=None)):
    _require_key(x_api_key)
    name = "usage_report_page2.json" if page == "page_demo_2" else "usage_report_page1.json"
    return _shift(_load(name))


@app.get("/v1/organizations/cost_report")
def cost_report(x_api_key: str = Header(default=None)):
    _require_key(x_api_key)
    return _shift(_load("cost_report.json"))


@app.get("/v1/organizations/api_keys")
def api_keys(x_api_key: str = Header(default=None)):
    _require_key(x_api_key)
    return _load("api_keys.json")


@app.get("/v1/organizations/users")
def users(x_api_key: str = Header(default=None)):
    _require_key(x_api_key)
    return _load("users.json")
```

Also create empty `mockserver/__init__.py` and `tests/__init__.py` if pytest import fails without them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_mockserver.py -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add mockserver tests/test_mockserver.py
git commit -m "feat: FastAPI mock Admin API with timestamp-shifting replay"
```

---

### Task 4: Admin API client (pure Python)

**Files:**
- Create: `TA_anthropic/package/bin/anthropic_client.py`
- Test: `tests/test_client.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: nothing Splunk-side; `requests` only.
- Produces (used by Tasks 5 & 7):
  - `AnthropicAdminClient(api_key: str, base_url: str = "https://api.anthropic.com", session=None, timeout: int = 30)`
  - `.iter_usage_report(starting_at: str, ending_at: str, bucket_width: str = "1h", group_by: list | None = None)` — yields full page dicts, following `next_page`.
  - `.iter_cost_report(starting_at: str, ending_at: str, group_by: list | None = None)`
  - `.list_api_keys() -> list[dict]` / `.list_users() -> list[dict]` — flattened `data` items across `after_id` pages.
  - Raises `AnthropicAPIError(status_code, message)` on non-retryable failures; retries once on 429/5xx.

> Note: the UCC scaffold (`TA_anthropic/`) is created in Task 6. Until then, create the `TA_anthropic/package/bin/` directory path directly — `ucc-gen init` in Task 6 is run in a temp dir and merged so these files survive.

- [ ] **Step 1: Write conftest so tests can import bin/ modules**

`tests/conftest.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))                          # mockserver pkg
sys.path.insert(0, str(Path(__file__).parent.parent / "TA_anthropic" / "package" / "bin"))
```

- [ ] **Step 2: Write the failing tests**

`tests/test_client.py` — uses a stub session, no network:

```python
import pytest
from anthropic_client import AnthropicAdminClient, AnthropicAPIError


class StubResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


class StubSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        return self.responses.pop(0)


PAGE1 = {"data": [{"starting_at": "2026-07-28T14:00:00Z", "ending_at": "2026-07-28T15:00:00Z",
                   "results": []}], "has_more": True, "next_page": "page_demo_2"}
PAGE2 = {"data": [], "has_more": False, "next_page": None}


def make(responses):
    session = StubSession(responses)
    return AnthropicAdminClient("sk-ant-admin01-x", base_url="http://mock", session=session), session


def test_sends_auth_headers_and_params():
    client, session = make([StubResponse(200, PAGE2)])
    list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z",
                                  group_by=["model", "workspace_id"]))
    call = session.calls[0]
    assert call["headers"]["x-api-key"] == "sk-ant-admin01-x"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert call["params"]["group_by[]"] == ["model", "workspace_id"]
    assert call["url"] == "http://mock/v1/organizations/usage_report/messages"


def test_follows_next_page():
    client, session = make([StubResponse(200, PAGE1), StubResponse(200, PAGE2)])
    pages = list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z"))
    assert len(pages) == 2
    assert session.calls[1]["params"]["page"] == "page_demo_2"


def test_retries_once_on_429_then_succeeds():
    client, _ = make([StubResponse(429, {}), StubResponse(200, PAGE2)])
    assert len(list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z"))) == 1


def test_raises_after_second_failure():
    client, _ = make([StubResponse(500, {}), StubResponse(500, {})])
    with pytest.raises(AnthropicAPIError):
        list(client.iter_usage_report("2026-07-28T00:00:00Z", "2026-07-28T17:00:00Z"))


def test_list_api_keys_flattens_after_id_pages():
    keys_p1 = {"data": [{"id": "apikey_a"}], "has_more": True, "first_id": "apikey_a", "last_id": "apikey_a"}
    keys_p2 = {"data": [{"id": "apikey_b"}], "has_more": False, "first_id": "apikey_b", "last_id": "apikey_b"}
    client, session = make([StubResponse(200, keys_p1), StubResponse(200, keys_p2)])
    keys = client.list_api_keys()
    assert [k["id"] for k in keys] == ["apikey_a", "apikey_b"]
    assert session.calls[1]["params"]["after_id"] == "apikey_a"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'anthropic_client'`.

- [ ] **Step 4: Implement the client**

`TA_anthropic/package/bin/anthropic_client.py`:

```python
"""Pure REST client for the Anthropic Admin API. No Splunk imports."""
import time


class AnthropicAPIError(Exception):
    def __init__(self, status_code, message):
        self.status_code = status_code
        super().__init__("Admin API error {}: {}".format(status_code, message))


class AnthropicAdminClient:
    def __init__(self, api_key, base_url="https://api.anthropic.com", session=None, timeout=30):
        if session is None:
            import requests
            session = requests.Session()
        self._session = session
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "User-Agent": "TA_anthropic/0.1.0 (Splunk add-on)",
        }

    def _get(self, path, params):
        url = "{}{}".format(self._base_url, path)
        resp = self._session.get(url, headers=self._headers, params=params, timeout=self._timeout)
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2)
            resp = self._session.get(url, headers=self._headers, params=params, timeout=self._timeout)
        if resp.status_code != 200:
            raise AnthropicAPIError(resp.status_code, resp.text[:500])
        return resp.json()

    def _iter_report(self, path, params):
        page_token = None
        while True:
            page_params = dict(params)
            if page_token:
                page_params["page"] = page_token
            page = self._get(path, page_params)
            yield page
            if not page.get("has_more"):
                return
            page_token = page.get("next_page")

    def iter_usage_report(self, starting_at, ending_at, bucket_width="1h", group_by=None):
        params = {"starting_at": starting_at, "ending_at": ending_at,
                  "bucket_width": bucket_width}
        if group_by:
            params["group_by[]"] = list(group_by)
        return self._iter_report("/v1/organizations/usage_report/messages", params)

    def iter_cost_report(self, starting_at, ending_at, group_by=None):
        params = {"starting_at": starting_at, "ending_at": ending_at, "bucket_width": "1d"}
        if group_by:
            params["group_by[]"] = list(group_by)
        return self._iter_report("/v1/organizations/cost_report", params)

    def _list_directory(self, path):
        items, after_id = [], None
        while True:
            params = {"limit": 100}
            if after_id:
                params["after_id"] = after_id
            page = self._get(path, params)
            items.extend(page.get("data", []))
            if not page.get("has_more"):
                return items
            after_id = page.get("last_id")

    def list_api_keys(self):
        return self._list_directory("/v1/organizations/api_keys")

    def list_users(self):
        return self._list_directory("/v1/organizations/users")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_client.py -v`
Expected: 5 PASS.

- [ ] **Step 6: Commit**

```bash
git add TA_anthropic/package/bin/anthropic_client.py tests/test_client.py tests/conftest.py
git commit -m "feat: Admin API client with pagination and single retry"
```

---

### Task 5: Transform and checkpoint-window logic (pure Python)

**Files:**
- Create: `TA_anthropic/package/bin/anthropic_transform.py`
- Test: `tests/test_transform.py`

**Interfaces:**
- Consumes: page dicts from `AnthropicAdminClient` iterators.
- Produces (used by Task 7):
  - `compute_window(checkpoint, now, backfill_days, bucket_width)` → `(starting_at, ending_at)` RFC 3339 strings snapped to the bucket boundary, or `None` when no complete bucket is pending.
  - `flatten_usage(page: dict) -> list[dict]` — one event dict per (bucket, result); keys: `time` (epoch float of bucket start), `data` (body incl. `bucket_start`, `bucket_end`, all result fields, and `total_input_tokens` = uncached + cache_read + both cache_creation counts).
  - `flatten_cost(page: dict) -> list[dict]` — same shape; body includes `amount_usd` (float dollars = `amount`/100).
  - `max_ending_at(pages: list) -> str | None` — newest `ending_at` across pages (the next checkpoint value).

- [ ] **Step 1: Write the failing tests**

`tests/test_transform.py`:

```python
import json
from datetime import datetime, timezone
from pathlib import Path

from anthropic_transform import compute_window, flatten_usage, flatten_cost, max_ending_at

FIXTURES = Path(__file__).parent.parent / "mockserver" / "fixtures"
NOW = datetime(2026, 7, 30, 12, 34, 56, tzinfo=timezone.utc)


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_first_run_backfills_and_snaps_to_hour():
    start, end = compute_window(None, NOW, backfill_days=7, bucket_width="1h")
    assert start == "2026-07-23T12:00:00Z"
    assert end == "2026-07-30T12:00:00Z"


def test_resumes_from_checkpoint():
    start, end = compute_window("2026-07-30T10:00:00Z", NOW, backfill_days=7, bucket_width="1h")
    assert start == "2026-07-30T10:00:00Z"
    assert end == "2026-07-30T12:00:00Z"


def test_empty_window_returns_none():
    assert compute_window("2026-07-30T12:00:00Z", NOW, 7, "1h") is None


def test_daily_snapping():
    start, end = compute_window(None, NOW, backfill_days=30, bucket_width="1d")
    assert start == "2026-06-30T00:00:00Z"
    assert end == "2026-07-30T00:00:00Z"


def test_flatten_usage_events():
    events = flatten_usage(load("usage_report_page1.json"))
    assert len(events) == 3  # 2 results in bucket 1 + 1 in bucket 2
    first = events[0]
    assert first["time"] == datetime(2026, 7, 28, 14, 0, tzinfo=timezone.utc).timestamp()
    assert first["data"]["model"] == "claude-opus-5"
    assert first["data"]["total_input_tokens"] == 84100 + 611000 + 41200 + 0


def test_flatten_cost_converts_cents():
    events = flatten_cost(load("cost_report.json"))
    assert events[0]["data"]["amount_usd"] == 18.342
    assert events[0]["data"]["currency"] == "USD"


def test_max_ending_at():
    pages = [load("usage_report_page1.json"), load("usage_report_page2.json")]
    assert max_ending_at(pages) == "2026-07-28T17:00:00Z"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_transform.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'anthropic_transform'`.

- [ ] **Step 3: Implement**

`TA_anthropic/package/bin/anthropic_transform.py`:

```python
"""Time-window and event-shaping logic for the Anthropic Admin API. No Splunk imports."""
from datetime import timedelta, timezone
from datetime import datetime

RFC3339 = "%Y-%m-%dT%H:%M:%SZ"


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fmt(dt):
    return dt.strftime(RFC3339)


def _snap(dt, bucket_width):
    if bucket_width == "1d":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    return dt.replace(minute=0, second=0, microsecond=0)


def compute_window(checkpoint, now, backfill_days, bucket_width):
    ending = _snap(now.astimezone(timezone.utc), bucket_width)
    if checkpoint:
        starting = _parse(checkpoint)
    else:
        starting = _snap(ending - timedelta(days=backfill_days), bucket_width)
    if starting >= ending:
        return None
    return _fmt(starting), _fmt(ending)


def _flatten(page, enrich):
    events = []
    for bucket in page.get("data", []):
        epoch = _parse(bucket["starting_at"]).timestamp()
        for result in bucket.get("results", []):
            body = dict(result)
            body["bucket_start"] = bucket["starting_at"]
            body["bucket_end"] = bucket["ending_at"]
            enrich(body)
            events.append({"time": epoch, "data": body})
    return events


def flatten_usage(page):
    def enrich(body):
        cache_creation = body.get("cache_creation") or {}
        body["total_input_tokens"] = (
            (body.get("uncached_input_tokens") or 0)
            + (body.get("cache_read_input_tokens") or 0)
            + (cache_creation.get("ephemeral_5m_input_tokens") or 0)
            + (cache_creation.get("ephemeral_1h_input_tokens") or 0)
        )
    return _flatten(page, enrich)


def flatten_cost(page):
    def enrich(body):
        body["amount_usd"] = round(float(body.get("amount") or 0) / 100.0, 6)
    return _flatten(page, enrich)


def max_ending_at(pages):
    endings = [b["ending_at"] for p in pages for b in p.get("data", [])]
    return max(endings) if endings else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_transform.py -v`
Expected: 7 PASS. Then run the whole suite: `pytest -v` — everything green.

- [ ] **Step 5: Commit**

```bash
git add TA_anthropic/package/bin/anthropic_transform.py tests/test_transform.py
git commit -m "feat: window computation and event flattening for usage/cost reports"
```

---

### Task 6: UCC scaffold and globalConfig

**Files:**
- Create: `TA_anthropic/globalConfig.json` (generated, then edited)
- Create: `TA_anthropic/package/**` (generated; merged with the two bin/ modules from Tasks 4–5)
- Create: `TA_anthropic/package/lib/requirements.txt`

**Interfaces:**
- Produces: a UCC source tree where `ucc-gen build` succeeds; account config exposes `api_key` (encrypted) + `api_base_url`; three inputs exist: `anthropic_usage`, `anthropic_cost`, `anthropic_directory`, each with `account`, `interval`, and (usage/cost) `backfill_days` parameters. Task 7 writes the helper bodies.

- [ ] **Step 1: Generate the scaffold in a temp dir and merge**

```bash
SCRATCH=$(mktemp -d)
(cd "$SCRATCH" && ucc-gen init --addon-name TA_anthropic \
  --addon-display-name "Anthropic Add-on for Splunk" \
  --addon-input-name anthropic_usage \
  --addon-version 0.1.0)
rsync -a "$SCRATCH/TA_anthropic/" TA_anthropic/
```

(`rsync` merges the scaffold around the already-created `package/bin/anthropic_client.py` / `anthropic_transform.py`.)

- [ ] **Step 2: Verify the pristine scaffold builds**

Run: `cd TA_anthropic && ucc-gen build --source package && cd ..`
Expected: build completes; `TA_anthropic/output/TA_anthropic/` exists. Fix any environment issue before editing configs.

- [ ] **Step 3: Edit globalConfig.json — account fields**

In `TA_anthropic/globalConfig.json`, locate `pages.configuration.tabs` → the `account` tab → `entity` array. Replace its field list so it contains exactly (keep the generated `name` field's validators if richer):

```json
[
  {
    "type": "text",
    "label": "Account name",
    "field": "name",
    "required": true,
    "help": "A unique name for this Anthropic organization.",
    "validators": [
      {"type": "regex", "pattern": "^[a-zA-Z]\\w*$",
       "errorMsg": "Account name must start with a letter and contain only letters, digits, underscores."}
    ]
  },
  {
    "type": "text",
    "label": "Admin API key",
    "field": "api_key",
    "encrypted": true,
    "required": true,
    "help": "Anthropic Admin API key (sk-ant-admin01-...). Created in Console > Settings > Organization."
  },
  {
    "type": "text",
    "label": "API base URL",
    "field": "api_base_url",
    "required": false,
    "defaultValue": "https://api.anthropic.com",
    "help": "Override for testing/proxies. The live demo fallback sets this to http://127.0.0.1:8081 (local mock server)."
  }
]
```

- [ ] **Step 4: Edit globalConfig.json — inputs**

In `pages.inputs.services`, configure three services. Model each on the generated `anthropic_usage` service, keeping the generated structure (`name`, handler wiring keys, `entity` skeleton) and setting per-service entities as follows (every service keeps the generated `name` field and interval validator):

- Service `anthropic_usage`, title `Anthropic Usage Report`: fields `name`, `account` (type `singleSelect`, `options.referenceName: "account"`, required), `interval` (default `300`), `backfill_days` (type `text`, default `7`, help "Days of history to ingest on first run (hourly buckets).")
- Service `anthropic_cost`, title `Anthropic Cost Report`: fields `name`, `account`, `interval` (default `3600`), `backfill_days` (default `30`)
- Service `anthropic_directory`, title `Anthropic Org Directory`: fields `name`, `account`, `interval` (default `3600`)

Duplicate the generated service block twice and edit rather than writing from scratch — the generated block carries UCC-version-specific keys that must be preserved.

- [ ] **Step 5: Runtime dependencies**

`TA_anthropic/package/lib/requirements.txt`:

```text
splunktaucclib
solnlib
splunk-sdk
requests
```

- [ ] **Step 6: Rebuild and verify**

Run: `cd TA_anthropic && ucc-gen build --source package && cd ..`
Expected: build succeeds; `TA_anthropic/output/TA_anthropic/bin/` contains `anthropic_usage.py`, `anthropic_cost.py`, `anthropic_directory.py` (generated wiring) alongside `anthropic_client.py` and `anthropic_transform.py`; `output/TA_anthropic/default/inputs.conf` lists the three modular input stanzas.

- [ ] **Step 7: Commit**

```bash
git add TA_anthropic .gitignore
git commit -m "feat: UCC scaffold with account (encrypted key + base URL) and three inputs"
```

(Add `TA_anthropic/output/` to `.gitignore` if `ucc-gen` wrote it inside the source tree.)

---

### Task 7: Modular input helpers

**Files:**
- Create/Modify: `TA_anthropic/package/bin/anthropic_usage_helper.py`
- Create/Modify: `TA_anthropic/package/bin/anthropic_cost_helper.py`
- Create/Modify: `TA_anthropic/package/bin/anthropic_directory_helper.py`

**Interfaces:**
- Consumes: `AnthropicAdminClient` (Task 4), `compute_window`/`flatten_usage`/`flatten_cost`/`max_ending_at` (Task 5), UCC-generated wiring from Task 6.
- Produces: `stream_events(inputs, event_writer)` implementations writing JSON events with sourcetypes `anthropic:usage`, `anthropic:cost`, `anthropic:api_keys`, `anthropic:users`; checkpoints stored in KV Store collection `TA_anthropic_checkpoints` keyed by input name.

> `ucc-gen init` generates helper stubs (named like `<input>_helper.py`) with `validate_input` / `stream_events` signatures. Keep the generated signatures and imports; replace the body logic with the code below, adapting names if the installed UCC version's stub differs.

- [ ] **Step 1: Implement the usage helper**

`TA_anthropic/package/bin/anthropic_usage_helper.py` (body; keep generated imports such as `import_declare_test`):

```python
import json
from datetime import datetime, timezone

from solnlib import conf_manager, log
from solnlib.modular_input import checkpointer
from splunklib import modularinput as smi

from anthropic_client import AnthropicAdminClient
from anthropic_transform import compute_window, flatten_usage, max_ending_at

ADDON_NAME = "TA_anthropic"
CHECKPOINT_COLLECTION = "TA_anthropic_checkpoints"
SOURCETYPE = "anthropic:usage"
GROUP_BY = ["model", "workspace_id", "api_key_id", "service_tier"]


def logger_for_input(input_name):
    return log.Logs().get_logger("{}_{}".format(ADDON_NAME.lower(), input_name))


def get_account_config(session_key, account_name):
    cfm = conf_manager.ConfManager(
        session_key, ADDON_NAME,
        realm="__REST_CREDENTIAL__#{}#configs/conf-ta_anthropic_account".format(ADDON_NAME),
    )
    return cfm.get_conf("ta_anthropic_account").get(account_name)


def validate_input(definition):
    return


def stream_events(inputs, event_writer):
    for input_name, input_item in inputs.inputs.items():
        normalized_name = input_name.split("/")[-1]
        logger = logger_for_input(normalized_name)
        session_key = inputs.metadata["session_key"]
        try:
            account = get_account_config(session_key, input_item.get("account"))
            client = AnthropicAdminClient(
                api_key=account.get("api_key"),
                base_url=account.get("api_base_url") or "https://api.anthropic.com",
            )
            ckpt = checkpointer.KVStoreCheckpointer(
                CHECKPOINT_COLLECTION, session_key, ADDON_NAME)
            ckpt_key = "usage_{}".format(normalized_name)
            window = compute_window(
                ckpt.get(ckpt_key),
                datetime.now(timezone.utc),
                backfill_days=int(input_item.get("backfill_days") or 7),
                bucket_width="1h",
            )
            if window is None:
                logger.info("No complete bucket pending; skipping run.")
                continue
            starting_at, ending_at = window
            pages = []
            count = 0
            for page in client.iter_usage_report(starting_at, ending_at,
                                                 bucket_width="1h", group_by=GROUP_BY):
                pages.append(page)
                for event in flatten_usage(page):
                    event_writer.write_event(smi.Event(
                        data=json.dumps(event["data"], ensure_ascii=False, default=str),
                        time="{:.3f}".format(event["time"]),
                        sourcetype=SOURCETYPE,
                        source="anthropic_usage://{}".format(normalized_name),
                    ))
                    count += 1
            new_ckpt = max_ending_at(pages)
            if new_ckpt:
                ckpt.update(ckpt_key, new_ckpt)
            logger.info("Ingested {} usage events; checkpoint={}".format(count, new_ckpt))
        except Exception as e:
            logger.error("Ingestion failed: {}".format(e))
```

- [ ] **Step 2: Implement the cost helper**

`anthropic_cost_helper.py`: identical structure to Step 1 with these substitutions — `SOURCETYPE = "anthropic:cost"`, `GROUP_BY = ["workspace_id", "description"]`, checkpoint key prefix `cost_`, `bucket_width="1d"`, default `backfill_days` 30, iterator `client.iter_cost_report(starting_at, ending_at, group_by=GROUP_BY)`, flattener `flatten_cost`, source `anthropic_cost://{normalized_name}`. Write the full file (repeat the boilerplate; do not import from the usage helper).

- [ ] **Step 3: Implement the directory helper**

`anthropic_directory_helper.py`: same account/client boilerplate, no checkpoint or window. Body of the per-input loop after building `client`:

```python
            # One snapshot instant per run, used for BOTH the in-body
            # ``snapshot_at`` string and the explicit event ``time``.
            snapshot_dt = datetime.now(timezone.utc).replace(microsecond=0)
            now_iso = snapshot_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            snapshot_time = "{:.3f}".format(snapshot_dt.timestamp())
            for sourcetype, items in (
                ("anthropic:api_keys", client.list_api_keys()),
                ("anthropic:users", client.list_users()),
            ):
                for item in items:
                    body = dict(item)
                    body["snapshot_at"] = now_iso
                    event_writer.write_event(smi.Event(
                        data=json.dumps(body, ensure_ascii=False, default=str),
                        time=snapshot_time,
                        sourcetype=sourcetype,
                        source="anthropic_directory://{}".format(normalized_name),
                    ))
            logger.info("Directory snapshot written.")
```

> **The explicit `time=` is load-bearing.** Directory events have no bucket to take a timestamp from, and the plan originally omitted `time=`. Without it Splunk falls back to timestamp heuristics and picks `created_at` / `added_at` out of the JSON body — back-dating each snapshot by **months** (the fixtures' key-creation dates are January–March). The baseline saved search dispatches over `-24h`, so it would find nothing and the whole shadow-AI reveal would fail. Truncating to whole seconds keeps the epoch exactly equal to the second-resolution `snapshot_at` string.

- [ ] **Step 4: Verify build still passes and pure modules stay pure**

Run:
```bash
cd TA_anthropic && ucc-gen build --source package && cd ..
pytest -v
```
Expected: build OK; the full pytest suite still passes (helpers are not imported by tests — they require Splunk libs).

- [ ] **Step 5: Commit**

```bash
git add TA_anthropic/package/bin
git commit -m "feat: modular input helpers with KV Store checkpointing"
```

---

### Task 8: Sourcetypes, lookup, alert, and dashboard

**Files:**
- Create: `TA_anthropic/package/default/props.conf`
- Create: `TA_anthropic/package/default/transforms.conf`
- Create: `TA_anthropic/package/default/savedsearches.conf`
- Create: `TA_anthropic/package/default/data/ui/views/ai_observability.xml`
- Create/Modify: `TA_anthropic/package/default/data/ui/nav/default.xml`
- Create: `TA_anthropic/package/lookups/anthropic_api_key_baseline.csv`

**Interfaces:**
- Consumes: event shapes from Task 7 (JSON bodies, `amount_usd`, `total_input_tokens`, `api_key_id`).
- Produces: lookup `anthropic_api_key_baseline`, saved search `Anthropic - Build API Key Baseline`, alert `Anthropic - Unrecognized API Key`, dashboard `ai_observability`.

- [ ] **Step 1: props.conf**

```ini
[anthropic:usage]
SHOULD_LINEMERGE = false
LINE_BREAKER = ([\r\n]+)
KV_MODE = json
TRUNCATE = 100000
category = Structured
description = Anthropic Admin API usage report events

[anthropic:cost]
SHOULD_LINEMERGE = false
LINE_BREAKER = ([\r\n]+)
KV_MODE = json
TRUNCATE = 100000
category = Structured
description = Anthropic Admin API cost report events

[anthropic:api_keys]
SHOULD_LINEMERGE = false
LINE_BREAKER = ([\r\n]+)
KV_MODE = json
TRUNCATE = 100000
category = Structured
description = Anthropic organization API key inventory snapshots

[anthropic:users]
SHOULD_LINEMERGE = false
LINE_BREAKER = ([\r\n]+)
KV_MODE = json
TRUNCATE = 100000
category = Structured
description = Anthropic organization user inventory snapshots
```

- [ ] **Step 2: transforms.conf + seed lookup**

`transforms.conf`:

```ini
[anthropic_api_key_baseline]
filename = anthropic_api_key_baseline.csv
```

`TA_anthropic/package/lookups/anthropic_api_key_baseline.csv` — **pre-populated with the two known keys**, not header-only:

```csv
id,name,workspace_id,status
apikey_demo_ci,ci-pipeline,wrkspc_demo_platform,active
apikey_demo_claudecode,claude-code-team,wrkspc_demo_eng,active
```

> Originally a header-only seed. Shipping it empty means the shadow-AI panel flags **every** key until the `*/30` baseline cron has fired at least once after the directory input has run — so a fresh install shows the wrong answer during exactly the window the demo runs in. Pre-populating makes the reveal correct from the first second; the scheduled search then keeps it current. The rows deliberately match `api_keys.json` and deliberately exclude `apikey_rogue_demo`.

- [ ] **Step 3: savedsearches.conf**

```ini
[Anthropic - Build API Key Baseline]
search = sourcetype=anthropic:api_keys | stats latest(name) as name latest(workspace_id) as workspace_id latest(status) as status by id | outputlookup override_if_empty=false anthropic_api_key_baseline.csv
description = Rebuilds the known-API-key baseline from the newest org directory snapshot. override_if_empty=false because outputlookup DELETES the lookup file when a run returns no rows; a -7d window plus that guard means one missed directory run cannot empty the baseline and unblank the shadow-AI panel.
cron_schedule = */30 * * * *
enableSched = 1
dispatch.earliest_time = -7d
dispatch.latest_time = now

[Anthropic - Unrecognized API Key]
search = sourcetype=anthropic:usage api_key_id=* | stats sum(total_input_tokens) as input_tokens sum(output_tokens) as output_tokens by api_key_id | lookup anthropic_api_key_baseline id as api_key_id OUTPUT name | where isnull(name)
cron_schedule = */5 * * * *
enableSched = 1
dispatch.earliest_time = -24h
dispatch.latest_time = now
alert.severity = 4
alert.track = 1
counttype = number of events
relation = greater than
quantity = 0

[Anthropic - Token Spike Anomaly]
search = sourcetype=anthropic:usage | bin _time span=1h | stats sum(total_input_tokens) as tokens by _time | sort 0 _time | streamstats window=6 current=f avg(tokens) as baseline stdevp(tokens) as sd | where sd > 0 AND tokens > 2000000 AND tokens > baseline + 2*sd | eval baseline = round(baseline), sd = round(sd), spike_ratio = round(tokens / baseline, 2) | table _time tokens baseline sd spike_ratio
description = Fires on an hour whose input tokens clear an absolute floor of 2,000,000 AND exceed the mean of the 6 PRECEDING hours by more than 2 population standard deviations. current=f keeps the spiking hour out of its own baseline - with current=t the z-score of n buckets is capped at (n-1)/sqrt(n), so a 3-sigma test over a short window can never fire.
cron_schedule = */15 * * * *
enableSched = 1
dispatch.earliest_time = -48h
dispatch.latest_time = now
alert.severity = 3
alert.track = 1
counttype = number of events
relation = greater than
quantity = 0
```

(The token-spike alert is the spec's backup security beat; the rogue-key alert is the primary. Both land in Triggered Alerts.)

> **Alert keys: `counttype`/`relation`/`quantity` only.** The plan originally also carried `alert_type`, `alert_comparator`, and `alert_threshold`. Those are **REST argument names**, not `savedsearches.conf` settings — they are absent from `savedsearches.conf.spec` on Splunk 10.2.1 (`grep -c` returns 0) and `slim validate` rejects them. The three that *are* in the spec (lines 284/290/294) are the ones kept. Do not reintroduce the other three.
>
> **`override_if_empty=false` on the baseline builder.** `outputlookup`'s default is `override_if_empty=true`, which per `searchbnf.conf` "deletes the lookup file if it exists" when the search returns no rows. If the directory input has not run inside the dispatch window the search returns zero rows and would **wipe the pre-populated baseline**, after which every API key reads as unrecognized. `override_if_empty=false` makes a zero-result run a no-op instead. The window was also widened from `-24h` to `-7d` as belt-and-braces: one missed hourly directory run can no longer empty the baseline even in principle.

- [ ] **Step 4: Dashboard**

`TA_anthropic/package/default/data/ui/views/ai_observability.xml`:

```xml
<dashboard version="1.1" theme="dark">
  <label>AI Observability &amp; Security</label>
  <description>Anthropic org usage, cost, and anomaly monitoring — ingested by TA_anthropic</description>
  <row>
    <panel>
      <title>Token Usage Over Time by Model</title>
      <chart>
        <search>
          <query>sourcetype=anthropic:usage | timechart span=1h sum(total_input_tokens) as input sum(output_tokens) as output by model</query>
          <earliest>-7d</earliest>
          <latest>now</latest>
        </search>
        <option name="charting.chart">column</option>
        <option name="charting.chart.stackMode">stacked</option>
      </chart>
    </panel>
    <panel>
      <title>Daily Cost by Workspace (USD)</title>
      <chart>
        <search>
          <query>sourcetype=anthropic:cost | eval workspace=coalesce(workspace_id, "default") | timechart span=1d sum(amount_usd) as usd by workspace</query>
          <earliest>-30d</earliest>
          <latest>now</latest>
        </search>
        <option name="charting.chart">column</option>
        <option name="charting.chart.stackMode">stacked</option>
      </chart>
    </panel>
  </row>
  <row>
    <panel>
      <title>Cache Hit Ratio (cost optimization)</title>
      <single>
        <search>
          <query>sourcetype=anthropic:usage | stats sum(cache_read_input_tokens) as cached sum(total_input_tokens) as total | eval ratio=round(100*cached/total,1) | fields ratio</query>
          <earliest>-7d</earliest>
          <latest>now</latest>
        </search>
        <option name="unit">%</option>
        <option name="rangeColors">["0xdc4e41","0xf1813f","0x53a051"]</option>
        <option name="rangeValues">[30,60]</option>
        <option name="useColors">1</option>
      </single>
    </panel>
    <panel>
      <title>Top API Keys by Output Tokens</title>
      <table>
        <search>
          <query>sourcetype=anthropic:usage | stats sum(output_tokens) as output_tokens by api_key_id | lookup anthropic_api_key_baseline id as api_key_id OUTPUT name | eval name=coalesce(name, "UNRECOGNIZED") | sort -output_tokens | table name api_key_id output_tokens</query>
          <earliest>-7d</earliest>
          <latest>now</latest>
        </search>
      </table>
    </panel>
    <panel>
      <title>Unrecognized API Keys (shadow AI)</title>
      <table>
        <search>
          <query>sourcetype=anthropic:usage api_key_id=* | stats sum(total_input_tokens) as input_tokens sum(output_tokens) as output_tokens by api_key_id | lookup anthropic_api_key_baseline id as api_key_id OUTPUT name | where isnull(name)</query>
          <earliest>-7d</earliest>
          <latest>now</latest>
        </search>
      </table>
    </panel>
  </row>
</dashboard>
```

Nav — merge into the generated `TA_anthropic/package/default/data/ui/nav/default.xml` (create if absent):

```xml
<nav search_view="search">
  <view name="ai_observability" default="true"/>
  <view name="inputs"/>
  <view name="configuration"/>
  <view name="search"/>
</nav>
```

- [ ] **Step 5: Build and verify**

Run: `cd TA_anthropic && ucc-gen build --source package && cd ..`
Expected: build succeeds; `output/TA_anthropic/default/` contains props, transforms, savedsearches, and the view.

- [ ] **Step 6: Commit**

```bash
git add TA_anthropic/package/default TA_anthropic/package/lookups
git commit -m "feat: sourcetypes, shadow-AI alert, and AI observability dashboard"
```

---

### Task 9: Build and package pipeline (SLIM)

**Files:**
- Create: `scripts/build.sh`

**Interfaces:**
- Produces: `dist/TA_anthropic-0.1.0.tar.gz` (SLIM package) — the on-stage "ready to distribute" artifact; extracted into `/opt/splunk/etc/apps/` by Task 10.

- [ ] **Step 1: Write build.sh**

```bash
#!/usr/bin/env bash
# Build TA_anthropic: ucc-gen build -> slim validate -> slim package
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate

echo "==> ucc-gen build"
(cd TA_anthropic && ucc-gen build --source package)

echo "==> slim validate"
slim validate TA_anthropic/output/TA_anthropic

echo "==> slim package"
mkdir -p dist
slim package TA_anthropic/output/TA_anthropic -o dist

echo "==> done"
ls -lh dist/
```

- [ ] **Step 2: Run it**

Run: `chmod +x scripts/build.sh && ./scripts/build.sh`
Expected: `dist/TA_anthropic-0.1.0.tar.gz` exists. `slim validate` may emit warnings (acceptable) but no errors. If SLIM complains about a missing `app.manifest`, run `slim generate-manifest TA_anthropic/output/TA_anthropic -o TA_anthropic/package/app.manifest` once, rebuild, and re-run.

- [ ] **Step 3: AppInspect validation (the "production-ready" proof point)**

```bash
pip install splunk-appinspect
splunk-appinspect inspect dist/TA_anthropic-0.1.0.tar.gz --mode precert
```

Expected: report ends with 0 failures (warnings and manual checks are acceptable). If `splunk-appinspect` fails to install on this Python version, note it in the runbook and rely on `slim validate` for the on-stage claim — do not block the pipeline on it.

- [ ] **Step 4: Add dist/ to .gitignore and commit**

```bash
echo "dist/" >> .gitignore
git add scripts/build.sh .gitignore
git commit -m "feat: build pipeline (ucc-gen -> slim validate -> slim package)"
```

---

### Task 10: Local Splunk 10.2.1 end-to-end against the mock

> **Rewritten.** This task originally stood up `splunk/docker-compose.yml`. The demo box has Splunk Enterprise 10.2.1 installed at `/opt/splunk`, so the container was dropped (see "Deviation from spec"). No compose file is created. Everything here is done in the **UI**, because we hold no admin credentials on this box — the REST calls the Docker version used are not available to us.

**Files:**
- None. (`splunk/.env.example` from Task 1 stays as the password template for whoever owns the box.)

**Interfaces:**
- Consumes: built TA (Task 9), mock server (Task 3).
- Produces: the local Splunk at `http://localhost:8000` with TA_anthropic installed, configured against the mock, with usage/cost/directory events searchable and the alert able to fire.

- [ ] **Step 1: Start the mock and install the TA**

```bash
.venv/bin/uvicorn mockserver.app:app --port 8081 &
tar -xzf dist/TA_anthropic-*.tar.gz -C /opt/splunk/etc/apps/
/opt/splunk/bin/splunk restart
```

Restart takes ~60–90 s, not the 2–4 minutes a container first boot took.

- [ ] **Step 2: Configure the account in the UI**

Splunk Web → **TA_anthropic → Configuration → Account → Add**:
`name=demo_org`, `api_key=sk-ant-admin01-demo` (any non-empty string — the mock only checks presence), `api_base_url=http://127.0.0.1:8081`.

- [ ] **Step 3: Enable all three inputs in the UI**

**TA_anthropic → Inputs → Create New Input**, once per input:

| Input | name | account | interval | backfill_days |
|---|---|---|---|---|
| Anthropic Usage Report | `usage` | demo_org | 300 | 7 |
| Anthropic Cost Report | `cost` | demo_org | 3600 | 30 |
| Anthropic Org Directory | `directory` | demo_org | 3600 | — |

**All three, not two.** `anthropic_directory` is the only producer of `sourcetype=anthropic:api_keys`, which is the only input to the baseline saved search. Skip it and the shadow-AI reveal flags every key instead of one.

- [ ] **Step 4: Verify events landed**

In the search bar (no credentials needed), within ~2 minutes of the first input run:

```
index=main sourcetype=anthropic:usage    earliest=-7d  | stats count
index=main sourcetype=anthropic:cost     earliest=-30d | stats count
index=main sourcetype=anthropic:api_keys earliest=-24h | stats count
```

Expected: usage ≥ 5, cost ≥ 3, api_keys = 2. The api_keys count over `-24h` is the real assertion for the timestamp fix in Task 7 — before it, those events landed months in the past and this returned 0.

Then verify no duplicates: disable/enable the usage input and re-run the usage count — unchanged. (Note that within the same clock hour this is *guaranteed* unchanged because `compute_window` returns `None`; the stronger test is across an hour boundary.)

- [ ] **Step 5: Verify the alert story**

```
| savedsearch "Anthropic - Build API Key Baseline"
| inputlookup anthropic_api_key_baseline
| savedsearch "Anthropic - Unrecognized API Key"
```

Expected: the lookup holds exactly 2 rows (`apikey_demo_ci`, `apikey_demo_claudecode`) and the last search returns **exactly one row**, `api_key_id=apikey_rogue_demo`. Open `http://localhost:8000/en-US/app/TA_anthropic/ai_observability` and confirm all four panels render with data.

- [ ] **Step 6: Commit**

Nothing to commit for this task (no compose file). Record the verified counts in the runbook instead.

---

### Task 11: Fixture recorder and preflight script

**Files:**
- Create: `scripts/record_fixtures.py`
- Create: `scripts/preflight.sh`

**Interfaces:**
- Consumes: `AnthropicAdminClient`; a real Admin key via env `ANTHROPIC_ADMIN_KEY`.
- Produces: raw captures for refreshing fixtures (gitignored `raw_*`); a preflight report exiting non-zero on any failed check.

- [ ] **Step 1: record_fixtures.py**

```python
"""Capture real Admin API responses for refreshing the mock fixtures.

Usage: ANTHROPIC_ADMIN_KEY=sk-ant-admin01-... python scripts/record_fixtures.py
Writes raw responses to mockserver/fixtures/raw_*.json (gitignored). Manually
review raw_* files, sanitize names/emails, then merge into the committed
fixtures — keeping apikey_rogue_demo in usage page 2 and OUT of api_keys.json.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "TA_anthropic" / "package" / "bin"))
from anthropic_client import AnthropicAdminClient  # noqa: E402

OUT = Path(__file__).parent.parent / "mockserver" / "fixtures"


def main():
    key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not key:
        sys.exit("Set ANTHROPIC_ADMIN_KEY (sk-ant-admin01-...)")
    client = AnthropicAdminClient(key)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    usage_pages = list(client.iter_usage_report(
        (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        bucket_width="1h",
        group_by=["model", "workspace_id", "api_key_id", "service_tier"]))
    for i, page in enumerate(usage_pages, 1):
        (OUT / "raw_usage_page{}.json".format(i)).write_text(json.dumps(page, indent=2))

    cost_pages = list(client.iter_cost_report(
        (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z"),
        now.strftime("%Y-%m-%dT00:00:00Z"),
        group_by=["workspace_id", "description"]))
    (OUT / "raw_cost.json").write_text(json.dumps({"pages": cost_pages}, indent=2))

    (OUT / "raw_api_keys.json").write_text(json.dumps(
        {"data": client.list_api_keys(), "has_more": False}, indent=2))
    (OUT / "raw_users.json").write_text(json.dumps(
        {"data": client.list_users(), "has_more": False}, indent=2))

    print("Wrote raw_* files to {}. Review, sanitize, then merge into the "
          "committed fixtures (keep apikey_rogue_demo in usage page 2 only).".format(OUT))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: preflight.sh**

No Docker checks, and **no authenticated REST checks** — we hold no admin credentials on this box, so every check is either local-filesystem, unauthenticated HTTP, or the unauthenticated `splunk status` CLI. Run: `scripts/preflight.sh` (env overrides `SPLUNK_HOME`, `SPLUNK_WEB_URL`, `MOCK_URL`, `ANTHROPIC_ADMIN_KEY`).

The check list:

```text
# build toolchain          .venv/bin/python, .venv/bin/slim, .venv/bin/ucc-gen   (explicit paths, no `activate`)
# code health              .venv/bin/pytest -q tests
# artifacts                ls dist/TA_anthropic-*.tar.gz
# runtime services         mock answering on $MOCK_URL (x-api-key header)
#                          $SPLUNK_HOME/bin/splunk status
#                          test -d $SPLUNK_HOME/etc/apps/TA_anthropic
#                          Splunk web UI returns 200/302/303
# demo safety net          ls demo/backup_screencast.*
# optional                 real Admin API — SKIPPED unless ANTHROPIC_ADMIN_KEY is set
```

The script prints `PASS`/`FAIL`/`SKIP` per check, a tally, a list of failed checks, and exits non-zero if any required check failed. The optional Admin API check is a `SKIP` (not a `FAIL`) when the key is absent, since the demo runs against the mock by design.

- [ ] **Step 3: Verify and commit**

Run: `chmod +x scripts/preflight.sh && scripts/preflight.sh || true`
Expected: all checks PASS except `screencast backup present` (until the screencast is recorded), with `real Admin API reachable` reported as SKIP.

```bash
git add scripts/record_fixtures.py scripts/preflight.sh
git commit -m "feat: fixture recorder and day-of preflight checks"
```

---

### Task 12: Demo assets — prompt, runbook, checkpoints

**Files:**
- Create: `demo/demo_prompt.md`
- Create: `demo/runbook.md`
- Create: git tags `demo-scaffold`, `demo-client`, `demo-inputs`, `demo-final`

**Interfaces:**
- Consumes: everything above.
- Produces: the exact on-stage prompt, the run-of-show + failure decision tree, and jumpable git checkpoints.

- [ ] **Step 1: demo_prompt.md**

```markdown
# The prompt fed to Claude Code live on stage

Paste verbatim after opening Claude Code in an empty `live-build/` directory:

---

I'm a Splunk developer. Build me a production-ready Splunk Technical Add-on that
ingests AI usage telemetry from Anthropic's Admin API.

Requirements:
- Use Splunk's UCC framework (`ucc-gen`) to scaffold it. Name: TA_anthropic.
- Ingest `GET /v1/organizations/usage_report/messages` (hourly buckets, grouped
  by model, workspace_id, api_key_id, service_tier) and
  `GET /v1/organizations/cost_report` (daily buckets, grouped by workspace_id
  and description). Auth is an Admin API key via the `x-api-key` header plus
  `anthropic-version: 2023-06-01`. Both endpoints paginate with
  `has_more`/`next_page`.
- Store the Admin key encrypted via the UCC setup page. Make the API base URL a
  configurable setting.
- Checkpoint the last completed bucket so re-runs never duplicate events.
- Sourcetypes: `anthropic:usage` and `anthropic:cost`, JSON events.
- Package it with `ucc-gen build` and validate with `slim validate`.

The API docs are at https://platform.claude.com/docs/en/api/usage-cost-api —
fetch them if you need the exact response shapes. Start by showing me your plan.

---

Timing notes: kick this off at minute 4. By minute 8 Claude Code has typically
finished planning and scaffolded with ucc-gen — cut to slides. Return at minute
14 for the reveal, then switch to the pre-built Splunk (local install at
`/opt/splunk`, web UI on http://localhost:8000).
```

> `demo/demo_prompt.md` is quoted **verbatim** in the slide deck. Treat the shipped file as frozen; if it must change, the deck changes with it.

- [ ] **Step 2: runbook.md**

**`demo/runbook.md` is the live document — read it there, not here.** The draft that used to be inlined at this point has been superseded and is not reproduced, so the two cannot drift apart again. What it must contain, and what changed from the draft:

- **Run of show** (30 min, six beats) — unchanged from the draft.
- **Pre-stage** — now opens with *obtain and record the Splunk admin credentials*, because the checkpoint reset is the one recovery that needs them and there is no other way in. Then build → mock → extract into `/opt/splunk/etc/apps/` → `splunk restart`. No `docker compose`.
- **Enable all THREE inputs**, with the reason spelled out on the page: `anthropic_directory` is the sole source of `anthropic:api_keys`, the sole input to the baseline saved search. Two-of-three silently produces the wrong reveal.
- **Build the baseline by hand** — `| savedsearch "Anthropic - Build API Key Baseline"` rather than waiting out the `*/30` cron, then assert `| inputlookup anthropic_api_key_baseline` = 2 rows and the Unrecognized panel = exactly 1 row (`apikey_rogue_demo`). One row means the demo is armed.
- **Failure decision tree** — the Splunk row is `/opt/splunk/bin/splunk restart`, not `docker compose down/up`.
- **"No new events" section** — a no-op inside the same clock hour is *correct behaviour* (`compute_window` only releases completed buckets), not a fault to debug on stage. Checkpoints are in the **KV store** (`TA_anthropic_checkpoints`), inspected with `| rest /servicesNS/nobody/TA_anthropic/storage/collections/data/TA_anthropic_checkpoints` and cleared with a DELETE to the same endpoint (admin creds). Nothing under `/opt/splunk/var/lib/splunk/modinputs/` belongs to this add-on.
- **Checkpoint tags** — `demo-scaffold`, `demo-client`, `demo-inputs`, `demo-final`.

- [ ] **Step 3: Create the tags**

```bash
git log --oneline   # identify the commits from Tasks 5, 6, 7, 10
git tag demo-client   <commit-of-task-5>
git tag demo-scaffold <commit-of-task-6>
git tag demo-inputs   <commit-of-task-7>
git tag demo-final    <commit-of-task-10>
```

- [ ] **Step 4: Commit**

```bash
git add demo/
git commit -m "feat: on-stage prompt, runbook, and checkpoint tags"
```

---

## Out of scope (human tasks, tracked in the runbook)

- Recording `demo/backup_screencast.mp4` (requires a rehearsed live run).
- Creating the real Admin API key (Console → Settings → Organization; verify org-admin access).
- Running `scripts/record_fixtures.py` against the real org and sanitizing.
- Slides (`conf26_Breakout_Template.pptx`) and the QR-code public repo.
- Optional stretch from the spec (Claude Code Analytics API input) — only after `demo-final` is tagged.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

Materials for `.conf2026` session DEV1194 — *Agentic Data Ingestion: Building
Splunk Add-Ons with Anthropic's Claude*. It contains **two** Splunk Technology
Add-ons built with the UCC framework, offline FastAPI mocks of both upstream
APIs, and a stage runbook.

- **`TA_anthropic`** — ingests the Anthropic Admin API (usage, cost, api_keys, users)
- **`TA_openrouter`** — ingests the OpenRouter management API (analytics, key roster)

The two add-ons are independent deliverables that share one `.venv` and one
`requirements-dev.txt` at the repo root. There is no per-add-on venv or
requirements file.

Each add-on documents itself: `TA_anthropic/README.md` and
`TA_openrouter/README.md`, with `TA_openrouter/docs/` going considerably
deeper. `docs/BUILD_LOG.md` records the commit history this repository was
published from.

## Setup

```bash
/opt/homebrew/bin/python3.11 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip install ./splunk_packaging_toolkit-1.2.8   # vendored SLIM
```

`requirements-dev.txt` installs `ucc-gen` but **not** `slim`. SLIM ships as the
vendored `splunk_packaging_toolkit-1.2.8/` directory, which is git-ignored — a
fresh clone won't have it, and must `pip install splunk-packaging-toolkit` from
PyPI instead.

## Commands

```bash
# tests — both suites compose from the repo root (202 total)
.venv/bin/pytest -q tests TA_openrouter/tests
.venv/bin/pytest -q tests                    # TA_anthropic + mockserver (58)
.venv/bin/pytest -q TA_openrouter/tests      # TA_openrouter (144)
.venv/bin/pytest -q TA_openrouter/tests/test_openrouter_conf.py::test_all_three_sourcetypes_declared

# build (each add-on has its OWN script — they are not interchangeable)
./scripts/build.sh                           # TA_anthropic  -> dist/
TA_openrouter/scripts/build.sh               # TA_openrouter -> dist/
TA_VERSION=0.2.0 ./scripts/build.sh          # version override, both scripts

# offline mocks — set the add-on account's base URL to the address in the comment
.venv/bin/python -m uvicorn mockserver.app:app --port 8081          # Anthropic  -> http://127.0.0.1:8081
cd TA_openrouter && ../.venv/bin/python -m uvicorn openrouter_mockserver.app:app --port 8082
                                                                    # OpenRouter -> http://127.0.0.1:8082/api/v1

./scripts/preflight.sh                       # readiness checks for both add-ons, non-zero on any failure
```

Each suite's `conftest.py` puts its own add-on's `package/bin` on `sys.path`,
so add-on modules are imported by bare name (`from openrouter_transform import
...`), not by package path.

## Architecture

Both add-ons use the same four-layer shape, and the layering is what makes them
testable without a Splunk install:

| Layer | Files | Splunk imports? |
| --- | --- | --- |
| HTTP | `*_client.py` | no |
| Pure logic | `*_transform.py` | no |
| Modular-input entry points | `*_helper.py` | yes, behind `try/except ImportError` |
| Config/search surface | `default/*.conf`, `data/ui/views/*.xml`, `globalConfig.json` | n/a |

Keep client and transform free of Splunk imports and of any clock the caller
didn't pass in. That property, plus the helpers' guarded imports, is why the
whole suite runs in seconds with no Splunk present — don't break it for
convenience.

**Read `TA_openrouter/docs/architecture.md` before changing anything in
`TA_openrouter/`.** It is unusually detailed and documents the checkpoint and
truncation contracts, the two-query analytics design, and every build-time trap
below. Don't re-derive it. `docs/operations.md` covers day-2 operator behavior;
`docs/schema-verification.md` records which API fields were observed live
versus assumed.

### The two add-ons differ in ways that do not generalize

Do not carry a convention from one to the other:

- **Auth** — Anthropic uses `x-api-key` + `anthropic-version: 2023-06-01`;
  OpenRouter uses `Authorization: Bearer`.
- **Money** — Anthropic cost amounts are **cents** and `flatten_cost` divides by
  100 (the Admin API reference states this verbatim; the divisor has been
  wrongly flagged as a 100× bug before — do not remove it). OpenRouter is USD
  with no divisor.
- **Pagination** — Anthropic reports use `has_more` → `next_page`, its directory
  endpoints use `has_more` → `last_id`, and OpenRouter uses offset pagination
  that stops only on an **empty** page.
- **Timestamps** — OpenRouter analytics rows are space-separated with no UTC
  offset (`2026-07-31 18:00:00`), while the add-on's own `snapshot_at` is RFC
  3339. Each sourcetype gets its own `TIME_FORMAT` in `props.conf`; the wrong
  one parses fine as conf syntax and fails silently at index time.

### Failure modes that no gate catches

The recurring hazard in this codebase is **a change that passes every automated
check and silently ingests nothing**. Three specific ones:

- **`inputHelperModule`** must be declared on every input service in
  `globalConfig.json`. Without it `ucc-gen build` emits a self-contained stub
  that never calls the helper — and `slim validate` and AppInspect both report
  zero errors against that stub. `TA_openrouter/scripts/build.sh` greps the
  built output for the helper reference and fails loudly;
  `test_openrouter_globalconfig.py` guards the source.
- **The Python 3.9 pin.** Splunk's modular-input runtime is CPython 3.9.25.
  `ucc-gen` vendors `package/lib/requirements.txt` wheels with whatever
  `python3` resolves on PATH — often this repo's 3.11 venv. Wrong wheels install
  cleanly and fail only when Splunk imports them.
  `TA_openrouter/scripts/build.sh` pins the interpreter and refuses to build
  without one; set `TA_OPENROUTER_PYTHON39` on any machine lacking
  `/opt/splunk/bin/python3.9`.
- **A dashboard or saved search querying a field that never exists.** It parses,
  uses the index macro correctly, sums a real metric, and returns nothing. In
  `TA_openrouter`, `api_key_id` carries the key's **name**, never its `hash` —
  every shipped search joins on name. `test_openrouter_dashboards.py`
  field-checks every panel query against what the transforms actually emit.

`ucc-gen build` also **rewrites `globalConfig.json` in place**.
`TA_openrouter/scripts/build.sh` snapshots and restores it via an `EXIT` trap;
`scripts/build.sh` (TA_anthropic) does not. Never run `git checkout` on a
`globalConfig.json` after a build — that discards exactly the uncommitted edits
the restore mechanism exists to protect.

### Tests are the gate for the non-Python surface

`.conf` files, dashboard XML, `globalConfig.json`, and even README prose are
pinned by tests, because `slim validate` and AppInspect don't check any of what
matters here. `test_openrouter_readme.py` exists specifically because this
documentation set has already shipped four false claims. When changing a conf,
a dashboard, or documented build steps, expect to update a test — and treat a
failure there as a real defect rather than a stale assertion.

## Conventions

- **Checkpoints live in KV Store** (`TA_anthropic_checkpoints`,
  `TA_openrouter_checkpoints`), *not* under
  `$SPLUNK_HOME/var/lib/splunk/modinputs/`. Deleting files there does nothing.
- **Build scripts use explicit `.venv/bin/*` paths**, never `source
  .venv/bin/activate`, so they behave identically in interactive shells, CI, and
  non-interactive agent shells. Match that in new scripts.
- **`demo/demo_prompt.md` is final — do not edit.** The conference deck quotes
  it verbatim.
- **Mock servers freeze their time anchor at import.** Restarting a mock and
  immediately re-running an input re-anchors fixtures to the new boot hour and
  double-ingests. See `demo/runbook.md` → *Mock restart — the double-ingest
  trap*.
- **Secrets** live in `splunk/.env*` (git-ignored except `.env.example`).
  `mockserver/fixtures/raw_*` and `dist/*.tar.gz` are also git-ignored, so a
  fresh clone has no `dist/` until you build.
- **Fixtures are sanitized, not raw.** Recorded API responses had their
  account identifiers replaced with synthetic values before publication;
  `TA_openrouter/docs/schema-verification.md` records exactly which fields
  were observed live, which were authored, and what was replaced. Never
  commit a raw recording over them.

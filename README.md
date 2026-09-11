# Agentic Data Ingestion — Splunk .conf2026, DEV1194

Materials for `.conf2026` session **DEV1194 — Agentic Data Ingestion: Building
Splunk Add-Ons with Anthropic's Claude**.

The session's claim is that a Splunk Technology Add-on is a tractable target for
an AI coding agent, and that the agent is the constant while the vendor API is
the variable. This repository is the evidence: **two** complete UCC-framework
add-ons against two unrelated AI-provider APIs, built the same way, plus the
prompts, specs and plans that produced them.

| | |
| --- | --- |
| [`TA_anthropic`](TA_anthropic/README.md) | Ingests the Anthropic Admin API — token usage, cost, and the org's API-key and user directory |
| [`TA_openrouter`](TA_openrouter/README.md) | Ingests the OpenRouter management API — hourly analytics by key/model/provider, plus a key-inventory snapshot |

Both are independent deliverables sharing one `.venv` and one
`requirements-dev.txt` at the repo root. **202 tests** cover them (58 + 144) and
run in about five seconds with no Splunk installed.

## Quick start

```bash
python3 -m venv .venv                          # verified on Python 3.11
.venv/bin/pip install -r requirements-dev.txt

.venv/bin/pytest -q tests TA_openrouter/tests  # 202 tests, ~5s, no Splunk needed
```

Neither add-on needs a vendor API key to try: both ship an offline FastAPI mock
of their upstream API.

```bash
# Anthropic mock  -> set the account's API base URL to http://127.0.0.1:8081
.venv/bin/python -m uvicorn mockserver.app:app --port 8081

# OpenRouter mock -> set it to http://127.0.0.1:8082/api/v1  (note the path)
cd TA_openrouter && ../.venv/bin/python -m uvicorn openrouter_mockserver.app:app --port 8082
```

To build installable packages you additionally need `slim`, and a **Python 3.9**
interpreter, because Splunk's modular-input runtime is CPython 3.9:

```bash
.venv/bin/pip install splunk-packaging-toolkit

./scripts/build.sh                # TA_anthropic  -> dist/TA_anthropic-0.1.0.tar.gz
TA_openrouter/scripts/build.sh    # TA_openrouter -> dist/TA_openrouter-0.1.0.tar.gz
```

Each add-on's README covers its own build caveats; `TA_openrouter`'s is the more
thoroughly guarded of the two.

## The prompts

- **[`demo/demo_prompt.md`](demo/demo_prompt.md)** — the prompt fed to Claude
  Code live on stage, verbatim. It builds `TA_anthropic` from nothing.
- **[`CLAUDE.md`](CLAUDE.md)** — the project instruction file that keeps an
  agent from breaking this repo's non-obvious invariants. Most of it exists
  because something was broken once.
- **[`docs/superpowers/`](docs/superpowers/README.md)** — the design specs and
  implementation plans the work was actually driven from, published unedited as
  dated records.
- **[`demo/runbook.md`](demo/runbook.md)** — run of show, pre-stage checklist,
  and a failure decision tree.
- **[`docs/BUILD_LOG.md`](docs/BUILD_LOG.md)** — the 46-commit development
  history this repository was published from.

## What is real and what is synthetic

This matters for anyone reading the dashboards or the fixtures, so it is stated
plainly rather than buried.

- **The demo data is synthetic.** Both mock servers replay authored fixtures.
  `TA_anthropic`'s fixtures are entirely invented. `TA_openrouter`'s mix a small
  number of byte-for-byte recorded rows with a larger number of authored ones.
- **Recorded rows had their account identifiers replaced** with synthetic values
  before publication — key hashes, user id, workspace id. The metric figures the
  dashboards and alert thresholds derive from are untouched, which is why
  nothing moved when the identifiers changed.
- **No credential appears anywhere in this repository**, in any file or in its
  history. The OpenRouter `/keys` endpoint never returns a full key, only an
  already-truncated display label.
- `TA_openrouter/docs/schema-verification.md` records, field by field, which
  parts of the API response shapes were **observed live** and which were
  **assumed** from documentation. Treat anything marked ASSUMED accordingly.

## Architecture

Both add-ons use the same four layers, and the layering is what makes them
testable without a Splunk install:

| Layer | Files | Imports Splunk? |
| --- | --- | --- |
| HTTP | `*_client.py` | no |
| Pure logic | `*_transform.py` | no |
| Modular-input entry points | `*_helper.py` | yes, behind `try/except ImportError` |
| Config and search surface | `default/*.conf`, `data/ui/views/*.xml`, `globalConfig.json` | n/a |

Keep the client and transform free of Splunk imports and of any clock the caller
did not pass in. That property, plus the helpers' guarded imports, is why the
whole suite runs in seconds with nothing installed.

The two add-ons differ in ways that deliberately **do not** generalize — auth
header, money units, three different pagination schemes, two different timestamp
formats. `CLAUDE.md` lists them, because assuming one add-on's convention holds
in the other is the most productive way to break either.

The recurring hazard in this kind of add-on is **a change that passes every
automated check and silently ingests nothing**: a missing `inputHelperModule`
that makes `ucc-gen` emit a stub which `slim validate` and AppInspect both bless,
wheels vendored by the wrong interpreter, or a dashboard querying a field that
never exists. The test suites exist mostly to catch those, and
`TA_openrouter/docs/architecture.md` explains each one.

## Layout

```
TA_anthropic/          the Anthropic add-on (package/, globalConfig.json, README)
TA_openrouter/         the OpenRouter add-on (+ docs/, tests/, its own mock)
mockserver/            offline FastAPI replay of the Anthropic Admin API
tests/                 TA_anthropic + Anthropic mock tests (58)
demo/                  the on-stage prompt and the run-of-show runbook
docs/superpowers/      design specs and implementation plans
scripts/               TA_anthropic build, fixture recorder, preflight checks
splunk/.env.example    credential template; splunk/.env* is git-ignored
```

## Status and support

Version 0.1.0, published as a worked example. Not a supported product, not on
Splunkbase, and carrying no warranty — see [`LICENSE`](LICENSE).

## License

Apache License 2.0. Copyright 2026 bitsIO Inc. See [`LICENSE`](LICENSE) and
[`NOTICE`](NOTICE).

Anthropic, Claude, and OpenRouter are trademarks of their respective owners and
are named here for identification only. Neither add-on is affiliated with,
endorsed by, or supported by Anthropic PBC, OpenRouter, or Splunk LLC.

# DEV1194 Use-Case Design — Agentic Data Ingestion: Building Splunk Add-Ons with Anthropic's Claude

**Date:** 2026-07-30
**Session:** Splunk .conf2026, DEV1194, 30-minute technical breakout
**Abstract (locked):** Live demo showing Claude turning raw vendor API docs into a secure, production-ready Splunk Technical Add-on (TA), reducing TA development from days to minutes.

## Decisions Made

| Decision | Choice |
| --- | --- |
| Use-case domain | AI observability & AI security |
| Target API | Anthropic Admin API (Usage & Cost reports + org enrichment) |
| Cost constraint | $0 — Admin API is free to call; demo data is the speaker's own Claude Code usage |
| Demo vehicle | Claude Code live on stage, "cooking show" format with pre-baked checkpoints |
| TA architecture | Splunk UCC framework (`ucc-gen`) — setup UI, encrypted credentials, AppInspect-passable |
| Packaging | Splunk Packaging Toolkit (SLIM) 1.2.8, provided in-repo at `splunk_packaging_toolkit-1.2.8/` — `slim validate` + `slim package` produce the distributable |
| Final payoff | AI usage dashboard + one alert firing live |
| Fallback plan | First-class deliverable: built, tested, and rehearsed (see Section 5) |

## 1. The Hook

**"Watch Claude build the Splunk add-on that monitors Claude."**

Every organization is rolling out AI, and almost none can answer "who's using it, what does it cost, and is anything anomalous happening?" from Splunk. There is no official Anthropic TA on Splunkbase — a genuine coverage gap. The session fills that gap live, using the AI itself as the developer. The self-referential loop (Claude ingesting its own telemetry) is the memorable core of the talk.

## 2. What Gets Built

**"Anthropic Add-on for Splunk"** — a UCC-framework TA ingesting the Anthropic Admin API:

- **Usage Report** (`GET /v1/organizations/usage_report/messages`): time-bucketed input/output/cache token counts, grouped by model, workspace, and API key.
- **Cost Report** (`GET /v1/organizations/cost_report`): daily USD spend by workspace.
- **API keys and users endpoints**: pulled as enrichment lookups so dashboards show human-readable names instead of IDs.

Claude Code writes, during/around the demo:

- The Python REST client for the Admin API (auth header `x-api-key` with an Admin key, `anthropic-version` header, pagination).
- A **checkpointed modular input** (solnlib checkpointer stores the last completed bucket timestamp — re-runs never duplicate events).
- **Encrypted credential storage** via the UCC setup page (`storage/passwords`) — the "secure" claim in the abstract, demonstrated on screen.
- Sourcetypes `anthropic:usage` and `anthropic:cost`; props for timestamping and JSON field extraction.
- **Configurable API base URL** in the TA setup. This is a legitimate production feature (testing/proxies) and doubles as stage insurance: the same TA runs against the real API or the local mock (Section 5) with one config change.
- **Packaging step**: the finished TA is validated and packaged with the Splunk Packaging Toolkit (`slim validate`, then `slim package`) — shown on stage as the "ready to distribute" beat. SLIM 1.2.8 lives in the repo; note it requires Python ≥3.5.1 and <3.14.

First run backfills ~30 days of history so the dashboard is instantly rich.

## 3. The Payoff Dashboard

**"AI Observability & Security"** dashboard plus one alert, both packaged inside the TA itself (no separate app to install — one artifact on stage):

- Token usage over time by model; cost trend by workspace; top API keys by volume.
- **Cache hit ratio** panel (cache-read vs fresh input tokens) — a genuine cost-optimization insight.
- **Security beat:** alert for "unrecognized API key observed" (a key ID absent from the baseline lookup) — the shadow-AI detection moment. Fires live on stage using a planted fixture event. Backup alert: token-spike anomaly over a rolling baseline.

## 4. Run of Show (30 minutes)

| Time | Beat |
| --- | --- |
| 0–4 | The gap: AI usage is invisible to SOC/FinOps; no Anthropic TA exists; why TA development is historically slow |
| 4–8 | **Live:** kick off Claude Code with the Admin API docs and the goal; audience watches it plan and scaffold with `ucc-gen` |
| 8–14 | Claude works in background; slides cover architecture: docs → UCC TA → checkpointed ingestion → dashboards |
| 14–20 | **"Baked earlier":** finished TA installed in Splunk — setup page with encrypted key, events in search, dashboard, alert fires |
| 20–22 | Takeaway: QR code to repo (prompts + generated TA + how-to); "days to minutes" recap |
| 22–30 | Q&A |

## 5. Fallback Plan (First-Class Deliverable)

These are built and rehearsed, not hypothetical:

1. **Local Splunk** — Splunk Enterprise in Docker on the presentation laptop, with the finished TA, dashboard, alert, and lookups pre-installed. A saved container image/snapshot restores a known-good state in one command.
2. **Mock Admin API server** — a small local server (FastAPI) replaying **recorded JSON fixtures** captured from the real Admin API ahead of the conference (usage report, cost report, api_keys, users — sanitized). Because the TA's base URL is configurable (Section 2), switching to the mock is a config change, not a code change. The entire ingestion → dashboard → alert pipeline runs fully offline.
3. **Pre-staged build checkpoints** — the Claude Code build committed as sequential git tags (scaffold → client → checkpoint logic → final). If the live agent stalls or goes sideways, jump to any checkpoint and narrate.
4. **Pre-recorded screencast** — full capture of a successful live Claude Code segment, stored locally on the laptop, as the last-resort replacement for the one demo beat that requires internet.
5. **Network fallback** — phone hotspot tested with Claude Code before the session.
6. **Day-of preflight checklist** — verify: Admin API key valid, Docker container healthy, mock server serves all fixtures, hotspot works, screencast file plays, dashboard renders on the projector resolution.

**Decision tree:**

| Failure | Response |
| --- | --- |
| Conference Wi-Fi down | Switch to hotspot; if hotspot fails, play screencast for the live segment |
| Claude Code run stalls/derails | Narrate over it briefly, then jump to next git checkpoint |
| Admin API errors or key revoked | Flip TA base URL to local mock; say so honestly ("recorded responses from the real API") |
| Splunk container broken | Restore known-good snapshot (one command, rehearsed) |

## 6. Success Criteria

- Audience sees a real agentic build start live, a production-grade (AppInspect-passable) TA finish, and real data become a real alert.
- The talk lands "days to minutes" without a single beat depending on live infrastructure.
- Attendees leave with a repo containing the exact prompts, the generated TA, and a reproduction guide.

## Open Items (verify before build)

1. Confirm org-admin access on the Anthropic Console to create an Admin API key (this week). If unavailable, the mock server carries the demo end-to-end.
2. Decide the TA's public name for the repo/Splunkbase (working name: "Anthropic Add-on for Splunk"; check Splunkbase naming/branding rules for third-party names).
3. Optional stretch: Anthropic's Claude Code analytics usage endpoint as a second input (sessions, lines of code written) — very on-theme for a developer audience; include only if the primary scope lands early.

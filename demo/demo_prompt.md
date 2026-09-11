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

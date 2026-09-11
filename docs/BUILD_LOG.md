# Build log

This repository is published with a fresh git history. The development history
contained recorded API fixtures carrying real account identifiers, and those
were removed rather than rewritten. The commit record itself is preserved
below, because how these add-ons were built is the subject of the talk.

**46 commits**, 2026-07-30 to 2026-08-03, written by Claude Code under human review.
34 of the 46 carry a `Co-Authored-By` trailer naming the model:

- 25 commits — `Claude Opus 5 (1M context) <noreply@anthropic.com>`
- 9 commits — `Claude Fable 5 <noreply@anthropic.com>`

The remaining 12 predate that convention in this repo; they were
written the same way.

## Commits, oldest first

| Date | Commit |
| --- | --- |
| 2026-07-30 | Add DEV1194 use-case design: Anthropic Admin API TA built live with Claude Code |
| 2026-07-30 | Clarify dashboard ships inside the TA |
| 2026-07-30 | Add SLIM packaging step to DEV1194 design |
| 2026-07-30 | Add DEV1194 implementation plan: TA + mock + Splunk stack + demo assets |
| 2026-07-30 | Add deck generator: DEV1194 slides from conf26 breakout template |
| 2026-07-30 | Add diagrams, icons and charts to the DEV1194 deck |
| 2026-07-30 | Build Anthropic Add-on for Splunk (TA_anthropic) with mock server and demo assets |
| 2026-07-30 | Polish TA_anthropic: timezone, alert windows, runbook safety, alert regression tests |
| 2026-07-30 | Add handoff document |
| 2026-07-31 | Add TA_openrouter design spec |
| 2026-07-31 | Revise TA_openrouter spec against verified OpenAPI spec |
| 2026-07-31 | Pull provider-routing attribution into TA_openrouter v0.1.0 |
| 2026-07-31 | Add TA_openrouter implementation plan |
| 2026-07-31 | feat(openrouter): scaffold TA and add fixture recorder |
| 2026-07-31 | feat(openrouter): add compute_window with complete-hour and retention clamp |
| 2026-07-31 | fix: add timezone-aware validation to compute_window |
| 2026-07-31 | feat(openrouter): add query-driven row flattening and truncation detection |
| 2026-07-31 | feat(openrouter): add Bearer-auth client with analytics query and offset key paging |
| 2026-07-31 | fix(openrouter): terminate list_keys on empty page, add runaway guard |
| 2026-07-31 | feat(openrouter): add UCC globalConfig with account and two inputs |
| 2026-07-31 | fix(openrouter): remove invalid 'enable' action from inputs table |
| 2026-07-31 | fix(openrouter): parse UTC-naive timestamps and coerce string metrics |
| 2026-07-31 | feat(openrouter): record live API fixtures and enrich for the demo scenario |
| 2026-07-31 | feat(openrouter): add query-driven mock server anchored to the UTC hour |
| 2026-07-31 | fix(openrouter): rename mockserver package to avoid TA_anthropic collision, honor /keys pagination |
| 2026-07-31 | test(openrouter): assert fixtures encode the demo scenario |
| 2026-07-31 | feat(openrouter): add analytics input with per-query checkpoints and truncation guard |
| 2026-07-31 | fix(openrouter): isolate per-query failures in analytics stream_events |
| 2026-07-31 | feat(openrouter): add API key roster snapshot input |
| 2026-07-31 | fix(openrouter): isolate per-input logger-construction failures |
| 2026-07-31 | feat(openrouter): add sourcetypes, index macro, and empty baseline lookup |
| 2026-07-31 | feat(openrouter): add baseline builder and two alerts with derived threshold |
| 2026-08-01 | fix(openrouter): wire input helpers, make checkpoint windows disjoint, warn on truncation |
| 2026-08-01 | fix(openrouter): replicate streamstats sample stdev and growing window |
| 2026-08-01 | feat(openrouter): add model mix, provider routing, and key governance dashboards |
| 2026-08-01 | feat(openrouter): add packaging scaffold and build script |
| 2026-08-03 | docs(openrouter): add README and operator documentation |
| 2026-08-03 | fix(openrouter): guard globalConfig.json against ucc-gen rewrite, validate python3.9 override |
| 2026-08-03 | docs(openrouter): fix two false claims from review round 1 |
| 2026-08-03 | fix(openrouter): correct the truncation WARN's ineffective remedy |
| 2026-08-03 | docs(openrouter): sync truncation runbook with the corrected WARN |
| 2026-08-03 | docs(openrouter): correct stale build advice and the dashboard-join claim |
| 2026-08-03 | fix(openrouter): guard the baseline lookup and reject wedging backfill_days |
| 2026-08-03 | fix(openrouter): stop spike alert re-firing, correct suppression, chart totals, and install docs |
| 2026-08-03 | fix(openrouter): cap analytics row limit at the API maximum of 10000 |
| 2026-08-03 | feat(openrouter): add a time-range picker to all three dashboards |

# DEV1194 Run of Show & Failure Runbook

Demo machine: **local Splunk 10.2.1 at `/opt/splunk`**, mock Admin API on
`127.0.0.1:8081`, Splunk Web on `http://localhost:8000`. Everything runs
natively on this laptop — no container, no remote host, no network needed
after minute 8.

## Run of show (30 min)
| Time | Beat | Machine state |
|---|---|---|
| 0–4 | The gap: AI usage invisible; no Anthropic TA on Splunkbase | Slides |
| 4–8 | LIVE: paste demo/demo_prompt.md into Claude Code | live-build/ dir, network up |
| 8–14 | Slides: architecture (docs → UCC TA → checkpointed ingest → dashboard) | Claude Code running in background |
| 14–20 | Reveal: finished TA in Splunk — setup page, events, dashboard, alert fires | localhost:8000, mock on :8081 |
| 20–22 | QR to repo; "days → minutes" recap | Slides |
| 22–30 | Q&A | — |

## Pre-stage (night before)
1. **Obtain and record the Splunk admin credentials** for this `/opt/splunk`
   instance (username + password). Write them on the same card as the run of
   show. The checkpoint reset below is the only on-stage recovery that needs
   them, and there is no way to do it without them.
2. `./scripts/build.sh` — fresh `dist/TA_anthropic-*.tar.gz`
3. Start the mock: `.venv/bin/uvicorn mockserver.app:app --port 8081 &`
   (fixtures time-shift to "now", so the dashboard is never empty)
4. Install the TA into the local Splunk and restart:
   ```bash
   tar -xzf dist/TA_anthropic-*.tar.gz -C /opt/splunk/etc/apps/
   /opt/splunk/bin/splunk restart
   ```
5. In Splunk Web (http://localhost:8000) → **TA_anthropic → Configuration**:
   create the account with base URL `http://127.0.0.1:8081` and any dummy Admin
   key, then enable **all three inputs** on the **Inputs** tab:

   | Input | Interval |
   |---|---|
   | `anthropic_usage` (Anthropic Usage Report) | 300 |
   | `anthropic_cost` (Anthropic Cost Report) | 3600 |
   | `anthropic_directory` (Anthropic Org Directory) | 3600 |

   **The directory input is not optional.** It is the *only* source of
   `sourcetype=anthropic:api_keys`, which is the *only* input to the
   `[Anthropic - Build API Key Baseline]` saved search that writes the
   `anthropic_api_key_baseline` lookup. Leave it disabled and the baseline is
   empty, so the shadow-AI reveal flags **every** key instead of the one
   planted rogue one — the centrepiece of the talk silently produces the wrong
   answer, and it looks like it worked.

6. Confirm all three sourcetypes land (give the inputs one interval). **Do not
   pin an index.** `inputs.conf` ships `index = default` and the Inputs tab lets
   you send the inputs to any index, so on an instance whose `defaultDatabase`
   is something other than `main` a hard-coded index constraint returns zero
   from a perfectly healthy add-on and you will "fix" inputs that were never
   broken. Search by sourcetype alone (re-add an index term only if you
   deliberately overrode the index on the Inputs tab):
   ```
   sourcetype=anthropic:usage     | head 5
   sourcetype=anthropic:cost      | head 5
   sourcetype=anthropic:api_keys  | stats dc(id)    → 2
   ```
   `dc(id)`, **not** `stats count`. The directory input writes a *complete*
   2-event snapshot on **every** run (interval 3600), so the raw event count is
   `2 × runs` and keeps climbing all night — `stats count → 2` is only true in
   the first hour and would send you off fixing a healthy system. Two *distinct*
   key ids is the assertion that stays true forever. Equivalent if you prefer it:
   `sourcetype=anthropic:api_keys | dedup id | stats count` — also **2**.
7. **Build the baseline by hand — do not wait for the `*/30` cron — and run it
   from inside the TA's own app context.** `outputlookup` resolves its
   destination from the *current* app (`createinapp` defaults to true,
   `create_context` defaults to `app`). Run it from Search & Reporting and it
   writes `/opt/splunk/etc/apps/search/lookups/anthropic_api_key_baseline.csv`,
   while `transforms.conf` inside TA_anthropic keeps resolving to the TA's own
   copy — the search reports success, the lookup you just built is invisible to
   the alert, and the shadow-AI panel stays wrong. Open the TA's search view
   explicitly (the `search` view is exported to system, so this URL works):

   <http://localhost:8000/en-US/app/TA_anthropic/search>

   and run it *there*:
   ```
   | savedsearch "Anthropic - Build API Key Baseline"
   ```
   (Equivalent: **Settings → Searches, reports and alerts**, filter app
   `TA_anthropic`, and hit **Run** on the row — that dispatches in the owning
   app's context.)

   Then verify the lookup it wrote — first through the knowledge object:
   ```
   | inputlookup anthropic_api_key_baseline
   ```
   Expect **exactly two rows**: `apikey_demo_ci` and `apikey_demo_claudecode`.

   Then confirm on disk that the file that changed is the **TA's** copy:
   ```bash
   ls -l /opt/splunk/etc/apps/TA_anthropic/lookups/anthropic_api_key_baseline.csv
   cat   /opt/splunk/etc/apps/TA_anthropic/lookups/anthropic_api_key_baseline.csv
   # mtime must be seconds old and the body must hold those two ids.

   # This one must NOT exist. If it does, you ran the search in the wrong app —
   # delete it and re-run from the TA_anthropic URL above.
   ls -l /opt/splunk/etc/apps/search/lookups/anthropic_api_key_baseline.csv
   ```
   If the lookup is empty, the directory input has not run yet — re-run it from
   the Inputs tab (disable/enable), confirm `sourcetype=anthropic:api_keys |
   stats dc(id)` returns **2** (again: `dc(id)`, not `count` — the count is
   `2 × runs`), then re-run the `savedsearch` above from the TA app URL.
8. Open the dashboard and confirm the **Unrecognized API Keys (shadow AI)**
   panel shows **exactly one row — `apikey_rogue_demo`**. One row = the demo is
   armed. Zero rows or three rows = stop and fix now, not on stage. Also check
   the dashboard renders at projector resolution.
9. `scripts/preflight.sh` — everything green except the optional Admin API check.
10. Charge laptop; copy the backup screencast into `demo/backup_screencast.mp4`.

## Failure decision tree
| Failure | Response |
|---|---|
| Conference Wi-Fi down | Phone hotspot. Still down → play `demo/backup_screencast.*` for minutes 4–8; everything else (Splunk + mock) is local and needs no network |
| Claude Code stalls/derails | Narrate briefly, `git checkout demo-<next-tag>` in live-build, continue story |
| Real Admin API errors / key revoked | TA account already points at the mock — nothing to do; say "recorded responses from the real API" |
| Splunk broken / weird UI state | `/opt/splunk/bin/splunk restart` (~60–90 s). Still broken → `/opt/splunk/bin/splunk stop; /opt/splunk/bin/splunk start`, then re-run `scripts/preflight.sh` |
| TA not showing in the app menu | Confirm `ls /opt/splunk/etc/apps/TA_anthropic`, re-extract from `dist/`, `/opt/splunk/bin/splunk restart` |
| No new events / inputs not firing | **Usually correct behaviour, not a fault** — see "Checkpoint reset" below before touching anything. Check `/opt/splunk/var/log/splunk/ta_anthropic*.log` for a real error first |
| Mock server died | `.venv/bin/uvicorn mockserver.app:app --port 8081` (starts in ~2 s) — then **stop**. Do **not** restart-and-immediately-re-run: a restart re-anchors the fixture to the new boot hour, so the same buckets come back at *later* timestamps and get indexed on top of the copies already there — on screen that is `apikey_rogue_demo`'s totals jumping, a **second plateau** on the usage chart, and a lifted spike-alert baseline that can stop the alert firing. Mid-demo: leave the checkpoint alone and **wait for the next natural interval** (usage = 300 s). Off-stage only: reset the checkpoint *and* accept a full re-ingest. See "Mock restart — the double-ingest trap" below |
| Port 8000/8081 already taken | `lsof -ti :8000` / `lsof -ti :8081` and kill the stale process; Splunk Web port is `/opt/splunk/etc/system/local/web.conf` |

## Mock restart — the double-ingest trap

The mock freezes its time anchor **once, at import** (`REPORTS = _anchor_reports()`).
That is what makes repeated polls byte-identical and lets the checkpoint suppress
re-ingestion — but it also means **a restart re-anchors everything**: the newest
usage bucket is re-stamped to end at the top of the hour the *new* process booted
in, and every other bucket slides forward with it. The KV Store checkpoint still
holds the *old* boundary, so any bucket that has now slid past it looks brand new,
sails through the `starting_at`/`ending_at` filter, and is indexed a second time —
same numbers, later `_time`.

**How to recognise it on screen:** the usage chart grows a *second plateau* of the
same shape shifted to the right, and `apikey_rogue_demo`'s token totals step up
(the more hours the restart crossed, the more buckets re-land; a full checkpoint
reset after a restart re-lands *all* of them and cleanly doubles the totals).
Worse, the extra plateau raises the 6-hour rolling mean that
`[Anthropic - Token Spike Anomaly]` compares against, so the alert may quietly
**stop firing**.

Pick one, deliberately:

| Situation | Do this |
|---|---|
| **Mid-demo (default)** | Restart the mock and **do nothing else**. Leave the checkpoint alone and let the input fire on its own next interval (usage 300 s). At worst one extra hourly bucket lands; if the restart stayed inside the same clock hour the anchor is unchanged and nothing lands at all. Cheapest and least visible. |
| **Restart crossed several hours and the chart already looks wrong** | Narrate it ("the recorder re-anchored") and keep going, or switch to the backup screencast. Do not try to un-ingest on stage. |
| **Off-stage / between sessions, you want a clean slate** | Reset the checkpoint (next section) **and accept a full re-ingest** — every bucket returns at the new anchor on top of the old set. Only worth it if you can also clear the old events first — point the inputs at a scratch index, or use the `delete` command with a role that holds `can_delete`. |

The one thing never to do is restart the mock and immediately disable/enable the
input "to refresh it". That is the exact sequence that produces the doubled
totals and the disarmed spike alert.

## "No new events" — diagnosis, then checkpoint reset

**Step 1 — expect it.** Re-running the usage input inside the *same clock hour*
is supposed to produce nothing. `compute_window` only releases **completed**
buckets: it snaps `ending_at` down to the last whole hour (whole day for cost)
and returns `None` when the checkpoint has already reached it. So "I re-ran the
input and got no new events" is the add-on working correctly — that is the
no-duplicates guarantee the talk is claiming. Don't debug it, and don't reset a
checkpoint to make an event appear. Say the line and move on.

**Step 2 — look for a real error.** `/opt/splunk/var/log/splunk/ta_anthropic*.log`.
A genuine failure logs an exception; a no-op logs the skipped-run line.

**Step 3 — only then, reset the checkpoint.** Checkpoints are in the **KV
store**, not on disk. Nothing under `/opt/splunk/var/lib/splunk/modinputs/`
belongs to this add-on — deleting there is a no-op and pure stage time burned.
The add-on uses solnlib's `KVStoreCheckpointer` with collection
`TA_anthropic_checkpoints`.

Inspect (search bar, no credentials needed):

```
| rest /servicesNS/nobody/TA_anthropic/storage/collections/data/TA_anthropic_checkpoints
```

Clear it — **needs the admin credentials from pre-stage step 1** — which forces
a full backfill on the next input run:

```bash
curl -sk -u admin:<password> -X DELETE \
  https://localhost:8089/servicesNS/nobody/TA_anthropic/storage/collections/data/TA_anthropic_checkpoints
```

Then disable/enable the input from the Inputs tab to trigger it immediately.

Note: the collection is created by solnlib on the input's first run, so both
commands return nothing (or 404) until an input has run at least once — which
is itself the answer if you are seeing no events at all.

## Checkpoint tags (in this repo)
- `demo-scaffold` — after Task 6 (ucc-gen scaffold + config)
- `demo-client` — after Tasks 4–5 (client + transform, tests green)
- `demo-inputs` — after Task 7 (working inputs)
- `demo-final` — after Task 10 (end-to-end verified)

Tags are created centrally once the corresponding commits exist:
```bash
git log --oneline            # identify the commits from Tasks 5, 6, 7, 10
git tag demo-client   <commit-of-task-5>
git tag demo-scaffold <commit-of-task-6>
git tag demo-inputs   <commit-of-task-7>
git tag demo-final    <commit-of-task-10>
```

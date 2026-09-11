# DEV1194 deck — conference-ready redesign

**Date:** 2026-08-04
**Status:** Approved
**Supersedes deck content in:** `docs/superpowers/specs/2026-07-30-conf26-dev1194-usecase-design.md`
**Session:** `.conf2026` DEV1194, September 2026, 30-minute breakout

---

## Why this redesign

The deck (now at `deck/conf26_DEV1194_Presentation_v1.pptx`, renamed
2026-08-05 to the conf26-mandated filename) was generated on
2026-07-30 and tells a single-vendor story: Claude Code builds `TA_anthropic`
against the Anthropic Admin API, revealed on mock data.

Two things changed after it was built.

1. **The Anthropic live path was dropped, not deferred.** The Console account is
   an individual org and the Admin API is unavailable to individual accounts.
   The demo runs on the offline mock server permanently.
2. **A second add-on exists.** `TA_openrouter` was built between 2026-07-31 and
   2026-08-03 — three dashboards, two alerts, packaged, installed, and running
   against a *real* OpenRouter management key.

The deck knows about none of this. It describes one add-on and one API when the
repo now holds two of each, and the second one is the only one with live
credentials.

## Goal

One deck that tells a two-vendor story: **Claude Code is the constant, the
vendor API is the variable.** Build one add-on live on stage, then show a second
one already built the same way — against a real account, with real spend.

Conference-ready means: no unfilled placeholders that require the speaker to
remember something, every screenshot present, and the deck reproducible from
`scripts/build_deck.py` so a late copy edit does not mean re-pasting images.

## Decisions

Settled during brainstorming; not open questions.

| Decision | Choice |
| --- | --- |
| Deck story | **Both** vendors — Anthropic on mock data, OpenRouter on real data |
| On-stage flow | Build `TA_anthropic` **live**; reveal `TA_openrouter` as already-built proof |
| Which is live | The risky live build sits on the safe mock path; real data is the closing payoff |
| OpenRouter analytics data | Generate real traffic via a trickle script, screenshot after |
| Anthropic data | Populate from the mock server now; capture screenshots now |
| `demo/demo_prompt.md` | **Not edited.** The deck quotes it verbatim |
| Add-on code | **Not changed.** This is a deck and demo-prep task only |

## The data situation, measured

Probed on 2026-08-04 against the live API and the running Splunk instance. This
is the fact that shaped the plan, so it is recorded rather than assumed.

| Source | Reality |
| --- | --- |
| Anthropic, in Splunk | **0 events.** Mock and fixtures work; nothing was ever ingested |
| OpenRouter key roster | **Real and presentable.** 2 keys, one at $20.08 of a $20 limit, `Alpaca` at $0.01 of $25. *(Superseded — see the amendment below.)* |
| OpenRouter analytics | **1 row across a full 30-day query.** One hour bucket, one key, one model, $0.0129, 3 requests |

That key's $20 was spent before the analytics retention window, which is why
the roster showed real money and the analytics did not. Model Mix, Provider
Routing, and spend-over-time each rendered as a single bar.

**Amendment, 2026-08-05.** Two things changed after this was written. First, all
three OpenRouter dashboards are now captured from the **offline mock**, not the
live account — the live analytics was too thin to screenshot, so the fixtures
supply the scenario and slide 20 states that on the slide. Second, the recorded
key named above was **deleted from the fixtures entirely**: it carried a real
64-hex hash, a personal key name, and real lifetime spend, none of which
belongs on a projected slide. The roster now holds `Alpaca` (recorded) and
`Nimbus_Prod_Key` (authored), and the slide's budget callout cites Nimbus's
synthetic $128.45-of-$150 instead. `Alpaca`'s own real identifiers remain in
the fixtures and in the screenshot — flagged in `HANDOFF.md`, not yet decided.

**Consequence:** "OpenRouter has real data" is true of the roster and the
governance story, and not yet true of the charts. The trickle script closes that
gap; until it has run, those three panels stay drop-zones.

## Deck structure

23 slides. New slides are marked with a plus.

| # | Slide | Treatment |
| --- | --- | --- |
| 1 | Title | Unchanged |
| 2 | Forward-looking statements | Mandatory template slide, kept |
| 3 | whoami | Done 2026-08-05: two speakers, photo-left / bio-right, from the `SPEAKERS` tuple |
| 4 | Agenda | Rewritten for the two-vendor arc |
| 5 | AI adoption is invisible to Splunk | Unchanged |
| 6 | Why add-ons take days | Unchanged |
| 7 | The gap | Widened: Splunkbase has nothing for **either** vendor |
| 8 | Segue — build it live | Unchanged |
| 9 | The one-prompt spec | Unchanged, quotes `demo_prompt.md` |
| 10 | What Claude Code is doing | Unchanged |
| 11 | Anatomy of the add-on | Unchanged |
| 12 | Three endpoints | Unchanged |
| 13 | Production-ready is not a vibe | Unchanged |
| 14 | Segue — the reveal | Unchanged |
| 15 | AI Observability dashboard | Screenshot replaces drop-zone |
| 16 | Shadow AI: unknown key | Screenshot of the fired alert beside the chart |
| +17 | Segue — same method, second vendor | New |
| +18 | OpenRouter is a different API in every way that matters | New |
| +19 | OpenRouter dashboards — real account | Screenshots, three dashboards |
| 20 | Days to minutes | Unchanged |
| 21 | Takeaways | Gains the transfer-proof line |
| 22 | Resources | Covers both add-ons; QR drop-zone stays |
| 23 | Thank you | Unchanged |

### Slide 18 — the substance

The new technical slide. Its job is to show that the second build was not a
copy-paste of the first, and that none of the differences were hand-discovered.

Four contrasts, drawn as a two-column comparison:

| | Anthropic Admin API | OpenRouter management API |
| --- | --- | --- |
| Auth | `x-api-key` + `anthropic-version` | `Authorization: Bearer` |
| Shape | `GET` report endpoints | `POST /analytics/query` with a body |
| Pagination | `has_more` to `next_page` / `last_id` | offset, to exhaustion |
| The trap | Cost amounts arrive in **cents** | Analytics identifies a key by **name**, never by hash |

Each trap is a thing a human skims past and an agent reading the actual spec
does not. That is the argument of the talk in one slide.

## Screenshot pipeline

The deck is generated: `scripts/build_deck.py` copies the 40 MB template and
rebuilds every slide, so an image pasted into the `.pptx` by hand is lost on the
next run. Screenshots therefore become build inputs.

- PNGs live in `deck/assets/`, referenced by a stable filename.
- `build_deck.py` places each one into its slot, scaled to fit and centred.
- **A missing file degrades to today's dashed drop-zone with its caption.** The
  build never fails because a screenshot has not been captured yet, and an
  unfilled slot still reads as intentional in review.

Asset names:

| File | Slide |
| --- | --- |
| `headshot_alexey.jpg`, `headshot_teja.jpg` | 3 |
| `anthropic_dashboard.png` | 15 |
| `anthropic_alert.png` | 16 |
| `openrouter_key_governance.png` | 19 |
| `openrouter_model_mix.png` | 19 |
| `openrouter_provider_routing.png` | 19 |
| `resources_qr.png` | 24 (generated by `scripts/make_qr.py`) |

## Demo-data preparation

Populating Anthropic is also the demo-day prep already listed in `HANDOFF.md`
section 2, so it is not throwaway work.

1. Start the mock server on `127.0.0.1:8081`.
2. Point the `admin` account's `api_base_url` at it.
3. Raise `backfill_days` on `Test_UsageReport` from 1 to 7 — the token-spike
   alert reasons over `-48h` with `streamstats window=6`, and one day of history
   cannot populate that reference set.
4. Restart splunkd. This both registers `anthropic_directory://Test_Directory`
   (added after the last restart, so it has never fired) and makes all three
   inputs run immediately rather than waiting out their intervals.
5. Run **Build API Key Baseline** from inside the `TA_anthropic` app —
   `outputlookup` writes to whichever app the search runs in.
6. Verify: four sourcetypes have events, `| inputlookup
   anthropic_api_key_baseline` returns exactly two rows, and the shadow-AI
   search isolates `apikey_rogue_demo` alone.

**Do not restart the mock and immediately re-run an input.** The mock freezes
its time anchor at import while the KV Store checkpoint does not, so a restart
mints a second set of buckets at new timestamps that slip past the checkpoint —
the rogue key's totals visibly double.

## OpenRouter trickle traffic

A script that makes real inference calls through the OpenRouter API so the
analytics dashboards acquire a genuine time axis.

- Calls spread across **several models and providers**, on **both** existing
  keys, so Model Mix and Provider Routing have more than one series.
- Spread over **24 to 48 hours**, because analytics granularity is hourly and
  every call made in one sitting lands in a single bucket.
- Bounded spend, with the ceiling stated in the script and echoed as it runs.
- Run by the user, not by this session — it needs to span real wall-clock time.

The dashboards are captured after it completes. Until then the three OpenRouter
panels on slide 19 stay drop-zones, which the asset pipeline handles.

## Out of scope

- Any change to `TA_anthropic` or `TA_openrouter` add-on code.
- Editing `demo/demo_prompt.md`.
- The speaker's job title and social handle, and the QR code — both need
  information this session does not have (the second needs a public repo).
- The backup screencast. Still an open item in `HANDOFF.md`.

## Success criteria

1. The deck rebuilds from `scripts/build_deck.py` with no manual steps.
2. Every screenshot slot either holds an image or a captioned drop-zone.
3. No slide describes an API path that cannot be demonstrated.
4. The Anthropic mock demo is verified end to end in the running Splunk
   instance, not just assumed to work.
5. `HANDOFF.md` reflects the two-vendor story and the closed Anthropic blocker.

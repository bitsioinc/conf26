"""Local replay of the OpenRouter management API for offline development.

Two deliberate properties, carried over from the plan:

1. **Anchored to the current UTC hour, not to import time.** Fixture buckets
   are shifted so the newest lands on the hour the process started in.
   Because the anchor is an hour boundary rather than an import instant,
   restarting the mock inside the same hour regenerates identical buckets --
   so a restart cannot mint a second set of timestamps that slip past a
   consumer's checkpoint. (TA_anthropic's mock anchors at import and has
   exactly that hazard -- see the "Mock restart"
   section of demo/runbook.md.)

2. **Responses are query-driven.** The real endpoint's row shape depends on
   the requested dimensions, so the mock selects a fixture per dimension set
   and honours time_range and limit. Requesting a low limit sets
   metadata.truncated, which is how the truncation path gets tested.

Two more properties were fixed against the *real*, live-recorded fixtures
rather than the plan that predated them -- see docs/schema-verification.md
for the full accounting:

3. **The time-bucket column is not a single constant.** The plan assumed one
   column named ``date``; that was falsified. The two analytics fixtures use
   different bucket columns for what is conceptually the same value --
   ``date__hour`` for ``[api_key_id, model]`` queries, ``created_at__hour``
   for ``[model, provider]`` queries. FIXTURE_TIME_FIELDS carries the right
   column alongside each fixture; there is no shared TIME_FIELD.

4. **Timestamps are parsed and re-emitted in the format the real API uses.**
   Fixture rows are space-separated and offset-free
   (``"2026-07-31 18:00:00"``), not RFC 3339. Request ``time_range`` values
   arrive as RFC 3339 (``"2026-07-31T10:00:00Z"``) because that is what
   ``openrouter_transform.compute_window`` sends. ``_parse`` accepts both
   shapes and attaches UTC to whichever one arrives naive -- mirroring
   ``openrouter_transform._parse``'s own convention -- so every datetime
   this module touches ends up timezone-aware and comparisons in ``_anchor``
   and the time_range filter never raise TypeError. Bucket values written
   back into a response are always re-emitted via ``_fmt`` in the observed
   space-separated shape, never rewritten to RFC 3339.

Real responses also carry no ``warnings`` key (VERIFIED absent -- see
docs/schema-verification.md); this mock does not synthesize one.

5. **``/keys`` honours ``offset`` and ``include_disabled``.**
   ``OpenRouterClient.list_keys`` pages by advancing ``offset`` until it
   receives an *empty* page -- not a short one -- and gives up after
   ``KEYS_MAX_PAGES`` with a loud error if that never happens. A mock that
   always returns the full roster regardless of ``offset`` therefore never
   emits an empty page and drives the real client straight into that error.
   This mock slices the roster at ``offset`` and returns everything from
   there to the end in one page (page size is unverified against the live
   API -- see docs/schema-verification.md -- so there is no basis to invent
   a smaller one); requesting past the end returns ``{"data": []}``, which
   is what actually terminates the client's loop. ``include_disabled=false``
   filters out disabled keys.
"""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

FIXTURES = Path(__file__).parent / "fixtures"
app = FastAPI(title="OpenRouter management API (mock)")

BY_KEY_MODEL = "analytics_by_key_model.json"
BY_MODEL_PROVIDER = "analytics_by_model_provider.json"
KEYS = "keys.json"

#: Verified against a live collection run: the real API rejects any
#: analytics `limit` above this with a 400. openrouter_client.DEFAULT_ROW_LIMIT
#: must never exceed it; this mock enforces the same ceiling so a client
#: that regresses past it fails tests instead of only failing in production.
MAX_ANALYTICS_LIMIT = 10000

#: Real bucket column name differs per fixture -- see docstring point 3.
FIXTURE_TIME_FIELDS = {
    BY_KEY_MODEL: "date__hour",
    BY_MODEL_PROVIDER: "created_at__hour",
}

#: Observed wire format for analytics bucket timestamps: space-separated,
#: no UTC offset. NOT RFC 3339 -- see docstring point 4.
FIXTURE_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _load(name):
    return json.loads((FIXTURES / name).read_text())


def _parse(ts):
    """Parse a timestamp, attaching UTC when it carries no offset.

    Handles both RFC 3339 request-side strings (``2026-07-31T18:00:00Z``)
    and the space-separated, offset-free shape real fixture rows use
    (``2026-07-31 18:00:00``). Mirrors openrouter_transform._parse's
    convention: a naive result gets UTC attached, an already-aware one is
    left exactly as parsed. Every datetime this module produces is
    therefore timezone-aware, so _anchor and the time_range filter can
    compare them freely without TypeError.
    """
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt(dt):
    """Re-emit in the observed fixture shape -- never RFC 3339."""
    return dt.strftime(FIXTURE_TS_FORMAT)


def _rows(payload):
    return payload["data"]["data"]


def _anchor(payload, time_field):
    """Shift buckets so the newest lands on the current UTC hour."""
    rows = _rows(payload)
    if not rows:
        return payload
    newest = max(_parse(r[time_field]) for r in rows)
    top_of_hour = datetime.now(timezone.utc).replace(
        minute=0, second=0, microsecond=0)
    delta = top_of_hour - newest
    shifted = copy.deepcopy(payload)
    for row in _rows(shifted):
        row[time_field] = _fmt(_parse(row[time_field]) + delta)
    return shifted


#: Frozen at import, but anchored to an hour boundary -- see the docstring.
ANALYTICS = {
    name: _anchor(_load(name), FIXTURE_TIME_FIELDS[name])
    for name in (BY_KEY_MODEL, BY_MODEL_PROVIDER)
}
KEY_ROSTER = _load(KEYS)


def _require_auth(authorization):
    if not authorization:
        raise HTTPException(status_code=401,
                            detail={"error": "missing Authorization header"})


def _select_fixture(dimensions):
    """Real row shape depends on requested dimensions -- see docstring point 2."""
    return BY_MODEL_PROVIDER if "provider" in dimensions else BY_KEY_MODEL


@app.post("/api/v1/analytics/query")
async def analytics_query(request: Request,
                          authorization: str = Header(default=None)):
    _require_auth(authorization)
    body = await request.json()

    limit = int(body.get("limit") or 1000)
    if limit > MAX_ANALYTICS_LIMIT:
        # Same shape as the live API's 400 (top-level "error", no "detail"
        # wrapper) -- returned directly rather than via HTTPException so the
        # body isn't nested under FastAPI's default "detail" key.
        return JSONResponse(
            status_code=400,
            content={"error": {
                "message": "limit: Too big: expected number to be <={}".format(
                    MAX_ANALYTICS_LIMIT),
                "code": 400,
            }},
        )

    dimensions = body.get("dimensions") or []
    fixture = _select_fixture(dimensions)
    time_field = FIXTURE_TIME_FIELDS[fixture]

    window = body.get("time_range") or {}
    start = _parse(window["start"]) if window.get("start") else None
    end = _parse(window["end"]) if window.get("end") else None

    rows = []
    for row in _rows(ANALYTICS[fixture]):
        stamp = _parse(row[time_field])
        if start is not None and stamp < start:
            continue
        if end is not None and stamp >= end:
            continue
        rows.append(dict(row))

    truncated = len(rows) > limit
    rows = rows[:limit]

    return {
        "data": {
            "data": rows,
            "metadata": {"query_time_ms": 1, "row_count": len(rows),
                         "truncated": truncated},
        }
    }


@app.get("/api/v1/keys")
def list_keys(authorization: str = Header(default=None),
              offset: int = 0, include_disabled: bool = True):
    """Paginate by ``offset``, terminating on an empty page -- see docstring
    point 5. ``offset`` is applied after the ``include_disabled`` filter, so
    a client that never sends ``include_disabled=true`` still advances
    correctly past a roster containing disabled keys."""
    _require_auth(authorization)
    keys = KEY_ROSTER["data"]
    if not include_disabled:
        keys = [key for key in keys if not key.get("disabled")]
    return {"data": keys[offset:]}

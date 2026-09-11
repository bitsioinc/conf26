"""Local replay of the Anthropic Admin API for offline demos.

Two properties matter for the demo and both are deliberate:

1. **The re-anchor happens exactly once, at import time.**  The recorded
   fixtures are stamped with fixed 2026-07-28 timestamps; on startup every
   report bucket is shifted so the newest *usage* bucket ends at the top of
   the hour the server booted in, and the newest *cost* bucket ends at that
   day's midnight (a whole number of days, so daily buckets stay
   midnight-aligned like the real API's).  The shift is a process-lifetime
   constant, so repeated polls get byte-identical timestamps and the add-on's
   KV-store checkpoint can actually suppress re-ingestion.  Re-computing the
   anchor per request would re-stamp the same three buckets to "now" on every
   poll, multiplying the rogue key's token total by the number of polls.

2. **Report endpoints honour ``starting_at`` / ``ending_at``.**  Like the real
   Admin API, only buckets that fall entirely inside the requested window come
   back.  Together with (1) this means the second and later polls of an
   unchanged window return zero buckets instead of duplicates.

``has_more`` / ``next_page`` are always echoed from the fixture, never
recomputed from the filtered bucket list: a window can legitimately empty out
page 1 while page 2 still has matching buckets, and collapsing ``has_more``
there would make the client stop early and lose data.
"""
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query

FIXTURES = Path(__file__).parent / "fixtures"
app = FastAPI(title="Anthropic Admin API (mock)")

USAGE_PAGE_1 = "usage_report_page1.json"
USAGE_PAGE_2 = "usage_report_page2.json"
COST_REPORT = "cost_report.json"
API_KEYS = "api_keys.json"
USERS = "users.json"

RFC3339 = "%Y-%m-%dT%H:%M:%SZ"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fmt(dt: datetime) -> str:
    return dt.strftime(RFC3339)


def _newest_ending(*reports) -> datetime:
    return max(_parse(b["ending_at"]) for r in reports for b in r["data"])


def _shift(report: dict, delta) -> dict:
    """Return a copy of ``report`` with every bucket boundary moved by delta."""
    shifted = copy.deepcopy(report)
    for bucket in shifted["data"]:
        bucket["starting_at"] = _fmt(_parse(bucket["starting_at"]) + delta)
        bucket["ending_at"] = _fmt(_parse(bucket["ending_at"]) + delta)
    return shifted


def _anchor_reports() -> dict:
    """Re-anchor the recorded reports into the recent past. Called ONCE."""
    now = datetime.now(timezone.utc)
    top_of_hour = now.replace(minute=0, second=0, microsecond=0)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)

    page1, page2 = _load(USAGE_PAGE_1), _load(USAGE_PAGE_2)
    cost = _load(COST_REPORT)

    # Hourly usage buckets land in the last few hours; daily cost buckets land
    # on the last few midnights. Separate deltas keep each bucket width aligned
    # to its own grid, which is what anthropic_transform.compute_window snaps to.
    usage_delta = top_of_hour - _newest_ending(page1, page2)
    cost_delta = midnight - _newest_ending(cost)

    return {
        USAGE_PAGE_1: _shift(page1, usage_delta),
        USAGE_PAGE_2: _shift(page2, usage_delta),
        COST_REPORT: _shift(cost, cost_delta),
    }


#: Frozen at import time -- see the module docstring.
REPORTS = _anchor_reports()
DIRECTORIES = {API_KEYS: _load(API_KEYS), USERS: _load(USERS)}


def _window(report: dict, starting_at, ending_at) -> dict:
    """Drop buckets that do not fall entirely inside [starting_at, ending_at].

    Either bound may be omitted, in which case that side is unbounded.
    """
    try:
        start = _parse(starting_at) if starting_at else None
        end = _parse(ending_at) if ending_at else None
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"type": "invalid_request_error",
                    "message": "starting_at/ending_at must be RFC 3339"},
        )
    if start is None and end is None:
        return report
    selected = [
        b for b in report["data"]
        if (start is None or _parse(b["starting_at"]) >= start)
        and (end is None or _parse(b["ending_at"]) <= end)
    ]
    windowed = dict(report)
    windowed["data"] = selected
    return windowed


def _require_key(x_api_key) -> None:
    if not x_api_key:
        raise HTTPException(status_code=401, detail={"type": "authentication_error"})


@app.get("/v1/organizations/usage_report/messages")
def usage_report(page: str = Query(default=None),
                 starting_at: str = Query(default=None),
                 ending_at: str = Query(default=None),
                 x_api_key: str = Header(default=None)):
    _require_key(x_api_key)
    name = USAGE_PAGE_2 if page == "page_demo_2" else USAGE_PAGE_1
    return _window(REPORTS[name], starting_at, ending_at)


@app.get("/v1/organizations/cost_report")
def cost_report(starting_at: str = Query(default=None),
                ending_at: str = Query(default=None),
                x_api_key: str = Header(default=None)):
    _require_key(x_api_key)
    return _window(REPORTS[COST_REPORT], starting_at, ending_at)


@app.get("/v1/organizations/api_keys")
def api_keys(x_api_key: str = Header(default=None)):
    _require_key(x_api_key)
    return DIRECTORIES[API_KEYS]


@app.get("/v1/organizations/users")
def users(x_api_key: str = Header(default=None)):
    _require_key(x_api_key)
    return DIRECTORIES[USERS]

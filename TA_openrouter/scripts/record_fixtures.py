"""Record live OpenRouter API responses into openrouter_mockserver fixtures.

Usage:
    OPENROUTER_MANAGEMENT_KEY=sk-or-v1-... python3 TA_openrouter/scripts/record_fixtures.py

Writes verbatim response bodies. The recorded shapes become the contract the
mock server and the transform module are written against.
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "https://openrouter.ai/api/v1"
FIXTURES = Path(__file__).resolve().parent.parent / "openrouter_mockserver" / "fixtures"

METRICS = [
    "request_count", "total_usage", "tokens_total", "tokens_prompt",
    "tokens_completion", "reasoning_tokens", "cached_tokens", "byok_usage",
]


def _call(path, key, body=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Authorization", "Bearer " + key)
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def main():
    key = os.environ.get("OPENROUTER_MANAGEMENT_KEY")
    if not key:
        sys.exit("OPENROUTER_MANAGEMENT_KEY is not set")
    FIXTURES.mkdir(parents=True, exist_ok=True)

    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=7)
    window = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    captures = {
        "analytics_meta.json": ("/analytics/meta", None),
        "keys.json": ("/keys?include_disabled=true", None),
        "analytics_by_key_model.json": ("/analytics/query", {
            "metrics": METRICS,
            "dimensions": ["api_key_id", "model"],
            "granularity": "hour",
            "time_range": window,
        }),
        "analytics_by_model_provider.json": ("/analytics/query", {
            "metrics": METRICS,
            "dimensions": ["model", "provider"],
            "granularity": "hour",
            "time_range": window,
        }),
    }

    for name, (path, body) in captures.items():
        payload = _call(path, key, body)
        (FIXTURES / name).write_text(json.dumps(payload, indent=2) + "\n")
        print("wrote", name)


if __name__ == "__main__":
    main()

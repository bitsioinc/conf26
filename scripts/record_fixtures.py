"""Capture real Admin API responses for refreshing the mock fixtures.

Usage: ANTHROPIC_ADMIN_KEY=sk-ant-admin01-... python scripts/record_fixtures.py
Writes raw responses to mockserver/fixtures/raw_*.json (gitignored). Manually
review raw_* files, sanitize names/emails, then merge into the committed
fixtures — keeping apikey_rogue_demo in usage page 2 and OUT of api_keys.json.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "TA_anthropic" / "package" / "bin"))
from anthropic_client import AnthropicAdminClient  # noqa: E402

OUT = Path(__file__).parent.parent / "mockserver" / "fixtures"


def main():
    key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not key:
        sys.exit("Set ANTHROPIC_ADMIN_KEY (sk-ant-admin01-...)")
    OUT.mkdir(parents=True, exist_ok=True)
    client = AnthropicAdminClient(key)
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    usage_pages = list(client.iter_usage_report(
        (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        bucket_width="1h",
        group_by=["model", "workspace_id", "api_key_id", "service_tier"]))
    for i, page in enumerate(usage_pages, 1):
        (OUT / "raw_usage_page{}.json".format(i)).write_text(json.dumps(page, indent=2))

    cost_pages = list(client.iter_cost_report(
        (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z"),
        now.strftime("%Y-%m-%dT00:00:00Z"),
        group_by=["workspace_id", "description"]))
    (OUT / "raw_cost.json").write_text(json.dumps({"pages": cost_pages}, indent=2))

    (OUT / "raw_api_keys.json").write_text(json.dumps(
        {"data": client.list_api_keys(), "has_more": False}, indent=2))
    (OUT / "raw_users.json").write_text(json.dumps(
        {"data": client.list_users(), "has_more": False}, indent=2))

    print("Wrote raw_* files to {}. Review, sanitize, then merge into the "
          "committed fixtures (keep apikey_rogue_demo in usage page 2 only).".format(OUT))


if __name__ == "__main__":
    main()

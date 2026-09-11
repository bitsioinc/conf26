import json
import re
from pathlib import Path

FIXTURES = Path(__file__).parent.parent / "mockserver" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_usage_pages_paginate():
    p1 = load("usage_report_page1.json")
    p2 = load("usage_report_page2.json")
    assert p1["has_more"] is True and p1["next_page"] == "page_demo_2"
    assert p2["has_more"] is False and p2["next_page"] is None
    for page in (p1, p2):
        for bucket in page["data"]:
            assert set(bucket) == {"starting_at", "ending_at", "results"}
            for r in bucket["results"]:
                assert {"uncached_input_tokens", "cache_read_input_tokens",
                        "cache_creation", "output_tokens", "model",
                        "workspace_id", "api_key_id", "service_tier"} <= set(r)


def test_rogue_key_in_usage_but_not_directory():
    p2 = load("usage_report_page2.json")
    usage_keys = {r["api_key_id"] for b in p2["data"] for r in b["results"]}
    directory_keys = {k["id"] for k in load("api_keys.json")["data"]}
    assert "apikey_rogue_demo" in usage_keys
    assert "apikey_rogue_demo" not in directory_keys


def test_cost_amounts_are_decimal_strings():
    cost = load("cost_report.json")
    for b in cost["data"]:
        for r in b["results"]:
            assert isinstance(r["amount"], str) and float(r["amount"]) > 0
            assert re.fullmatch(r"\d+\.\d{2}", r["amount"]), r["amount"]
            assert r["currency"] == "USD"


def test_cost_amounts_are_cents_and_read_as_plausible_daily_spend():
    # `amount` is in the lowest currency unit (cents), so a believable day for
    # an org burning millions of tokens is tens of thousands of cents.
    cost = load("cost_report.json")
    for b in cost["data"]:
        usd = sum(float(r["amount"]) for r in b["results"]) / 100.0
        assert 100.0 <= usd <= 2000.0, (b["starting_at"], usd)

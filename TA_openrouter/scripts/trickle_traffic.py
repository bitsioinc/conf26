"""Generate real OpenRouter inference traffic so the analytics dashboards have
a genuine time axis.

Usage:
    python3 TA_openrouter/scripts/trickle_traffic.py --hours 36
    python3 TA_openrouter/scripts/trickle_traffic.py --hours 2 --budget 0.25   # short rehearsal
    python3 TA_openrouter/scripts/trickle_traffic.py --dry-run                 # plan only, no calls

Why this exists
---------------
OpenRouter's analytics granularity is hourly. Every call made in one sitting
lands in a *single* bucket, so a burst of a thousand requests still renders as
one bar. Only wall-clock spread produces a chart with a time axis, which is why
this runs for hours rather than minutes.

It also spreads calls across several models and *pinned* providers so the Model
Mix and Provider Routing dashboards have more than one series each. The
model/provider pairs below were verified against
``GET /models/{slug}/endpoints`` -- passing a provider a model is not served by
makes the request fail rather than silently reroute.

Keys
----
Reads ``splunk/.env.openrouter``. Any line of the form ``NAME=sk-or-v1-...``
is treated as an inference key, and NAME is what shows up as ``api_key_id`` in
the analytics dashboards. Two or more keys give the "spend by key" panels more
than one series.

    Alpaca=sk-or-v1-...
    Ferdinand=sk-or-v1-...

**Inference keys, not the management key.** They share the ``sk-or-v1-``
prefix and are distinguishable only by the page that created them. A management
key returns 401 here. A key that has reached its credit limit returns 402 --
the script reports that per key and keeps going with the others rather than
dying on it.

Spend
-----
Bounded by ``--budget`` (default $2.00), enforced against OpenRouter's own
reported cost after every call and re-checked before each new one. The models
below cost roughly $0.0000001 per completion token, so a full 36-hour run
typically lands well under a dollar. Stops early and says so if the cap is hit.
"""
import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
ENV_FILE = ROOT / "splunk" / ".env.openrouter"

BASE = "https://openrouter.ai/api/v1"
COMPLETIONS = BASE + "/chat/completions"

#: (model, provider) pairs verified against GET /models/{slug}/endpoints.
#: Pinning the provider with allow_fallbacks=false is what makes the Provider
#: Routing dashboard show distinct upstreams instead of one house default.
ROUTES = [
    ("meta-llama/llama-3.1-8b-instruct", "DeepInfra"),
    ("meta-llama/llama-3.1-8b-instruct", "Novita"),
    ("meta-llama/llama-3.1-8b-instruct", "Groq"),
    ("mistralai/mistral-nemo", "DeepInfra"),
    ("mistralai/mistral-nemo", "Parasail"),
    ("openai/gpt-oss-20b", "CoreWeave"),
    ("openai/gpt-oss-20b", "Together"),
    ("google/gemma-3-4b-it", "DeepInfra"),
]

#: Short, boring prompts. Varied so responses are not served from a cache and
#: reported token counts stay realistic, but small enough to keep spend trivial.
PROMPTS = [
    "Name one benefit of structured logging. One sentence.",
    "What does an observability pipeline do? One sentence.",
    "Define 'cardinality' in metrics. One sentence.",
    "Why checkpoint a polling data ingest? One sentence.",
    "What is a technical add-on in Splunk? One sentence.",
    "Give one reason to rotate API keys. One sentence.",
    "What does 'shadow AI' mean? One sentence.",
    "Why cap a query's row limit? One sentence.",
]

MAX_TOKENS = 60


def load_keys():
    """Return [(name, secret)] from the env file, management keys excluded."""
    if not ENV_FILE.exists():
        sys.exit("No key file at {}".format(ENV_FILE))
    keys = []
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, secret = line.partition("=")
        name, secret = name.strip(), secret.strip()
        if not secret.startswith("sk-or-v1-"):
            continue
        if name.upper() in ("OPENROUTER_MANAGEMENT_KEY", "MANAGEMENT_KEY"):
            continue
        keys.append((name, secret))
    if not keys:
        sys.exit(
            "No inference keys in {}.\n"
            "Add one line per key, e.g.  Alpaca=sk-or-v1-...\n"
            "These must be inference keys from the API-keys page, not the "
            "management key.".format(ENV_FILE)
        )
    return keys


def call(secret, model, provider, prompt):
    """One chat completion. Returns (cost_usd, tokens) or raises."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": MAX_TOKENS,
        "provider": {"order": [provider], "allow_fallbacks": False},
        "usage": {"include": True},
    }
    req = urllib.request.Request(
        COMPLETIONS, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + secret)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "conf26-trickle/1.0")
    with urllib.request.urlopen(req, timeout=90) as resp:
        payload = json.loads(resp.read().decode())
    usage = payload.get("usage") or {}
    return float(usage.get("cost") or 0.0), int(usage.get("total_tokens") or 0)


def stamp():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--hours", type=float, default=36.0,
                    help="wall-clock hours to spread traffic over (default 36)")
    ap.add_argument("--per-hour", type=int, default=12,
                    help="calls per hour per key (default 12)")
    ap.add_argument("--budget", type=float, default=2.00,
                    help="hard USD ceiling across the whole run (default 2.00)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan and exit without calling anything")
    args = ap.parse_args()

    keys = load_keys()
    rounds = max(1, int(args.hours * args.per_hour))
    interval = (args.hours * 3600.0) / rounds

    print("OpenRouter trickle traffic")
    print("  keys        : {}".format(", ".join(n for n, _ in keys)))
    print("  routes      : {} model/provider pairs".format(len(ROUTES)))
    print("  duration    : {:.1f}h, one call per key every {:.1f}s"
          .format(args.hours, interval))
    print("  planned     : {} calls".format(rounds * len(keys)))
    print("  budget      : ${:.2f} (hard stop)".format(args.budget))
    print("  started     : {}".format(stamp()))
    print()

    if args.dry_run:
        print("Dry run -- no calls made.")
        return 0

    spent = 0.0
    made = 0
    failed = 0
    dead = set()
    deadline = time.time() + args.hours * 3600.0
    stop = False

    while not stop and time.time() < deadline and len(dead) < len(keys):
        for name, secret in keys:
            if name in dead:
                continue
            if spent >= args.budget:
                print("[{}] budget ${:.2f} reached -- stopping early"
                      .format(stamp(), args.budget))
                stop = True
                break

            model, provider = random.choice(ROUTES)
            prompt = random.choice(PROMPTS)
            try:
                cost, tokens = call(secret, model, provider, prompt)
            except urllib.error.HTTPError as exc:
                detail = exc.read()[:200].decode("utf-8", "replace")
                failed += 1
                if exc.code in (401, 402, 403):
                    # Permanent for this key: wrong key type, or out of credit.
                    dead.add(name)
                    print("[{}] {:<14} DISABLED after HTTP {} -- {}"
                          .format(stamp(), name, exc.code, detail))
                else:
                    print("[{}] {:<14} HTTP {} on {} via {} -- {}"
                          .format(stamp(), name, exc.code, model, provider, detail))
                continue
            except Exception as exc:              # network blips, timeouts
                failed += 1
                print("[{}] {:<14} {}: {}".format(
                    stamp(), name, type(exc).__name__, exc))
                continue

            spent += cost
            made += 1
            print("[{}] {:<14} {:<36} via {:<12} {:>4} tok  ${:.6f}  "
                  "(total ${:.4f})".format(
                      stamp(), name, model, provider, tokens, cost, spent))

        if not stop and time.time() < deadline:
            time.sleep(interval)

    print()
    print("Done at {}".format(stamp()))
    print("  calls succeeded : {}".format(made))
    print("  calls failed    : {}".format(failed))
    print("  keys disabled   : {}".format(", ".join(sorted(dead)) or "none"))
    print("  total spend     : ${:.4f}".format(spent))
    print()
    print("The Analytics input picks this up on its next interval (hourly by")
    print("default). Analytics only reports *completed* hour buckets, so the")
    print("most recent hour will not appear until it closes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

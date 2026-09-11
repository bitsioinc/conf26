#!/usr/bin/env bash
# Readiness check for the DEV1194 demo environment, covering BOTH add-ons.
#
# Run:  scripts/preflight.sh
#
# Optional env:
#   SPLUNK_HOME           default /opt/splunk
#   SPLUNK_WEB_URL        default http://localhost:8000
#   ANTHROPIC_MOCK_URL    default http://127.0.0.1:8081
#   OPENROUTER_MOCK_URL   default http://127.0.0.1:8082/api/v1
#   ANTHROPIC_ADMIN_KEY   if set, the live Admin API check runs (otherwise SKIP)
#   OPENROUTER_API_KEY    if set, the live management API check runs (otherwise SKIP)
#
# Prints PASS/FAIL/SKIP per check, a tally, and exits non-zero if any required
# check failed. Splunk-dependent checks fail on a machine with no Splunk
# install; that is expected and is not a defect in the add-ons.
set -uo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"

SPLUNK_HOME="${SPLUNK_HOME:-/opt/splunk}"
SPLUNK_WEB_URL="${SPLUNK_WEB_URL:-http://localhost:8000}"
ANTHROPIC_MOCK_URL="${ANTHROPIC_MOCK_URL:-http://127.0.0.1:8081}"
OPENROUTER_MOCK_URL="${OPENROUTER_MOCK_URL:-http://127.0.0.1:8082/api/v1}"

PASS=0; FAIL=0; SKIP=0
FAILED_CHECKS=()

check() {  # check "<label>" "<shell command>"
  if eval "$2" >/dev/null 2>&1; then
    echo "PASS  $1"; PASS=$((PASS+1))
  else
    echo "FAIL  $1"; FAIL=$((FAIL+1)); FAILED_CHECKS+=("$1")
  fi
}

skip() {   # skip "<label>" "<reason>"
  echo "SKIP  $1  ($2)"; SKIP=$((SKIP+1))
}

# --- helpers ---------------------------------------------------------------
web_ui_reachable() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$SPLUNK_WEB_URL")"
  [ "$code" = "200" ] || [ "$code" = "303" ] || [ "$code" = "302" ]
}

anthropic_mock_answering() {
  curl -sf -o /dev/null --max-time 5 \
    -H 'x-api-key: sk-ant-admin01-demo' \
    "$ANTHROPIC_MOCK_URL/v1/organizations/usage_report/messages"
}

openrouter_mock_answering() {
  curl -sf -o /dev/null --max-time 5 \
    -H 'Authorization: Bearer demo-management-key' \
    "$OPENROUTER_MOCK_URL/keys"
}

admin_api_reachable() {
  curl -sf -o /dev/null --max-time 15 \
    -H "x-api-key: ${ANTHROPIC_ADMIN_KEY:-}" \
    -H 'anthropic-version: 2023-06-01' \
    'https://api.anthropic.com/v1/organizations/users?limit=1'
}

openrouter_api_reachable() {
  curl -sf -o /dev/null --max-time 15 \
    -H "Authorization: Bearer ${OPENROUTER_API_KEY:-}" \
    'https://openrouter.ai/api/v1/keys'
}

echo "DEV1194 preflight  —  repo: $REPO"
echo "  splunk: $SPLUNK_HOME   web: $SPLUNK_WEB_URL"
echo "  mocks:  anthropic $ANTHROPIC_MOCK_URL   openrouter $OPENROUTER_MOCK_URL"
echo

# --- build toolchain -------------------------------------------------------
check "venv python present"          "test -x .venv/bin/python && .venv/bin/python --version"
check "ucc-gen installed"            "test -x .venv/bin/ucc-gen && .venv/bin/ucc-gen --help"
check "slim installed"               "test -x .venv/bin/slim && .venv/bin/slim --version"

# --- code health: BOTH suites ----------------------------------------------
check "TA_anthropic tests green"     ".venv/bin/pytest -q tests"
check "TA_openrouter tests green"    ".venv/bin/pytest -q TA_openrouter/tests"

# --- artifacts: BOTH add-ons -----------------------------------------------
check "TA_anthropic package built"   "ls dist/TA_anthropic-*.tar.gz"
check "TA_openrouter package built"  "ls dist/TA_openrouter-*.tar.gz"

# --- runtime services ------------------------------------------------------
check "anthropic mock answering"     "anthropic_mock_answering"
check "openrouter mock answering"    "openrouter_mock_answering"
check "splunkd running"              "$SPLUNK_HOME/bin/splunk status"
check "Splunk web UI reachable"      "web_ui_reachable"

# --- installed add-ons: BOTH -----------------------------------------------
check "TA_anthropic installed"       "test -d $SPLUNK_HOME/etc/apps/TA_anthropic"
check "TA_openrouter installed"      "test -d $SPLUNK_HOME/etc/apps/TA_openrouter"

# --- optional: live APIs ---------------------------------------------------
if [ -n "${ANTHROPIC_ADMIN_KEY:-}" ]; then
  check "live Anthropic Admin API"   "admin_api_reachable"
else
  skip "live Anthropic Admin API" "ANTHROPIC_ADMIN_KEY not set; the demo runs against the mock"
fi

if [ -n "${OPENROUTER_API_KEY:-}" ]; then
  check "live OpenRouter API"        "openrouter_api_reachable"
else
  skip "live OpenRouter API" "OPENROUTER_API_KEY not set; the demo runs against the mock"
fi

echo
echo "$PASS passed, $FAIL failed, $SKIP skipped"
if [ "$FAIL" -ne 0 ]; then
  echo "Failed checks:"
  for c in "${FAILED_CHECKS[@]}"; do echo "  - $c"; done
fi
[ "$FAIL" -eq 0 ]

#!/usr/bin/env bash
# Build TA_openrouter:
#   1. resolve and validate a Python 3.9 interpreter to vendor lib/ wheels with
#   2. snapshot globalConfig.json (ucc-gen rewrites it in place; restored on exit)
#   3. ucc-gen build
#   4. strip junk from output/
#   5. assert the generated modular inputs actually wire up their helpers
#   6. slim validate
#   7. slim package into repo-root dist/
#
# Uses explicit .venv/bin/* paths instead of `source .venv/bin/activate` so the
# script behaves identically in interactive shells, CI, and non-interactive
# agent shells.
set -euo pipefail
cd "$(dirname "$0")/../.."

ROOT="$(pwd)"
VENV="$ROOT/.venv/bin"
UCC_GEN="$VENV/ucc-gen"
SLIM="$VENV/slim"
TA_VERSION="${TA_VERSION:-0.1.0}"

for tool in "$UCC_GEN" "$SLIM"; do
  if [ ! -x "$tool" ]; then
    echo "ERROR: $tool not found. Create the venv and install requirements-dev.txt first." >&2
    exit 1
  fi
done

# --- protect globalConfig.json from ucc-gen's in-place rewrite -----------
#
# `ucc-gen build` parses, schema-validates, and re-serializes globalConfig.json
# back onto disk as a side effect (reformats indentation and fills in
# schema-default keys, e.g. adds an explicit "required": true it infers for a
# field). That's unconditional, deterministic ucc-gen behavior, not something
# this script asks for -- but globalConfig.json is a reviewed/settled file
# whose 'inputHelperModule' keys are the difference between a working add-on
# and a silently inert one, so it must never drift unnoticed. Snapshot it
# before the build and restore the snapshot via an EXIT trap, which fires on
# success, on a normal error exit under `set -e`, and on a signal -- so both
# the happy path and any failure path leave the file untouched. A plain file
# copy (not `git checkout`) is used deliberately: it works in a non-git
# checkout, and it restores exactly whatever bytes were on disk before this
# script ran rather than jumping to git HEAD, so it can't clobber a
# deliberate uncommitted edit the operator made themselves.
CONFIG="$ROOT/TA_openrouter/globalConfig.json"
CONFIG_BACKUP="$(mktemp "${TMPDIR:-/tmp}/ta_openrouter_globalConfig.XXXXXX")"
cp "$CONFIG" "$CONFIG_BACKUP"

restore_global_config() {
  cp "$CONFIG_BACKUP" "$CONFIG"
  rm -f "$CONFIG_BACKUP"
  echo "==> restored TA_openrouter/globalConfig.json to its pre-build state"
}
trap restore_global_config EXIT

# --- pin the python3 used to vendor lib/requirements.txt wheels ---------
#
# `ucc-gen build` defaults `--python-binary-name` to "python3" resolved off
# PATH. In an interactive/CI shell that is very often a 3.11 interpreter
# (this repo's own .venv is 3.11), which silently vendors cp311 wheels into
# TA_openrouter/lib/. The build succeeds and `slim validate` is silent about
# it, but Splunk's runtime is CPython 3.9.25 -- the mismatch only surfaces
# when Splunk tries to import the vendored packages and fails, which is a
# much worse place to discover it. Pin explicitly to a 3.9 interpreter here.
#
# The same check (executable, and actually reports itself as 3.9.x) is
# applied whether the interpreter came from TA_OPENROUTER_PYTHON39 or was
# auto-discovered: an operator-supplied override that is wrong should fail
# exactly as loudly as no override at all, not fail later with a murkier
# error from inside ucc-gen/pip, and not be silently discarded in favor of a
# different interpreter than the one the operator explicitly asked for.
python39_version_ok() {
  local candidate="$1"
  [ -n "$candidate" ] || return 1
  [ -x "$candidate" ] || return 1
  local version
  version="$("$candidate" --version 2>&1)" || return 1
  case "$version" in
    Python\ 3.9.*|Python\ 3.9) return 0 ;;
    *) return 1 ;;
  esac
}

PYTHON39="${TA_OPENROUTER_PYTHON39:-}"
if [ -n "$PYTHON39" ]; then
  if ! python39_version_ok "$PYTHON39"; then
    echo "ERROR: TA_OPENROUTER_PYTHON39=$PYTHON39 is not a usable Python 3.9 interpreter." >&2
    if [ ! -e "$PYTHON39" ]; then
      echo "       The path does not exist." >&2
    elif [ ! -x "$PYTHON39" ]; then
      echo "       The path exists but is not executable." >&2
    else
      echo "       Actual version report: $("$PYTHON39" --version 2>&1)" >&2
    fi
    echo "       Splunk's runtime is CPython 3.9.25. Building lib/ wheels with any other" >&2
    echo "       python3 (e.g. this repo's 3.11 .venv) vendors incompatible wheels that" >&2
    echo "       only fail once Splunk tries to import them -- not during this build." >&2
    echo "       Fix or unset TA_OPENROUTER_PYTHON39." >&2
    exit 1
  fi
else
  for candidate in /opt/splunk/bin/python3.9 "$(command -v python3.9 2>/dev/null || true)"; do
    if python39_version_ok "$candidate"; then
      PYTHON39="$candidate"
      break
    fi
  done
fi

if [ -z "$PYTHON39" ]; then
  echo "ERROR: no Python 3.9 interpreter found (checked /opt/splunk/bin/python3.9 and PATH)." >&2
  echo "       Splunk's runtime is CPython 3.9.25. Building lib/ wheels with any other" >&2
  echo "       python3 (e.g. this repo's 3.11 .venv) vendors incompatible wheels that" >&2
  echo "       only fail once Splunk tries to import them -- not during this build." >&2
  echo "       Set TA_OPENROUTER_PYTHON39=/path/to/python3.9 to override." >&2
  exit 1
fi

echo "==> pinning python-binary-name to $PYTHON39 ($("$PYTHON39" --version 2>&1))"

echo "==> clean stale build artifacts"
rm -rf "$ROOT/TA_openrouter/output"
find "$ROOT/TA_openrouter/package" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$ROOT/TA_openrouter/package" -name '*.pyc' -delete 2>/dev/null || true

echo "==> ucc-gen build (version ${TA_VERSION})"
(cd "$ROOT/TA_openrouter" && "$UCC_GEN" build --source package --ta-version "$TA_VERSION" --python-binary-name "$PYTHON39")

# Test-only hook: lets us prove the globalConfig.json restore trap above
# fires on a failure path, not just the happy path, without hand-editing
# this script for every review. Off unless explicitly requested.
if [ "${TA_OPENROUTER_TEST_FORCE_FAIL_AFTER_BUILD:-}" = "1" ]; then
  echo "==> TA_OPENROUTER_TEST_FORCE_FAIL_AFTER_BUILD=1 set: forcing a non-zero exit" >&2
  echo "    right after ucc-gen build to test the globalConfig.json restore path." >&2
  exit 1
fi

echo "==> strip build leftovers from output"
find "$ROOT/TA_openrouter/output/TA_openrouter" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$ROOT/TA_openrouter/output/TA_openrouter" -name '*.pyc' -delete 2>/dev/null || true
find "$ROOT/TA_openrouter/output/TA_openrouter" -name '.DS_Store' -delete 2>/dev/null || true

# --- assert the modular input helper wiring landed in the built output ---
#
# Nothing else in this pipeline detects a missing/disconnected
# inputHelperModule: `ucc-gen build` will happily generate a stub
# bin/openrouter_analytics.py or bin/openrouter_keys.py that never calls
# into the OpenRouter helper, `slim validate` reports 0 errors against it,
# and AppInspect precert reports 0/0 against it too. If this generated
# wiring is ever missing, the input silently does nothing at runtime.
ANALYTICS_SCRIPT="$ROOT/TA_openrouter/output/TA_openrouter/bin/openrouter_analytics.py"
KEYS_SCRIPT="$ROOT/TA_openrouter/output/TA_openrouter/bin/openrouter_keys.py"

if ! grep -q openrouter_analytics_helper "$ANALYTICS_SCRIPT"; then
  echo "ERROR: $ANALYTICS_SCRIPT does not reference openrouter_analytics_helper." >&2
  echo "       This means the generated modular input is a stub that never calls" >&2
  echo "       OpenRouter -- it would ship, pass slim validate, and pass AppInspect" >&2
  echo "       precert while silently doing nothing. Check globalConfig.json's" >&2
  echo "       'inputHelperModule' for the openrouter_analytics input." >&2
  exit 1
fi

if ! grep -q openrouter_keys_helper "$KEYS_SCRIPT"; then
  echo "ERROR: $KEYS_SCRIPT does not reference openrouter_keys_helper." >&2
  echo "       This means the generated modular input is a stub that never calls" >&2
  echo "       OpenRouter -- it would ship, pass slim validate, and pass AppInspect" >&2
  echo "       precert while silently doing nothing. Check globalConfig.json's" >&2
  echo "       'inputHelperModule' for the openrouter_keys input." >&2
  exit 1
fi

echo "==> helper wiring confirmed in built output"

echo "==> slim validate"
"$SLIM" validate "$ROOT/TA_openrouter/output/TA_openrouter"

echo "==> slim package"
mkdir -p "$ROOT/dist"
rm -f "$ROOT"/dist/TA_openrouter-*.tar.gz
"$SLIM" package "$ROOT/TA_openrouter/output/TA_openrouter" -o "$ROOT/dist"

echo "==> done"
ls -lh "$ROOT/dist/"

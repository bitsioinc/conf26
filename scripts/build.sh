#!/usr/bin/env bash
# Build TA_anthropic: ucc-gen build -> slim validate -> slim package
#
# Uses explicit .venv/bin/* paths instead of `source .venv/bin/activate` so the
# script behaves identically in interactive shells, CI, and non-interactive
# agent shells.
set -euo pipefail
cd "$(dirname "$0")/.."

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

echo "==> clean stale build artifacts"
rm -rf "$ROOT/TA_anthropic/output"
find "$ROOT/TA_anthropic/package" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$ROOT/TA_anthropic/package" -name '*.pyc' -delete 2>/dev/null || true

echo "==> ucc-gen build (version ${TA_VERSION})"
(cd "$ROOT/TA_anthropic" && "$UCC_GEN" build --source package --ta-version "$TA_VERSION")

echo "==> strip build leftovers from output"
find "$ROOT/TA_anthropic/output/TA_anthropic" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$ROOT/TA_anthropic/output/TA_anthropic" -name '*.pyc' -delete 2>/dev/null || true
find "$ROOT/TA_anthropic/output/TA_anthropic" -name '.DS_Store' -delete 2>/dev/null || true

echo "==> slim validate"
"$SLIM" validate "$ROOT/TA_anthropic/output/TA_anthropic"

echo "==> slim package"
mkdir -p "$ROOT/dist"
rm -f "$ROOT"/dist/TA_anthropic-*.tar.gz
"$SLIM" package "$ROOT/TA_anthropic/output/TA_anthropic" -o "$ROOT/dist"

echo "==> done"
ls -lh "$ROOT/dist/"

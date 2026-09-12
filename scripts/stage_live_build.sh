#!/usr/bin/env bash
# Build the DEV1194 on-stage live-build project.
#
# Produces, OUTSIDE this repo:
#
#   <parent>/<repo>-live-demo/
#     stage/                 empty, plain directory -- open Claude Code HERE
#     kit/
#       checkpoints.git/     bare repo: empty -> scaffold -> client -> inputs -> final
#       rescue               ../kit/rescue <stage>  materialise a checkpoint into stage/
#       reset                ../kit/reset           wipe stage/ back to empty
#       prompt               ../kit/prompt          copy the demo prompt to the clipboard
#       STAGE_CARD.md        the one-page card for the podium
#
# WHY OUTSIDE THIS REPO: Claude Code loads CLAUDE.md from every parent
# directory. A live-build/ inside this repo would inherit its CLAUDE.md, which
# spells out the cents divisor, the inputHelperModule trap and the Python 3.9
# pin -- the exact things the talk claims Claude discovers from the API docs.
# The stage directory must have no CLAUDE.md anywhere above it.
#
# Uses explicit paths rather than `source .venv/bin/activate`, per this repo's
# convention, so it behaves the same in a shell, in CI and under an agent.
set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${LIVE_DEMO_DIR:-$(dirname "$SRC")/$(basename "$SRC")-live-demo}"
STAGE="$DEST/stage"
KIT="$DEST/kit"
BARE="$KIT/checkpoints.git"
TA="$SRC/TA_anthropic"

FORCE=0
[ "${1:-}" = "--force" ] && FORCE=1

if [ -e "$DEST" ] && [ "$FORCE" -ne 1 ]; then
  echo "ERROR: $DEST already exists. Re-run with --force to rebuild it." >&2
  exit 1
fi

for f in "$TA/globalConfig.json" "$TA/package/bin/anthropic_client.py" \
         "$SRC/tests/test_client.py" "$SRC/mockserver/fixtures/cost_report.json"; do
  [ -f "$f" ] || { echo "ERROR: missing source file $f" >&2; exit 1; }
done

echo "==> reset $DEST"
rm -rf "$DEST"
mkdir -p "$STAGE" "$KIT"

# ---------------------------------------------------------------- build tree
# WORK is assembled cumulatively; each stage commits the tree as it stands, so
# a checkpoint always contains everything from the checkpoints before it.
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git init -q --bare "$BARE"
GIT="git --git-dir=$BARE --work-tree=$WORK"

commit_stage() {   # commit_stage <tag> <subject>
  local tag="$1" subject="$2"
  find "$WORK" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  find "$WORK" -name '*.pyc' -delete 2>/dev/null || true
  find "$WORK" -name '.DS_Store' -delete 2>/dev/null || true
  $GIT add -A
  $GIT commit -q -m "$subject"
  $GIT tag -f "$tag" >/dev/null
  echo "    tagged $tag  ($($GIT ls-tree -r --name-only "$tag" | wc -l | tr -d ' ') files)"
}

copy() {           # copy <path-relative-to-repo-root> [dest-relative]
  local rel="$1" dst="${2:-$1}"
  mkdir -p "$WORK/$(dirname "$dst")"
  cp "$SRC/$rel" "$WORK/$dst"
}

echo "==> empty root commit (this is what stage/ looks like at 4:00)"
$GIT commit -q --allow-empty -m "empty working directory -- the live build starts here"
$GIT tag -f demo-empty >/dev/null

echo "==> demo-scaffold"
copy TA_anthropic/globalConfig.json
copy TA_anthropic/package/app.manifest
copy TA_anthropic/package/lib/requirements.txt
copy TA_anthropic/package/lib/exclude.txt
copy TA_anthropic/package/README.txt
copy TA_anthropic/package/LICENSES/LICENSE.txt
mkdir -p "$WORK/TA_anthropic/package/static"
cp "$TA"/package/static/*.png "$WORK/TA_anthropic/package/static/"
commit_stage demo-scaffold "scaffold: ucc-gen layout + globalConfig (inputs, account, base URL)"

echo "==> demo-client"
copy TA_anthropic/package/bin/anthropic_client.py
copy TA_anthropic/package/bin/anthropic_transform.py
copy tests/conftest.py
copy tests/test_client.py
copy tests/test_transform.py
mkdir -p "$WORK/mockserver/fixtures"
cp "$SRC"/mockserver/fixtures/*.json "$WORK/mockserver/fixtures/"
cat > "$WORK/requirements-dev.txt" <<'REQ'
splunk-add-on-ucc-framework
pytest
requests
REQ
commit_stage demo-client "client + transform: auth, pagination, retry, bucket windows, cents->USD"

echo "==> demo-inputs"
copy TA_anthropic/package/bin/anthropic_usage_helper.py
copy TA_anthropic/package/bin/anthropic_cost_helper.py
copy TA_anthropic/package/bin/anthropic_directory_helper.py
commit_stage demo-inputs "modular inputs: three helpers, KV Store checkpoint per input"

echo "==> demo-final"
copy TA_anthropic/package/default/props.conf
copy TA_anthropic/package/default/transforms.conf
copy TA_anthropic/package/default/savedsearches.conf
copy TA_anthropic/package/default/data/ui/nav/default.xml
copy TA_anthropic/package/default/data/ui/views/ai_observability.xml
copy TA_anthropic/package/lookups/anthropic_api_key_baseline.csv
copy tests/test_alerts.py
# stage/ has no venv of its own, so point the copied build script at this
# repo's toolchain. TA_VENV overrides it on a machine where conf26 has moved.
mkdir -p "$WORK/scripts"
sed "s|^VENV=\"\$ROOT/.venv/bin\"|VENV=\"\${TA_VENV:-$SRC/.venv/bin}\"|" \
  "$SRC/scripts/build.sh" > "$WORK/scripts/build.sh"
grep -q "TA_VENV" "$WORK/scripts/build.sh" || {
  echo "ERROR: venv rewrite failed in scripts/build.sh -- build would break on stage" >&2
  exit 1; }
chmod +x "$WORK/scripts/build.sh"
commit_stage demo-final "props, saved searches, baseline lookup, dashboard -- end to end"

# --------------------------------------------------------------- kit scripts
cat > "$KIT/rescue" <<'RESCUE'
#!/usr/bin/env bash
# Materialise a checkpoint into stage/. Safe to run over a dirty stage/.
#   ../kit/rescue scaffold | client | inputs | final
set -euo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
STAGE="$(dirname "$KIT")/stage"
BARE="$KIT/checkpoints.git"
case "${1:-}" in
  scaffold|client|inputs|final) TAG="demo-$1" ;;
  "") echo "usage: rescue scaffold|client|inputs|final" >&2; exit 2 ;;
  *)  echo "unknown checkpoint '$1' (scaffold|client|inputs|final)" >&2; exit 2 ;;
esac
mkdir -p "$STAGE"
G="git --git-dir=$BARE --work-tree=$STAGE"
$G read-tree --reset -u "$TAG"
# -x as well as -d: pytest drops a .gitignore inside .pytest_cache/, so without
# -x the caches survive a rescue and stage/ drifts from the checkpoint.
$G clean -fdxq -e .venv
echo "stage/ is now at $TAG:"
ls "$STAGE" | sed 's/^/  /'
RESCUE

cat > "$KIT/reset" <<'RESET'
#!/usr/bin/env bash
# Wipe stage/ back to a genuinely empty directory. Run before every rehearsal
# and once more the morning of the talk.
set -euo pipefail
KIT="$(cd "$(dirname "$0")" && pwd)"
STAGE="$(dirname "$KIT")/stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
if [ -z "$(ls -A "$STAGE")" ]; then
  echo "stage/ is empty -- correct."
else
  echo "stage/ is NOT empty:" >&2; ls -A "$STAGE" >&2; exit 1
fi
RESET

cat > "$KIT/prompt" <<PROMPT
#!/usr/bin/env bash
# Copy the pasteable body of demo/demo_prompt.md to the clipboard. The body is
# everything between the two --- fences; the presenter notes are not copied.
set -euo pipefail
SRC="$SRC"
awk '/^---$/{n++; next} n==1' "\$SRC/demo/demo_prompt.md" | pbcopy
echo "demo prompt copied to clipboard (\$(pbpaste | wc -l | tr -d ' ') lines)"
echo "--- starts ---"
pbpaste | sed -n '1,4p'
PROMPT

cat > "$KIT/STAGE_CARD.md" <<CARD
# DEV1194 — live build, podium card

Regenerate any time with:  \`$SRC/scripts/stage_live_build.sh --force\`

## Before you go on

    cd $STAGE
    ../kit/reset          # stage/ must be EMPTY
    ../kit/prompt         # demo prompt -> clipboard

Bump the terminal font. Row twenty cannot read your normal size.

## At 4:00

    cd $STAGE
    claude

Paste (Cmd-V). The prompt is already on the clipboard.

## If Claude stalls, derails, or eats the clock

Narrate it for ten seconds — "this is what live looks like" — then, in a
second terminal tab:

    cd $STAGE
    ../kit/rescue scaffold     # ucc-gen layout + globalConfig
    ../kit/rescue client       # + REST client, transform, 31 tests green
    ../kit/rescue inputs       # + three modular inputs, checkpointing
    ../kit/rescue final        # + confs, dashboard, alert. 39 tests green

Pick the checkpoint just *ahead* of where Claude got to, so the story keeps
moving forward. Safe to run over a dirty stage/ — it force-resets.

Show tests green:      \`$SRC/.venv/bin/pytest -q tests\`
Show the packaging:    \`./scripts/build.sh\`

## The reveal at 14:30 does not depend on any of this

Splunk already has the finished add-on installed and the mock on :8081. Even
if the live build is a total loss, minutes 14-20 are unaffected. Say
"we'll come back to that" and move to the reveal.

## Do not move this directory into the conf26 repo

Claude Code reads CLAUDE.md from every parent directory. conf26/CLAUDE.md
names the cents divisor, the inputHelperModule trap and the Python 3.9 pin —
the exact things slide 13 claims Claude finds in the API docs on its own.
Staged here, stage/ has no CLAUDE.md above it, so the build is honest.

Checked at build time: no CLAUDE.md in $DEST or any parent.
CARD

chmod +x "$KIT/rescue" "$KIT/reset" "$KIT/prompt"

# Guard the property the whole staging decision rests on.
d="$STAGE"
while [ "$d" != "/" ]; do
  if [ -f "$d/CLAUDE.md" ]; then
    echo "ERROR: $d/CLAUDE.md sits above the stage directory. Claude Code would" >&2
    echo "       load it during the live build. Move the demo elsewhere." >&2
    exit 1
  fi
  d="$(dirname "$d")"
done
echo "==> verified: no CLAUDE.md above $STAGE"

echo
echo "==> done"
echo "  stage : $STAGE"
echo "  kit   : $KIT"
$GIT tag -l | sed 's/^/    tag /'

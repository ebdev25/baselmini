#!/usr/bin/env bash
set -euo pipefail

log() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

# Resolve repo root (parent of this script)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Inputs (repo-local only)
CFG="$REPO_ROOT/configs/std_approach.yml"
EXP="$REPO_ROOT/data/exposures.csv"
CAP="$REPO_ROOT/data/capital.csv"
LIQ="$REPO_ROOT/data/liquidity.csv"
FXF="$REPO_ROOT/data/fx.csv"
NSF="$REPO_ROOT/data/nsfr.csv"
SCN="$REPO_ROOT/scenarios/shock_fx_10pct.yml"

GOLD_EXP="$REPO_ROOT/golden/inputs/exposures.csv"
GOLD_CAP="$REPO_ROOT/golden/inputs/capital.csv"
GOLD_LIQ="$REPO_ROOT/golden/inputs/liquidity.csv"
GOLD_CFG="$REPO_ROOT/golden/inputs/config.yml"
GOLD_NSFR="$REPO_ROOT/golden/inputs/nsfr.csv"
GOLD_EXPECTED="$REPO_ROOT/golden/expected/results.json"

# Outputs (repo-local)
OUT_DEMO="$REPO_ROOT/out_demo"
OUT_DEMO_STRICT="$REPO_ROOT/out_demo_strict"
OUT_SCN="$REPO_ROOT/out_demo_scn"
OUT_GOLDEN="$REPO_ROOT/out_golden"

# Sanity: ensure inputs exist
need() { [[ -f "$1" ]] || die "Missing: $1"; }
need "$CFG"; need "$EXP"; need "$CAP"; need "$LIQ"; need "$NSF"
need "$GOLD_EXP"; need "$GOLD_CAP"; need "$GOLD_LIQ"; need "$GOLD_CFG"; need "$GOLD_NSFR"; need "$GOLD_EXPECTED"
# FX file optional; uncomment to require it:
# need "$FXF"

# Clean output dirs to ensure fresh runs
rm -rf "$OUT_DEMO" "$OUT_DEMO_STRICT" "$OUT_SCN" "$OUT_GOLDEN"
mkdir -p "$OUT_DEMO" "$OUT_DEMO_STRICT" "$OUT_SCN" "$OUT_GOLDEN"

log "Print version"
baselmini --version

log "Using repo-local examples (no installed data-files)"
printf "[configs]\n   %s\n" "$CFG"
printf "[data]\n   %s\n   %s\n   %s\n   %s\n   %s\n" "$CAP" "$EXP" "$FXF" "$LIQ" "$NSF"
printf "[scenarios]\n   %s\n" "$SCN"
printf "[golden/inputs]\n   %s\n   %s\n   %s\n   %s\n   %s\n" "$GOLD_CAP" "$GOLD_CFG" "$GOLD_EXP" "$GOLD_LIQ" "$GOLD_NSFR"

log "Show config (repo)"
baselmini --show-config --config-file "$CFG" | head -40

log "Examples: DRY RUN (should not require --out)"
baselmini run \
  --asof 2025-09-15 \
  --exposures  "$EXP" \
  --capital    "$CAP" \
  --liquidity  "$LIQ" \
  --config     "$CFG" \
  --nsfr       "$NSF" \
  --dry-run

log "Examples: normal run -> $OUT_DEMO"
baselmini run \
  --asof 2025-09-15 \
  --exposures  "$EXP" \
  --capital    "$CAP" \
  --liquidity  "$LIQ" \
  --config     "$CFG" \
  --nsfr       "$NSF" \
  --out        "$OUT_DEMO"

log "Examples: print report to stdout (first 50 lines)"
baselmini run \
  --asof 2025-09-15 \
  --exposures  "$EXP" \
  --capital    "$CAP" \
  --liquidity  "$LIQ" \
  --config     "$CFG" \
  --nsfr       "$NSF" \
  --stdout report | sed -n '1,50p'

log "Examples: strict mode should fail on warnings (expect non-zero exit)"
if baselmini run \
  --asof 2025-09-15 \
  --exposures  "$EXP" \
  --capital    "$CAP" \
  --liquidity  "$LIQ" \
  --config     "$CFG" \
  --nsfr       "$NSF" \
  --strict \
  --out        "$OUT_DEMO_STRICT" ; then
  die "--strict did not fail on warnings (expected failure)"
else
  echo "OK: --strict failed as expected"
fi

log "Golden: full run -> $OUT_GOLDEN"
baselmini run \
  --asof 2025-09-15 \
  --exposures  "$GOLD_EXP" \
  --capital    "$GOLD_CAP" \
  --liquidity  "$GOLD_LIQ" \
  --config     "$GOLD_CFG" \
  --nsfr       "$GOLD_NSFR" \
  --out        "$OUT_GOLDEN"

log "Golden: strict run must pass cleanly (no warnings)"
if ! baselmini run \
  --asof 2025-09-15 \
  --exposures  "$GOLD_EXP" \
  --capital    "$GOLD_CAP" \
  --liquidity  "$GOLD_LIQ" \
  --config     "$GOLD_CFG" \
  --nsfr       "$GOLD_NSFR" \
  --strict \
  --out        "$OUT_GOLDEN" >/dev/null ; then
  die "Golden strict run failed (unexpected warnings or errors)"
fi
echo "OK: Golden strict run passed"

log "Golden: compare results.json to expected"
if command -v jq >/dev/null 2>&1; then
  if ! diff -u <(jq -S . "$GOLD_EXPECTED") <(jq -S . "$OUT_GOLDEN/results.json"); then
    die "Golden results.json does not match expected"
  fi
else
  if ! diff -u "$GOLD_EXPECTED" "$OUT_GOLDEN/results.json"; then
    die "Golden results.json does not match expected"
  fi
fi
echo "Golden match OK"

log "Scenario: FX shock -> $OUT_SCN"
baselmini run \
  --asof 2025-09-15 \
  --exposures  "$EXP" \
  --capital    "$CAP" \
  --liquidity  "$LIQ" \
  --config     "$CFG" \
  --nsfr       "$NSF" \
  --scenario   "$SCN" \
  --out        "$OUT_SCN"

log "All smoke tests passed"


#!/usr/bin/env bash
# Pack a reviewer-facing DIPTYCH artifact zip (harness-only).
# Does not vendor ZeroDay/AOMB product trees. Does not invent AUROC / model scores.
#
# Usage (from repo root):
#   ./scripts/pack_artifact.sh              # writes dist/DIPTYCH-<shortsha>.zip
#   ./scripts/pack_artifact.sh /tmp/out     # optional output directory
#
# PDF: included if paper/one-trace-is-not-enough.pdf exists (make paper or CI
# paper-pdf download). Otherwise ARTIFACT_NOTES.txt explains how to attach the
# CI PDF artifact "one-trace-is-not-enough-pdf".
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

OUT_DIR="${1:-$ROOT/dist}"
mkdir -p "$OUT_DIR"

if ! command -v git >/dev/null 2>&1; then
  echo "error: git required to record CODE_SHA" >&2
  exit 1
fi

FULL_SHA="$(git rev-parse HEAD)"
SHORT_SHA="$(git rev-parse --short=8 HEAD)"
STAGING="$(mktemp -d "${TMPDIR:-/tmp}/diptych-artifact.XXXXXX")"
BUNDLE="DIPTYCH-${SHORT_SHA}"
DEST="${STAGING}/${BUNDLE}"
mkdir -p "$DEST"/{coverage,examples/poc,paper,adapters,docs/images,scripts,artifact}

cleanup() { rm -rf "$STAGING"; }
trap cleanup EXIT

# --- CODE SHA ---
printf '%s\n' "$FULL_SHA" >"$DEST/CODE_SHA.txt"
printf 'short=%s\nfull=%s\n' "$SHORT_SHA" "$FULL_SHA" >"$DEST/CODE_SHA.txt"

# --- Required harness facts ---
require_file() {
  local f="$1"
  if [[ ! -e "$ROOT/$f" ]]; then
    echo "error: missing required path: $f" >&2
    exit 1
  fi
}

require_file "coverage/matrix.json"
require_file "examples/poc/expected_matrix_snippet.json"
require_file "adapters/PINS.md"
require_file "paper/SUBMISSION.md"
require_file "paper/ARTIFACT_CHECKLIST.md"
require_file "paper/READINESS.md"
require_file "LICENSE"
require_file "README.md"
require_file "CITATION.cff"
require_file "scripts/run_poc.sh"

cp -a "$ROOT/coverage/matrix.json" "$DEST/coverage/"
cp -a "$ROOT/examples/poc/expected_matrix_snippet.json" "$DEST/examples/poc/"
cp -a "$ROOT/adapters/PINS.md" "$DEST/adapters/"
cp -a "$ROOT/LICENSE" "$DEST/"
cp -a "$ROOT/README.md" "$DEST/"
cp -a "$ROOT/CITATION.cff" "$DEST/"
cp -a "$ROOT/scripts/run_poc.sh" "$DEST/scripts/"
cp -a "$ROOT/scripts/print_matrix.py" "$DEST/scripts/" 2>/dev/null || true
cp -a "$ROOT/scripts/pack_artifact.sh" "$DEST/scripts/"

# Key paper / protocol docs (no fabricated scores)
for f in READINESS.md SUBMISSION.md ARTIFACT_CHECKLIST.md NOTES.md README.md \
         RQ_PROTOCOL.md ANON.md WITNESSES.md one-trace-is-not-enough.tex refs.bib; do
  if [[ -f "$ROOT/paper/$f" ]]; then
    cp -a "$ROOT/paper/$f" "$DEST/paper/"
  fi
done

# Figures if present
if [[ -d "$ROOT/docs/images" ]]; then
  cp -a "$ROOT/docs/images/"*.png "$DEST/docs/images/" 2>/dev/null || true
  cp -a "$ROOT/docs/images/"*.svg "$DEST/docs/images/" 2>/dev/null || true
fi

# Optional: include PDF if already built locally
PDF_NOTE="PDF not bundled (paper/one-trace-is-not-enough.pdf absent)."
if [[ -f "$ROOT/paper/one-trace-is-not-enough.pdf" ]]; then
  cp -a "$ROOT/paper/one-trace-is-not-enough.pdf" "$DEST/paper/"
  PDF_NOTE="PDF included: paper/one-trace-is-not-enough.pdf (local build)."
fi

# Minimal stranger path: PoC script expects full tree for run_full8.
# Pack core harness trees needed to reproduce greenx8x3 offline.
for d in diptych ops controls diptych-probes tests docs/adapters; do
  if [[ -e "$ROOT/$d" ]]; then
    mkdir -p "$DEST/$(dirname "$d")"
    cp -a "$ROOT/$d" "$DEST/$d"
  fi
done
# Root helpers used by PoC / make
for f in Makefile pyproject.toml PAPER_OUTLINE.md; do
  [[ -f "$ROOT/$f" ]] && cp -a "$ROOT/$f" "$DEST/"
done

# ARTIFACT_NOTES: how to attach CI PDF + non-claims
cat >"$DEST/ARTIFACT_NOTES.txt" <<EOF
DIPTYCH artifact bundle
=======================
CODE_SHA (full): $FULL_SHA
CODE_SHA (short): $SHORT_SHA
Packed-at-UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ)

$PDF_NOTE

How to attach the CI PDF (when not bundled)
------------------------------------------
1. Open https://github.com/pandeyaby/DIPTYCH/actions
2. Pick a green run on this SHA (or main after merge)
3. Download workflow artifact: one-trace-is-not-enough-pdf
4. Place one-trace-is-not-enough.pdf under paper/ in this zip
   (or submit alongside the zip per venue instructions)

Reproduce offline (no GPU / no network for core PoC)
----------------------------------------------------
./scripts/run_poc.sh
# expect exit 0; MATRIX CHECK OK (greenx8x3)

Non-claims
----------
- greenx8x3 = harness coverage + gate_axis_mutate only
- No AUROC / model_grade / invented model scores
- inconclusive != green
- ZeroDay / AOMB product trees are NOT vendored (pins only: adapters/PINS.md)

See paper/READINESS.md, paper/SUBMISSION.md, and paper/ARTIFACT_CHECKLIST.md.
License: LICENSE (MIT). Cite: CITATION.cff (https://github.com/pandeyaby/DIPTYCH; no DOI yet).
EOF

# Capture a short matrix snapshot into artifact/ (not a PoC log substitute)
python3 - <<'PY' >"$DEST/artifact/matrix_snapshot.txt" || true
import json
from pathlib import Path
m = json.loads(Path("coverage/matrix.json").read_text(encoding="utf-8"))
print("source_row:", m.get("source_row"))
ops = m.get("operators", {})
for op in sorted(ops):
    cell = ops[op]
    print(f"{op}: core={cell.get('diptych_core')} zd={cell.get('zeroday')} aomb={cell.get('aomb')} axis={cell.get('axis_power')}")
print("non-claim: categorical green + boolean axis_power only")
PY

ZIP_PATH="${OUT_DIR}/${BUNDLE}.zip"
rm -f "$ZIP_PATH" "${OUT_DIR}/${BUNDLE}.tar.gz"
(
  cd "$STAGING"
  if command -v zip >/dev/null 2>&1; then
    zip -qr "$ZIP_PATH" "$BUNDLE"
  else
    tar -czf "${OUT_DIR}/${BUNDLE}.tar.gz" "$BUNDLE"
  fi
)

if [[ -f "$ZIP_PATH" ]]; then
  :
elif [[ -f "${OUT_DIR}/${BUNDLE}.tar.gz" ]]; then
  ZIP_PATH="${OUT_DIR}/${BUNDLE}.tar.gz"
else
  echo "error: failed to write archive" >&2
  exit 1
fi

echo "Wrote $ZIP_PATH"
echo "CODE_SHA=$FULL_SHA"
echo "$PDF_NOTE"

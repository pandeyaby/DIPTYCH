.PHONY: poc poc-json grade test full8 matrix refresh-matrix schema schema-check pins-check smoke paper artifact clean

poc:
	./scripts/run_poc.sh

# Machine-readable stranger path: all 8 ops × conf/viol + gate_axis_mutate → JSON
# Optional SARIF: make poc-json SARIF=1
poc-json:
	./scripts/run_poc.sh --json $(if $(SARIF),--sarif,)

# Unified stranger smoke: schema freshness → pins --check → cassette grade →
# matrix --check → coupling_check → probe-tree → separation → ablation →
# amortization → inconclusive → poc json → thin reject.
# Exit non-zero on any failure.
# Machine report: make smoke JSON=1   OR   python -m diptych smoke --json
smoke:
	PYTHONPATH=. python3 -m diptych smoke $(if $(JSON),--json,)

# Adapter pin integrity alone (also folded into `make smoke`).
# CI / PRs: `make pins-check` or `python -m diptych.pins --check`
pins-check:
	PYTHONPATH=. python3 -m diptych.pins --check

# Adapter cassette/fixture ingest: grade CONTRACT JSON from disk (no product imports)
# Usage: make grade INPUT=examples/fixtures/zeroday/RESEED
# Optional: WITH_AXIS=1 make grade INPUT=examples/fixtures/aomb/SIGNFLIP/conforming
# Optional SARIF: SARIF=1 make grade INPUT=…   (or --format sarif)
grade:
	@test -n "$(INPUT)" || (echo "usage: make grade INPUT=path/to/probe.json|dir"; exit 2)
	PYTHONPATH=. python3 -m diptych.grade --input "$(INPUT)" \
		$(if $(WITH_AXIS),--with-axis-mutate,) \
		$(if $(SARIF),--sarif,)

test:
	@if python3 -c "import pytest" 2>/dev/null; then \
		python3 -m pytest -q; \
	else \
		PYTHONPATH=. python3 -m unittest discover -s tests -v; \
	fi

full8:
	PYTHONPATH=. python3 -m diptych.run_full8

matrix: full8
	@python3 scripts/print_matrix.py


# Derive/verify coverage/matrix.json diptych_core from fixture grades (adapter pins untouched).
# Check (CI): python -m diptych.matrix --check
# Write:      make refresh-matrix   OR   python -m diptych.matrix --write
refresh-matrix:
	PYTHONPATH=. python3 -m diptych.matrix --write
	@python3 scripts/print_matrix.py


# Executable CONTRACT v0.2 JSON Schema (adapters validate without prose).
# Check:  make schema-check   OR   python -m diptych.schema --check PATH
# Dump:   make schema         OR   python -m diptych.schema --dump-schema
schema:
	PYTHONPATH=. python3 -m diptych.schema --dump-schema
	PYTHONPATH=. python3 -m diptych.schema --check-fresh

schema-check:
	PYTHONPATH=. python3 -m diptych.schema --check-fresh
	PYTHONPATH=. python3 -m diptych.schema --check examples/fixtures/cassette
	PYTHONPATH=. python3 -m diptych.schema --check diptych-probes


# Build IEEEtran PDF when latexmk + TeX Live (IEEEtran) are installed.
# CI fallback: .github/workflows/ci.yml job "paper-pdf" uploads the artifact
# when local TeX is absent — download from the Actions run, do not invent scores.
paper:
	@if command -v latexmk >/dev/null 2>&1; then \
		cd paper && latexmk -pdf -interaction=nonstopmode -halt-on-error one-trace-is-not-enough.tex; \
	else \
		echo "make paper: latexmk not found."; \
		echo "  Install TeX Live (IEEEtran + latexmk), or use CI job 'paper-pdf'"; \
		echo "  (.github/workflows/ci.yml) which uploads one-trace-is-not-enough-pdf."; \
		echo "  See paper/SUBMISSION.md and paper/README.md."; \
		exit 1; \
	fi

# Reviewer-facing zip (SHA, matrix, PoC snippet, key docs). See paper/SUBMISSION.md.
# Optional: run `make paper` first to bundle the PDF; else ARTIFACT_NOTES.txt
# explains how to attach CI artifact one-trace-is-not-enough-pdf.
artifact:
	./scripts/pack_artifact.sh

clean:
	rm -rf reports/paired-probes dist __pycache__ diptych/__pycache__ tests/__pycache__
	@cd paper && latexmk -C one-trace-is-not-enough.tex >/dev/null 2>&1 || true

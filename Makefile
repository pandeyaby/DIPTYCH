.PHONY: poc poc-json grade test full8 matrix paper artifact clean

poc:
	./scripts/run_poc.sh

# Machine-readable stranger path: all 8 ops × conf/viol + gate_axis_mutate → JSON
# Optional SARIF: make poc-json SARIF=1
poc-json:
	./scripts/run_poc.sh --json $(if $(SARIF),--sarif,)

# Adapter cassette/fixture ingest: grade CONTRACT JSON from disk (no product imports)
# Usage: make grade INPUT=examples/fixtures/zeroday/RESEED
# Optional: WITH_AXIS=1 make grade INPUT=examples/fixtures/aomb/SIGNFLIP/conforming
grade:
	@test -n "$(INPUT)" || (echo "usage: make grade INPUT=path/to/probe.json|dir"; exit 2)
	PYTHONPATH=. python3 -m diptych.grade --input "$(INPUT)" $(if $(WITH_AXIS),--with-axis-mutate,)

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

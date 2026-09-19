.PHONY: poc test full8 matrix paper clean

poc:
	./scripts/run_poc.sh

test:
	PYTHONPATH=. python3 -m unittest discover -s tests -v

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

clean:
	rm -rf reports/paired-probes __pycache__ diptych/__pycache__ tests/__pycache__
	@cd paper && latexmk -C one-trace-is-not-enough.tex >/dev/null 2>&1 || true

.PHONY: poc test full8 matrix clean

poc:
	./scripts/run_poc.sh

test:
	PYTHONPATH=. python3 -m unittest discover -s tests -v

full8:
	PYTHONPATH=. python3 -m diptych.run_full8

matrix: full8
	@python3 -c 'import json;print(json.load(open("coverage/matrix.json"))["source_row"])'

clean:
	rm -rf reports/paired-probes __pycache__ diptych/__pycache__ tests/__pycache__

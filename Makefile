PY := .venv/bin/python

.PHONY: setup esco data golden twins engine eval test-hard report verify serve demo

setup:            ## create venv and install pinned deps
	uv venv -q .venv && uv pip install -q -p $(PY) -r requirements.txt

esco:             ## refresh the frozen ESCO cache (network)
	$(PY) data/esco_fetch.py

data:             ## regenerate the synthetic world from frozen inputs (seed 42)
	$(PY) data/generate.py --seed 42 --out data/out

golden:           ## stratified golden-set selection (annotation runs separately)
	$(PY) eval/golden.py select

test-hard:        ## hard gates, JUnit XML for the report
	@mkdir -p eval/results
	$(PY) -m pytest -m hard -q --junitxml=eval/results/hard.xml

twins:            ## translation / style / injection fixtures from the frozen bank
	$(PY) data/twins.py

engine:           ## step-2 pipeline (LLM calls replay from data/llm_cache; new calls need SPARK_ANTHROPIC_API_KEY)
	$(PY) -m engine.run extract && $(PY) -m engine.run judge && $(PY) -m engine.run lens && $(PY) -m engine.run stability && $(PY) -m engine.run injection

eval:             ## F4-F7 targets -> eval/results/targets_f4_f7.json
	$(PY) eval/run_eval.py

report:
	$(PY) reports/build_f0.py && $(PY) reports/build_f7.py

verify:           ## hard gates -> report; exits non-zero if any hard gate is red
	@mkdir -p eval/results
	$(PY) eval/run_eval.py && $(PY) eval/xfamily.py score && $(PY) reports/build_f7.py  # fresh targets (+ X1 scores, offline); the page-identity gate reads the fresh page
	$(PY) -m pytest -m hard -q --junitxml=eval/results/hard.xml; s=$$?; $(PY) reports/build_f0.py; $(PY) reports/build_f7.py; exit $$s

serve:            ## proof pages at http://127.0.0.1:8777/reports/
	$(PY) -m http.server 8777 --bind 127.0.0.1

demo:             ## offline Match Lens demo (replays stored results; no API key, no network). NEED=N-014 by default
	$(PY) demo.py $(NEED)

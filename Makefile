PY := .venv/bin/python

.PHONY: setup esco data golden twins engine eval step4 step5 step5-review browser-step5 load-step5 test-hard report verify serve serve-app serve-streamlit demo

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
	$(PY) reports/build_f0.py && $(PY) reports/build_f7.py && $(PY) reports/build_f3.py && $(PY) reports/build_f8.py && $(PY) reports/build_f16.py

step4:            ## F8 blind-payload and two-sided-reveal checks (offline)
	SPARK_ENGINE=d1 $(PY) eval/run_step4.py

step5:            ## F9-F16 offline synthetic workflow audit; reads the latest browser/load/review evidence
	SPARK_ENGINE=d1 $(PY) eval/run_step5.py && $(PY) reports/build_f16.py

step5-review:     ## create a blank human review sheet from the latest F9-F16 review packet
	$(PY) eval/step5_reviews.py prepare

browser-step5:    ## browser cache, expiry, pulse-sync and third-party request capture (app must be running)
	$(PY) eval/browser_step5.py

load-step5:       ## 1,000 concurrent pass requests (app must be running)
	$(PY) eval/load_step5.py

verify:           ## existing hard gates and F9-F16 audit -> updated release reports
	@mkdir -p eval/results
	$(PY) eval/run_eval.py && SPARK_ENGINE=d1 $(PY) eval/run_step4.py && SPARK_ENGINE=d1 $(PY) eval/run_step5.py && $(PY) reports/build_f7.py  # fresh targets; the page-identity gate reads the freshly built page
	$(PY) -m pytest -m hard -q --ignore=eval/tests/test_f1_f3.py --junitxml=eval/results/hard.xml; s=$$?; \
	SPARK_ENGINE=d1 $(PY) -m pytest -m hard -q eval/tests/test_f4_f7.py eval/tests/test_f1_f3.py --junitxml=eval/results/hard_d1.xml; t=$$?; \
	SPARK_ENGINE=d1 $(PY) -m pytest -m hard -q eval/tests/test_f8.py --junitxml=eval/results/hard_f8.xml; u=$$?; \
	$(PY) reports/build_f0.py; $(PY) reports/build_f7.py; $(PY) reports/build_f3.py; $(PY) reports/build_f8.py; $(PY) reports/build_f16.py; test $$s -eq 0 -a $$t -eq 0 -a $$u -eq 0

serve-app:        ## web app (Need Studio, Skill Story, Blind View, Event Console) on engine d1
	SPARK_ENGINE=d1 $(PY) -m uvicorn api.main:app --host 127.0.0.1 --port 8778

serve-streamlit:  ## Streamlit shell plus the existing Spark pages at http://localhost:8501
	SPARK_ENGINE=d1 $(PY) -m streamlit run streamlit_app.py --server.headless true

serve:
	$(PY) -m http.server 8777 --bind 127.0.0.1

demo:             ## offline Match Lens demo (replays stored results; no API key, no network). NEED=N-014 by default
	$(PY) demo.py $(NEED)

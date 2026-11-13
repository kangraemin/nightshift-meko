SEED ?= 1
MODEL ?= qwen3:8b

.PHONY: setup test demo-offline demo dashboard diagram

setup:
	pip install -r requirements.txt
	ollama pull $(MODEL)

# No accounts needed: in-process Meko stand-in + SQLite ledger, same agents and simulator.
demo-offline:
	NIGHTSHIFT_MODEL=$(MODEL) python3 -m nightshift.run --offline --seed $(SEED)

# Real Meko (two members sharing one datapack) + AMP Postgres ledger. See README "Live run".
demo:
	NIGHTSHIFT_MODEL=$(MODEL) python3 -m nightshift.run --seed $(SEED)

# Replay any run in the browser: http://localhost:8000/dashboard/?run=../results/<run_id>/events.jsonl
dashboard:
	python3 -m http.server 8000

diagram:
	python3 scripts/architecture.py

# Guardrail and ledger tests: no model, no network, under a second
test:
	python3 -m pytest -q tests

.PHONY: venv index test quality negative build up down smoke local-check

PYTHON ?= python3.12
SEARCH_PORT ?= 18080
VENV := .venv
BIN := $(VENV)/bin

venv: $(VENV)/.requirements-installed

$(VENV)/.requirements-installed: requirements.txt
	$(PYTHON) -m venv $(VENV)
	$(BIN)/python -m pip install -r requirements.txt
	touch $@

index: venv
	$(BIN)/python scripts/build_index.py --output artifacts

test: index
	$(BIN)/python -m pytest -q

quality: index
	$(BIN)/python scripts/evaluate.py --artifact-dir artifacts > artifacts/quality.json
	cat artifacts/quality.json

negative: index
	@status=0; \
	$(BIN)/python scripts/evaluate.py --artifact-dir artifacts --ranking-mode reverse \
		> artifacts/quality-reverse.json || status=$$?; \
	cat artifacts/quality-reverse.json; \
	if [ "$$status" -ne 2 ]; then \
		echo "Expected quality gate to reject reverse with exit 2, got $$status" >&2; exit 1; \
	fi; \
	echo "Reverse candidate blocked by quality gate"

build:
	docker compose build search

up:
	docker compose up -d --build search

down:
	docker compose down

smoke:
	$(PYTHON) scripts/smoke_local.py --base-url "http://127.0.0.1:$(SEARCH_PORT)"

local-check: test quality negative build

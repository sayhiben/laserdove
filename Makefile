PYTHON ?= python3
RUFF ?= ruff
DEFAULT_PATHS := src tests tools

.PHONY: lint format test

lint:
	$(RUFF) check $(DEFAULT_PATHS)
	$(RUFF) format --check $(DEFAULT_PATHS)

format:
	$(RUFF) format $(DEFAULT_PATHS)

test: lint
	$(PYTHON) -m pytest

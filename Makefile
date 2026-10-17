UV ?= uv

.PHONY: install run lint format test build

install:
	$(UV) sync --locked --dev
run:
	$(UV) run valutatrade
lint:
	$(UV) run ruff check .
format:
	$(UV) run ruff format .
test:
	$(UV) run python -m unittest discover -s tests -v
build:
	$(UV) build

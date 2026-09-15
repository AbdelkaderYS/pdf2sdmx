.PHONY: install install-ml lint fix test run api ui refresh evaluate figures docker clean

install:
	uv pip install --system -e ".[dev]"

install-ml:
	uv pip install --system --extra-index-url https://download.pytorch.org/whl/cpu -e ".[dev,ml]"

lint:
	ruff check src tests scripts
	ruff format --check src tests scripts

fix:
	ruff check --fix src tests scripts
	ruff format src tests scripts

test:
	pytest

run:
	python app.py

api:
	uvicorn pdf2sdmx.api.main:app --reload --port 8000

ui:
	python -m pdf2sdmx.ui.app

refresh:
	python -m pdf2sdmx.core.ingest.refresh

evaluate:
	python scripts/evaluate.py

figures:
	python scripts/make_figures.py

docker:
	docker compose up --build

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__

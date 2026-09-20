.PHONY: install install-ml schemas reference harvest audit lint fix test run api ui refresh evaluate docker clean

install:
	uv pip install --system -e ".[dev]"

install-ml:
	uv pip install --system --extra-index-url https://download.pytorch.org/whl/cpu -e ".[dev,ml]"

schemas:
	python -c "import sdmx; print(sdmx.install_schemas(version='2.1'))"

reference:
	python -c "from pdf2sdmx.core import registry; print(registry.download_all(refresh=True))"

harvest:
	python scripts/harvest_vocabulary.py data/raw/*.pdf

audit:
	python scripts/audit_against_portal.py

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


docker:
	docker compose up --build

clean:
	rm -rf .pytest_cache .ruff_cache **/__pycache__

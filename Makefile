PYTHON ?= python

.PHONY: install lint test validate verify dashboard evidence-live evidence-offline

install:
	$(PYTHON) -m pip install -e ".[dev,dashboard]"

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .

test:
	$(PYTHON) -m pytest -q

validate:
	$(PYTHON) scripts/validate_delivery.py

verify: lint test validate

dashboard:
	$(PYTHON) -m streamlit run dashboard/app.py

evidence-live:
	$(PYTHON) scripts/generate_evidence.py --mode live

evidence-offline:
	$(PYTHON) scripts/generate_evidence.py --mode offline

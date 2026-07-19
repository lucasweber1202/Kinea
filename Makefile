PYTHON ?= python

.PHONY: install lint test validate verify dashboard panel-demo contract-live evidence-live evidence-offline

install:
	$(PYTHON) -m pip install -e ".[dev,dashboard,modeling]"

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

panel-demo:
	$(PYTHON) -m kinea.cli panel --db evidence/revision_demo.db \
		--as-of 2026-07-10,2026-07-18 --series CZ_HICP_CORE_INDEX \
		--format parquet --output /tmp/kinea-pit-panel.parquet

contract-live:
	$(PYTHON) scripts/check_source_contract.py

evidence-live:
	$(PYTHON) scripts/generate_evidence.py --mode live

evidence-offline:
	$(PYTHON) scripts/generate_evidence.py --mode offline

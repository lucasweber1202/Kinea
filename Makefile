PYTHON ?= python

.PHONY: install lint test validate verify dashboard panel-demo feature-demo diff-demo dry-run quality source-health revisions-demo contract-live evidence-live evidence-offline

install:
	$(PYTHON) -m pip install -e ".[dev,dashboard,modeling]"

lint:
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m ruff check .
	$(PYTHON) -m mypy

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

feature-demo:
	$(PYTHON) -m kinea.cli features --db evidence/kinea.db \
		--as-of 2026-07-18 --format csv --output /tmp/kinea-features.csv

diff-demo:
	$(PYTHON) -m kinea.cli diff --db evidence/revision_demo.db \
		--from 2026-07-10 --to 2026-07-18 --series CZ_HICP_CORE_INDEX

dry-run:
	$(PYTHON) -m kinea.cli collect --mode live --db evidence/kinea.db --months 3 --dry-run

quality:
	$(PYTHON) -m kinea.cli quality --db evidence/kinea.db --as-of 2026-07-19

source-health:
	$(PYTHON) -m kinea.cli source-health --db evidence/kinea.db --as-of 2026-07-19

revisions-demo:
	$(PYTHON) -m kinea.cli revisions --db evidence/revision_demo.db

contract-live:
	$(PYTHON) scripts/check_source_contract.py

evidence-live:
	$(PYTHON) scripts/generate_evidence.py --mode live

evidence-offline:
	$(PYTHON) scripts/generate_evidence.py --mode offline

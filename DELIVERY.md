# Delivery handoff

This repository is the review-ready submission for the Kinea internship data-collector
assignment. Release tag `v2.2.0` identifies the delivered source tree; `SHA256SUMS` distributed
with the release verifies the downloadable ZIP and Git bundle.
The installable Python package uses the same `2.2.0` version, avoiding separate release/package
numbering.

## Five-minute review

```bash
python -m pip install -e ".[dev,dashboard,modeling]"
python -m pytest -q
python scripts/validate_delivery.py
python -m streamlit run dashboard/app.py
```

Expected validator result: `DELIVERY STATUS: READY`. The committed database makes these checks
network-independent.

## Delivered result

- Source: public, keyless ECB SDMX API.
- Scope: four Czech HICP component indices plus the daily EUR/CZK reference rate.
- Database: SQLite with exactly `metadata`, `time_series`, and `logs`.
- Live evidence: 5 series and 8,327 current observations, with all five latest samples matched
  against raw ECB responses.
- Vintage behavior: first observation, unchanged re-run, later revision, and same-day correction.
- Modeling interface: CSV/Parquet/Feather point-in-time panels with explicit knowledge dates and
  a validator-backed no-look-ahead demonstration.
- Data trust: pre-ingest range, cadence, jump, future-date, and staleness checks with a committed
  report and run-level quality status.
- Source drift protection: five real ECB golden fixtures plus a separately scheduled live contract.
- Analysis toolkit: canonical YoY, MoM, three-month annualized and rebased transformations, plus
  set-based revision magnitude/direction/observed-lag analytics without adding database tables.
- Production toolkit: selective/ranged collection, dry-run plans, raw payload SHA-256 archives,
  execution lock, short write transactions, batched vintage ingest, Retry-After support, structured
  per-series metrics, snapshot diff/export, source health and publication-lag reporting.
- Modeling features: vintage-safe wide/long feature matrices with monthly HICP changes and
  mixed-frequency EUR/CZK aggregation.
- Presentation: six Streamlit sections, seven CSV exports, source coverage, vintages, as-of view,
  and execution audit.
- Automated quality gate: 111 tests, Ruff formatting/lint, Python 3.11/3.12 CI matrix, and a
  fail-closed delivery validator.

## Evidence map

| Requirement | Evidence |
|---|---|
| Populated database | `evidence/kinea.db` |
| Exact schema | `kinea/db.py`, `evidence/schema.sql` |
| Idempotency | `evidence/idempotency.txt` |
| Two coexisting vintages | `evidence/revision_demo.db`, `evidence/revision_demo.txt` |
| Historical as-of result | `evidence/as_of_demo.txt` |
| Backtest-ready PIT panel | `evidence/pit_panel.csv`, `evidence/pit_panel.parquet` |
| Vintage-safe features | `evidence/feature_panel.csv` |
| Snapshot difference | `evidence/as_of_diff.txt` |
| Revision analytics | `kinea/analytics.py`, `python -m kinea.cli revisions` |
| Canonical derived views | `kinea/transforms.py`, HICP dashboard controls and heatmap |
| Success and error logging | `evidence/success_log.txt`, `evidence/error_log.txt` |
| Live source comparison | `evidence/live_validation.txt` |
| Machine-verifiable live proof | `evidence/live_validation.json` |
| Operational health | `evidence/source_health.txt` |
| Publication/observation lag | `evidence/publication_lag.txt` |
| Semantic data quality | `evidence/data_quality.txt` |
| Automated final audit | `evidence/validation_report.txt` |
| Presentation captures | `docs/dashboard-*.png` |

## Operational safeguards

- Fatal collection failures roll back partial series data and still create one error log row.
- Network fetch and validation complete before `BEGIN IMMEDIATE`; the write lock is held only for
  the batched database mutation.
- The CLI uses a per-database file lock to reject overlapping scheduled runs.
- Evidence generation is staged and validated before it atomically replaces the last-known-good
  delivery.
- Offline fixtures write to an isolated directory and cannot replace the committed live database.
- Secrets are not required. `.env` is ignored; `.env.example` documents the optional endpoint
  override.
- GitHub Actions tests Python 3.11 and 3.12, checks formatting and lint, runs the dashboard
  contract, property-based invariants, and validates committed evidence.
- A weekly opt-in network job detects external-ID or SDMX-column drift without making ordinary
  pull requests depend on ECB availability.

## Known limitation

The ECB does not announce every revised historical point. A bounded `--months` collection detects
revisions only inside that window; run an unbounded collection when complete revision discovery is
required. The labelled simulated revision is isolated in `evidence/revision_demo.db` and never
changes official values in `evidence/kinea.db`.

## Release checklist

- [x] Required three-table schema and structured IDs
- [x] Idempotent second run
- [x] Four vintage rules and as-of query
- [x] Success and error logs
- [x] Live populated database and raw-response comparison
- [x] Dashboard, downloads, and screenshots
- [x] Point-in-time panel export for honest backtests
- [x] Semantic quality gate and report
- [x] Golden and scheduled live source contracts
- [x] Property-based vintage invariants and float-noise tolerance
- [x] Tested derived transformations and set-based revision analytics
- [x] Snapshot diff/export and vintage-safe feature matrix
- [x] Selective/ranged/dry-run collection and raw payload archive
- [x] Short transaction, batched ingest, execution lock and Retry-After handling
- [x] Source-health and publication-lag reporting
- [x] Automated tests and fail-closed delivery validator
- [x] Reproducible ZIP, Git bundle, checksums, and GitHub tag

# Delivery handoff

This repository is the review-ready submission for the Kinea internship data-collector
assignment. Release tag `v1.0.0` identifies the delivered source tree; `SHA256SUMS` distributed
with the release verifies the downloadable ZIP and Git bundle.

## Five-minute review

```bash
python -m pip install -e ".[dev,dashboard]"
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
- Presentation: six Streamlit sections, seven CSV exports, source coverage, vintages, as-of view,
  and execution audit.
- Automated quality gate: 58 tests, Ruff formatting/lint, Python 3.11/3.12 CI matrix, and a
  fail-closed delivery validator.

## Evidence map

| Requirement | Evidence |
|---|---|
| Populated database | `evidence/kinea.db` |
| Exact schema | `kinea/db.py`, `evidence/schema.sql` |
| Idempotency | `evidence/idempotency.txt` |
| Two coexisting vintages | `evidence/revision_demo.db`, `evidence/revision_demo.txt` |
| Historical as-of result | `evidence/as_of_demo.txt` |
| Success and error logging | `evidence/success_log.txt`, `evidence/error_log.txt` |
| Live source comparison | `evidence/live_validation.txt` |
| Automated final audit | `evidence/validation_report.txt` |
| Presentation captures | `docs/dashboard-*.png` |

## Operational safeguards

- Fatal collection failures roll back partial series data and still create one error log row.
- Evidence generation is staged and validated before it atomically replaces the last-known-good
  delivery.
- Offline fixtures write to an isolated directory and cannot replace the committed live database.
- Secrets are not required. `.env` is ignored; `.env.example` documents the optional endpoint
  override.
- GitHub Actions tests Python 3.11 and 3.12, checks formatting and lint, runs the dashboard
  contract, and validates committed evidence.

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
- [x] Automated tests and fail-closed delivery validator
- [x] Reproducible ZIP, Git bundle, checksums, and GitHub tag

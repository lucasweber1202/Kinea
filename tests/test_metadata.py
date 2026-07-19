from pathlib import Path

import pytest

from kinea.client import OfflineClient
from kinea.collector import collect
from kinea.config import load_config
from kinea.db import connect
from kinea.identifiers import derive_description, derive_name


ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def populated():
    conn = connect(":memory:")
    collect(
        conn, load_config(), OfflineClient(ROOT / "fixtures" / "v2"),
        collected_at="2026-07-18T10:00:00+00:00",
    )
    return conn


def test_exactly_five_metadata_rows(populated):
    assert populated.execute("SELECT COUNT(*) FROM metadata").fetchone()[0] == 5


def test_every_fact_has_metadata(populated):
    missing = populated.execute(
        """
        SELECT COUNT(*) FROM time_series t
        LEFT JOIN metadata m ON m.series_id=t.series_id
        WHERE m.series_id IS NULL
        """
    ).fetchone()[0]
    assert missing == 0


def test_metadata_coverage_is_computed_from_time_series(populated):
    for row in populated.execute("SELECT * FROM metadata"):
        computed = populated.execute(
            """
            SELECT MIN(reference_date), MAX(reference_date), COUNT(DISTINCT reference_date)
            FROM time_series WHERE series_id=?
            """,
            (row["series_id"],),
        ).fetchone()
        assert (row["first_observation"], row["last_observation"], row["observation_count"]) == tuple(computed)


def test_names_and_descriptions_are_derived(populated):
    for row in populated.execute("SELECT series_id, name, description FROM metadata"):
        assert row["name"] == derive_name(row["series_id"])
        assert row["description"] == derive_description(row["series_id"])


def test_frequency_unit_country_are_correct(populated):
    rows = populated.execute("SELECT series_id, country, frequency, unit FROM metadata").fetchall()
    assert all(row["country"] == "CZ" for row in rows)
    assert {row["frequency"] for row in rows if "HICP" in row["series_id"]} == {"monthly"}
    assert {row["unit"] for row in rows if "HICP" in row["series_id"]} == {"index"}
    fx = next(row for row in rows if "_FX_" in row["series_id"])
    assert (fx["frequency"], fx["unit"]) == ("daily", "currency")

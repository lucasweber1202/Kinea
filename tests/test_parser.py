import pytest

from kinea.parser import normalize_reference_date, parse_sdmx_csv


def test_normalizes_month_to_date():
    assert normalize_reference_date("2026-07") == "2026-07-01"


def test_parses_daily_observation():
    result = parse_sdmx_csv(
        "KEY,TIME_PERIOD,OBS_VALUE\nEXR.D.CZK.EUR.SP00.A,2026-07-18,24.51\n",
        expected_external_id="EXR.D.CZK.EUR.SP00.A",
    )
    assert result.observations[0].reference_date == "2026-07-18"
    assert result.observations[0].value == 24.51


def test_bad_record_warns_and_good_record_survives():
    text = "KEY,TIME_PERIOD,OBS_VALUE\nX,not-a-date,abc\nX,2026-06,101.2\n"
    with pytest.warns(RuntimeWarning):
        result = parse_sdmx_csv(text, expected_external_id="X")
    assert len(result.observations) == 1
    assert len(result.warnings) == 1


def test_wrong_external_key_is_skipped():
    text = "KEY,TIME_PERIOD,OBS_VALUE\nWRONG,2026-06,101.2\n"
    with pytest.warns(RuntimeWarning):
        result = parse_sdmx_csv(text, expected_external_id="EXPECTED")
    assert result.observations == ()

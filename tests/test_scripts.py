import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import generate_evidence

ROOT = Path(__file__).resolve().parent.parent


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_offline_evidence_is_isolated_from_committed_live_database(tmp_path):
    live_database = ROOT / "evidence" / "kinea.db"
    before = _sha256(live_database)
    output = tmp_path / "offline-evidence"

    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "generate_evidence.py"),
            "--mode",
            "offline",
            "--output-dir",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )

    assert _sha256(live_database) == before
    assert "Generated isolated offline evidence" in completed.stdout
    assert (output / "kinea.db").exists()
    assert (output / "revision_demo.db").exists()
    assert "Status: PENDING" in (output / "live_validation.txt").read_text()
    assert not (output / "validation_report.txt").exists()


def test_validated_evidence_promotion_replaces_previous_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(generate_evidence, "ROOT", tmp_path)
    target = tmp_path / "evidence"
    target.mkdir()
    (target / "marker.txt").write_text("old", encoding="utf-8")
    staged = tmp_path / "staged"
    staged.mkdir()
    (staged / "marker.txt").write_text("new", encoding="utf-8")

    generate_evidence._promote_evidence(staged, target)

    assert (target / "marker.txt").read_text(encoding="utf-8") == "new"
    assert not staged.exists()


def test_failed_evidence_promotion_restores_previous_delivery(tmp_path, monkeypatch):
    monkeypatch.setattr(generate_evidence, "ROOT", tmp_path)
    target = tmp_path / "evidence"
    target.mkdir()
    (target / "marker.txt").write_text("last-known-good", encoding="utf-8")
    missing_staged = tmp_path / "missing-staged"

    with pytest.raises(FileNotFoundError):
        generate_evidence._promote_evidence(missing_staged, target)

    assert (target / "marker.txt").read_text(encoding="utf-8") == "last-known-good"

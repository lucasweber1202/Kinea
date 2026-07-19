import hashlib
import subprocess
import sys
from pathlib import Path


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

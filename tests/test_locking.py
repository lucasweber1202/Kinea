from __future__ import annotations

import pytest

from kinea.locking import RunLockError, execution_lock


def test_execution_lock_blocks_a_second_collector(tmp_path):
    database = tmp_path / "collector.db"
    with execution_lock(database):
        with pytest.raises(RunLockError, match="already running"):
            with execution_lock(database):
                pass


def test_execution_lock_can_be_reacquired_after_release(tmp_path):
    database = tmp_path / "collector.db"
    with execution_lock(database) as first:
        assert first and first.exists()
    with execution_lock(database) as second:
        assert second == first

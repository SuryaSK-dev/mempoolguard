"""Unit tests for system health checks (core/health.py)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from core.config import AppConfig
from core.database import Database
from core.health import (
    check_database,
    check_python_environment,
    check_rpc_node,
    check_storage_directories,
    run_all_health_checks,
)


def test_python_environment_check() -> None:
    """Ensure host Python version meets >= 3.11 requirement."""
    res = check_python_environment()
    assert res["status"] == "HEALTHY"
    assert res["is_supported"] is True
    assert "python_version" in res


def test_database_health_check(in_memory_db: Database) -> None:
    """Ensure database health check succeeds against initialized DB."""
    res = check_database(in_memory_db)
    assert res["status"] == "HEALTHY"
    assert "table_counts" in res
    assert res["table_counts"]["raw_transactions"] == 0


def test_rpc_node_check_offline() -> None:
    """Ensure offline RPC endpoint returns DEGRADED gracefully without raising."""
    res = check_rpc_node("http://127.0.0.1:59999", timeout_seconds=0.5)
    assert res["status"] == "DEGRADED"
    assert res["connected"] is False
    assert "message" in res


def test_storage_directories_check() -> None:
    """Ensure storage directory creation and write verification succeeds."""
    with tempfile.TemporaryDirectory() as tmpdir:
        test_dir1 = Path(tmpdir) / "dir1"
        test_dir2 = Path(tmpdir) / "dir2"
        res = check_storage_directories([test_dir1, test_dir2])
        assert res["status"] == "HEALTHY"
        assert test_dir1.exists()
        assert test_dir2.exists()


def test_run_all_health_checks(in_memory_db: Database, app_config: AppConfig) -> None:
    """Ensure composite health check aggregates all sub-checks cleanly."""
    res = run_all_health_checks(app_config, db=in_memory_db)
    assert "overall_status" in res
    assert "python" in res
    assert "database" in res
    assert "blockchain_rpc" in res
    assert "storage" in res
    # When Anvil is not running, overall is DEGRADED, which is expected and valid for M1
    assert res["overall_status"] in ("HEALTHY", "DEGRADED")

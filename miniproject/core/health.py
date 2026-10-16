"""Health check diagnostics for MempoolGuard local environment and services."""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from core.config import AppConfig, load_config
from core.database import Database


def check_python_environment() -> dict[str, Any]:
    """Verify host Python version and runtime environment."""
    version_info = sys.version_info
    is_supported = (version_info.major == 3 and version_info.minor >= 11) or version_info.major > 3
    return {
        "status": "HEALTHY" if is_supported else "UNHEALTHY",
        "python_version": f"{version_info.major}.{version_info.minor}.{version_info.micro}",
        "platform": sys.platform,
        "is_supported": is_supported,
    }


def check_database(db: Database) -> dict[str, Any]:
    """Test SQLite read/write capability and schema integrity."""
    try:
        conn = db.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 AS test_val")
        row = cur.fetchone()
        conn.close()
        if row and row["test_val"] == 1:
            counts = db.get_summary_counts()
            return {
                "status": "HEALTHY",
                "database_path": db.db_path,
                "table_counts": counts,
            }
        return {
            "status": "UNHEALTHY",
            "database_path": db.db_path,
            "error": "Query did not return expected result",
        }
    except (sqlite3.Error, OSError) as exc:
        return {
            "status": "UNHEALTHY",
            "database_path": db.db_path,
            "error": str(exc),
        }


def check_rpc_node(rpc_url: str, timeout_seconds: float = 2.0) -> dict[str, Any]:
    """Check availability of the local EVM node (e.g. Anvil).

    Does not crash if offline; returns clear connectivity status.
    """
    req_body = b'{"jsonrpc":"2.0","method":"web3_clientVersion","params":[],"id":1}'
    req = urllib.request.Request(
        rpc_url,
        data=req_body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
            if resp.status == 200:
                import json

                data = json.loads(resp.read().decode("utf-8"))
                client_version = data.get("result", "unknown")
                return {
                    "status": "HEALTHY",
                    "rpc_url": rpc_url,
                    "connected": True,
                    "client_version": client_version,
                }
            return {
                "status": "DEGRADED",
                "rpc_url": rpc_url,
                "connected": False,
                "http_status": resp.status,
            }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "status": "DEGRADED",
            "rpc_url": rpc_url,
            "connected": False,
            "message": f"Local node offline or unreachable: {exc}",
        }


def check_storage_directories(dirs: list[str | Path]) -> dict[str, Any]:
    """Verify storage directories exist and are writable."""
    results = {}
    all_writable = True
    for d in dirs:
        p = Path(d)
        p.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.TemporaryFile(dir=p) as tf:
                tf.write(b"health_check")
            results[str(p)] = "writable"
        except OSError as exc:
            results[str(p)] = f"error: {exc}"
            all_writable = False

    return {
        "status": "HEALTHY" if all_writable else "UNHEALTHY",
        "directories": results,
    }


def run_all_health_checks(
    config: AppConfig | None = None, db: Database | None = None
) -> dict[str, Any]:
    """Run full diagnostic suite across runtime, storage, database, and local EVM node."""
    if config is None:
        config = load_config()
    py_check = check_python_environment()

    if db is None:
        db = Database(
            db_path=config.database.sqlite_path,
            timeout_seconds=config.database.timeout_seconds,
            enable_wal=config.database.enable_wal,
        )
        db.init_schema()

    db_check = check_database(db)
    rpc_check = check_rpc_node(config.blockchain.rpc_http_url)

    required_dirs = [
        "data/raw",
        "data/processed",
        config.models.artifact_dir,
    ]
    storage_check = check_storage_directories(required_dirs)

    # Determine overall status
    if py_check["status"] == "UNHEALTHY" or db_check["status"] == "UNHEALTHY":
        overall = "UNHEALTHY"
    elif rpc_check["status"] == "DEGRADED":
        overall = "DEGRADED"  # Degraded if local anvil node is not running yet
    else:
        overall = "HEALTHY"

    return {
        "overall_status": overall,
        "python": py_check,
        "database": db_check,
        "blockchain_rpc": rpc_check,
        "storage": storage_check,
    }


if __name__ == "__main__":
    report = run_all_health_checks()
    print(json.dumps(report, indent=2))

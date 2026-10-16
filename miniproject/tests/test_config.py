"""Unit tests for configuration loader and validation (core/config.py)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import AppConfig, RiskConfig, load_config


def test_load_default_config(app_config: AppConfig) -> None:
    """Test loading standard default configuration from YAML."""
    assert app_config.blockchain.rpc_http_url.startswith("http")
    assert app_config.blockchain.chain_id == 31337
    assert app_config.risk.threshold_allow < app_config.risk.threshold_flag
    assert app_config.risk.default_fallback_decision in ("ALLOW", "FLAG", "HOLD")
    assert app_config.database.sqlite_path != ""
    assert app_config.api.port == 8000


def test_invalid_risk_thresholds() -> None:
    """Ensure threshold_allow must be strictly lower than threshold_flag."""
    with pytest.raises(ValidationError, match="threshold_allow .* must be strictly less than"):
        RiskConfig(threshold_allow=80.0, threshold_flag=50.0)

    with pytest.raises(ValidationError, match="threshold_allow .* must be strictly less than"):
        RiskConfig(threshold_allow=60.0, threshold_flag=60.0)


def test_invalid_fallback_decision() -> None:
    """Ensure fallback decision must be ALLOW, FLAG, or HOLD."""
    with pytest.raises(ValidationError, match="Invalid default_fallback_decision"):
        RiskConfig(
            threshold_allow=30.0,
            threshold_flag=70.0,
            default_fallback_decision="BLOCK_CHAIN",
        )


def test_environment_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Test overriding configuration using MEMPOOLGUARD_* environment variables."""
    monkeypatch.setenv("MEMPOOLGUARD_RPC_HTTP_URL", "http://127.0.0.1:9999")
    monkeypatch.setenv("MEMPOOLGUARD_THRESHOLD_ALLOW", "25.0")
    monkeypatch.setenv("MEMPOOLGUARD_API_PORT", "8888")

    config = load_config()
    assert config.blockchain.rpc_http_url == "http://127.0.0.1:9999"
    assert config.risk.threshold_allow == 25.0
    assert config.api.port == 8888


def test_nonexistent_config_file() -> None:
    """Test that a non-existent explicit config path raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_config(config_path="nonexistent_path/fake_config.yaml")

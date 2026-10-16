"""Configuration loader and schema validation for MempoolGuard.

Loads hierarchical YAML configurations with environment variable overrides
using Pydantic models to guarantee type safety and constraint validation.
"""

from __future__ import annotations

import os
import urllib.parse
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BlockchainConfig(BaseModel):
    """Blockchain node connection settings."""

    model_config = ConfigDict(extra="forbid")

    rpc_http_url: str = Field(
        default="http://127.0.0.1:8545", description="HTTP RPC URL of the EVM node"
    )
    rpc_ws_url: str | None = Field(
        default="ws://127.0.0.1:8545", description="WebSocket RPC URL of the EVM node"
    )
    chain_id: int = Field(default=31337, ge=0, description="EVM Chain ID (Anvil default: 31337)")
    poll_interval_seconds: float = Field(
        default=0.2, gt=0, description="Polling interval for fallback listener"
    )
    connection_timeout_seconds: float = Field(
        default=5.0, gt=0, description="Timeout for RPC connections in seconds"
    )

    @field_validator("rpc_http_url")
    @classmethod
    def validate_rpc_url(cls, v: str) -> str:
        parsed = urllib.parse.urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"rpc_http_url must use http or https scheme, got: {v}")
        return v


class DatabaseConfig(BaseModel):
    """SQLite persistence settings."""

    model_config = ConfigDict(extra="forbid")

    sqlite_path: str = Field(
        default="data/mempoolguard.db", description="Path to the SQLite database file"
    )
    timeout_seconds: float = Field(
        default=10.0, gt=0, description="SQLite connection timeout in seconds"
    )
    enable_wal: bool = Field(
        default=True, description="Enable Write-Ahead Logging (WAL) mode for concurrency"
    )


class FeaturesConfig(BaseModel):
    """Feature calculation parameters."""

    model_config = ConfigDict(extra="forbid")

    window_seconds: float = Field(
        default=60.0, gt=0, description="Time window in seconds for historical behavioral features"
    )
    feature_version: str = Field(
        default="1.0.0", description="Semantic version of the feature calculation engine"
    )


class RiskConfig(BaseModel):
    """Risk policy and threshold configuration."""

    model_config = ConfigDict(extra="forbid")

    threshold_allow: float = Field(
        default=30.0, ge=0.0, le=100.0, description="Maximum risk score for ALLOW decision"
    )
    threshold_flag: float = Field(
        default=70.0, ge=0.0, le=100.0, description="Score threshold above which decision is HOLD"
    )
    default_fallback_decision: str = Field(
        default="FLAG", description="Fallback decision when model or features fail"
    )
    policy_version: str = Field(
        default="1.0.0", description="Version identifier for the intervention policy"
    )
    hold_duration_seconds: float = Field(
        default=30.0, gt=0, description="Simulated duration in seconds for HOLD state"
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> RiskConfig:
        if self.threshold_allow >= self.threshold_flag:
            raise ValueError(
                f"threshold_allow ({self.threshold_allow}) must be strictly less than "
                f"threshold_flag ({self.threshold_flag})"
            )
        if self.default_fallback_decision not in ("ALLOW", "FLAG", "HOLD"):
            raise ValueError(
                f"Invalid default_fallback_decision: {self.default_fallback_decision}. "
                "Must be ALLOW, FLAG, or HOLD."
            )
        return self


class ModelsConfig(BaseModel):
    """Machine learning model artifact configuration."""

    model_config = ConfigDict(extra="forbid")

    artifact_dir: str = Field(
        default="artifacts/models", description="Directory where model artifacts are stored"
    )
    primary_model_filename: str = Field(
        default="xgboost_v1.json", description="Filename of the primary trained model"
    )
    model_version: str = Field(default="1.0.0", description="Active model release version")


class ApiConfig(BaseModel):
    """REST API service configuration."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1", description="FastAPI host binding")
    port: int = Field(default=8000, ge=1, le=65535, description="FastAPI port binding")
    title: str = Field(default="MempoolGuard API", description="API title")
    version: str = Field(default="1.0.0", description="API version")


class DashboardConfig(BaseModel):
    """Interactive Streamlit dashboard configuration."""

    model_config = ConfigDict(extra="forbid")

    host: str = Field(default="127.0.0.1", description="Dashboard host binding")
    port: int = Field(default=8501, ge=1, le=65535, description="Dashboard port binding")
    theme: str = Field(default="dark", description="Visual theme")


class LoggingConfig(BaseModel):
    """Logging settings."""

    model_config = ConfigDict(extra="forbid")

    level: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")
    format: str = Field(default="structured", description="Log format (structured or standard)")

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        v_upper = v.upper()
        if v_upper not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError(f"Invalid log level: {v}")
        return v_upper


class EvaluationConfig(BaseModel):
    """Model evaluation and data split settings."""

    model_config = ConfigDict(extra="forbid")

    random_seed: int = Field(default=42, description="Random seed for reproducibility")
    test_size: float = Field(default=0.2, gt=0, lt=1, description="Proportion for test split")
    val_size: float = Field(default=0.1, gt=0, lt=1, description="Proportion for validation split")


class AppConfig(BaseModel):
    """Root configuration holding all domain settings."""

    model_config = ConfigDict(extra="forbid")

    blockchain: BlockchainConfig = Field(default_factory=BlockchainConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)


def _apply_env_overrides(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Override nested dictionary configuration keys with MEMPOOLGUARD_* environment variables."""
    # Mapping of ENV VAR -> (section, key, type_caster)
    env_mappings = {
        "MEMPOOLGUARD_RPC_HTTP_URL": ("blockchain", "rpc_http_url", str),
        "MEMPOOLGUARD_RPC_WS_URL": ("blockchain", "rpc_ws_url", str),
        "MEMPOOLGUARD_CHAIN_ID": ("blockchain", "chain_id", int),
        "MEMPOOLGUARD_SQLITE_PATH": ("database", "sqlite_path", str),
        "MEMPOOLGUARD_DB_TIMEOUT_SECONDS": ("database", "timeout_seconds", float),
        "MEMPOOLGUARD_FEATURE_WINDOW_SECONDS": ("features", "window_seconds", float),
        "MEMPOOLGUARD_FEATURE_VERSION": ("features", "feature_version", str),
        "MEMPOOLGUARD_THRESHOLD_ALLOW": ("risk", "threshold_allow", float),
        "MEMPOOLGUARD_THRESHOLD_FLAG": ("risk", "threshold_flag", float),
        "MEMPOOLGUARD_DEFAULT_FALLBACK_DECISION": ("risk", "default_fallback_decision", str),
        "MEMPOOLGUARD_POLICY_VERSION": ("risk", "policy_version", str),
        "MEMPOOLGUARD_API_HOST": ("api", "host", str),
        "MEMPOOLGUARD_API_PORT": ("api", "port", int),
        "MEMPOOLGUARD_LOG_LEVEL": ("logging", "level", str),
    }

    for env_var, (section, key, caster) in env_mappings.items():
        val = os.getenv(env_var)
        if val is not None and val != "":
            if section not in raw_data:
                raw_data[section] = {}
            try:
                raw_data[section][key] = caster(val)
            except (ValueError, TypeError) as exc:
                raise ValueError(f"Failed to cast env var {env_var}={val} to {caster.__name__}: {exc}") from exc

    return raw_data


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load, validate, and return the application configuration.

    Order of precedence:
    1. Environment variables (MEMPOOLGUARD_*)
    2. YAML configuration file
    3. Pydantic default values
    """
    load_dotenv()

    # Determine config file path
    if config_path is None:
        target = Path("configs/config.yaml")
    else:
        target = Path(config_path)

    raw_data: dict[str, Any] = {}
    if target.exists():
        with open(target, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                raw_data = loaded
            elif loaded is not None:
                raise ValueError(f"Invalid YAML configuration format in {target}")
    elif config_path is not None:
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    # Apply environment variable overrides
    merged_data = _apply_env_overrides(raw_data)

    # Validate against AppConfig schema
    return AppConfig(**merged_data)

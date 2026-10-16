"""Pydantic request and response schemas for the MempoolGuard REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from core.contracts import DecisionType, utc_now


def _validate_hex_hash(v: str) -> str:
    clean = v.strip().lower()
    if not clean.startswith("0x") or len(clean) != 66:
        raise ValueError(f"Invalid 32-byte hex hash: expected 66 characters starting with '0x', got {v}")
    try:
        int(clean, 16)
    except ValueError:
        raise ValueError(f"Invalid hex string format: {v}") from None
    return clean


def _validate_hex_address(v: str | None) -> str | None:
    if v is None:
        return None
    clean = v.strip().lower()
    if not clean.startswith("0x") or len(clean) != 42:
        raise ValueError(f"Invalid 20-byte address: expected 42 characters starting with '0x', got {v}")
    try:
        int(clean, 16)
    except ValueError:
        raise ValueError(f"Invalid address hex string: {v}") from None
    return clean


class TransactionInput(BaseModel):
    """Transaction observation input payload."""

    model_config = ConfigDict(extra="forbid")

    tx_hash: str = Field(description="Unique 32-byte transaction hash (hex with 0x prefix)")
    chain_id: int = Field(default=31337, ge=0, description="EIP-155 chain ID")
    sender: str = Field(description="20-byte sender address (hex with 0x prefix)")
    receiver: str | None = Field(default=None, description="20-byte recipient address, or None for contract creation")
    value_wei: int = Field(default=0, ge=0, description="Transaction transfer value in wei")
    nonce: int = Field(default=0, ge=0, description="Sender sequence nonce")
    gas_limit: int = Field(default=21000, ge=21000, description="Maximum gas units allocated")
    gas_price_wei: int | None = Field(default=None, ge=0, description="Legacy gas price in wei")
    max_fee_per_gas_wei: int | None = Field(default=None, ge=0, description="EIP-1559 max fee per gas in wei")
    max_priority_fee_per_gas_wei: int | None = Field(default=None, ge=0, description="EIP-1559 max priority fee in wei")
    input_data: str = Field(default="0x", description="Transaction payload data (hex)")
    first_seen_at: datetime = Field(default_factory=utc_now, description="UTC timestamp of mempool observation")

    @field_validator("tx_hash")
    @classmethod
    def check_tx_hash(cls, v: str) -> str:
        return _validate_hex_hash(v)

    @field_validator("sender")
    @classmethod
    def check_sender(cls, v: str) -> str:
        res = _validate_hex_address(v)
        if res is None:
            raise ValueError("Sender address cannot be None")
        return res

    @field_validator("receiver")
    @classmethod
    def check_receiver(cls, v: str | None) -> str | None:
        return _validate_hex_address(v)


class PredictRequest(BaseModel):
    """Request payload for model risk score prediction."""

    model_config = ConfigDict(extra="forbid")

    transaction: TransactionInput | None = Field(default=None, description="Raw transaction attributes to extract features from")
    features: dict[str, Any] | None = Field(default=None, description="Pre-extracted 30 canonical feature vector dictionary")
    tx_hash: str | None = Field(default=None, description="Transaction hash (required if features supplied without transaction)")
    model_name: str | None = Field(default=None, description="Target model: 'xgboost', 'logistic_regression', or 'rule_baseline'")
    model_version: str | None = Field(default=None, description="Optional target model version: 'v1' or 'v2' (defaults to active registry version)")

    @model_validator(mode="after")
    def validate_input_presence(self) -> PredictRequest:
        if self.transaction is None and self.features is None:
            raise ValueError("Either 'transaction' or 'features' must be provided.")
        if self.transaction is None and self.features is not None and not self.tx_hash:
            raise ValueError("'tx_hash' must be provided when passing pre-computed 'features'.")
        if self.tx_hash:
            self.tx_hash = _validate_hex_hash(self.tx_hash)
        return self


class PredictResponse(BaseModel):
    """Standardized response schema for ML inference predictions."""

    model_config = ConfigDict(extra="forbid")

    tx_hash: str = Field(description="Transaction hash")
    risk_score: float = Field(ge=0.0, le=100.0, description="Risk score on 0.0 to 100.0 scale")
    predicted_class: int = Field(ge=0, le=1, description="Predicted binary class (0=benign, 1=anomaly)")
    model_name: str = Field(description="Identifier of model used")
    model_version: str = Field(description="Semantic version of model")
    inference_latency_ms: float = Field(ge=0.0, description="Inference latency in milliseconds")
    reason_codes: list[str] = Field(default_factory=list, description="Machine-readable risk tags")
    feature_version: str = Field(description="Feature pipeline schema version")
    explanation: str = Field(default="", description="Summary of risk drivers")


class DecisionRequest(BaseModel):
    """Request payload for full M6 -> M7 decision and audit flow."""

    model_config = ConfigDict(extra="forbid")

    transaction: TransactionInput | None = Field(default=None, description="Raw transaction attributes to evaluate")
    features: dict[str, Any] | None = Field(default=None, description="Pre-computed 30 canonical feature vector")
    tx_hash: str | None = Field(default=None, description="Transaction hash (required if features supplied without transaction)")
    model_name: str | None = Field(default=None, description="Target model: 'xgboost', 'logistic_regression', or 'rule_baseline'")
    model_version: str | None = Field(default=None, description="Optional target model version: 'v1' or 'v2' (defaults to active registry version)")
    idempotency: bool = Field(default=True, description="Enable duplicate decision suppression")

    @model_validator(mode="after")
    def validate_input_presence(self) -> DecisionRequest:
        if self.transaction is None and self.features is None:
            raise ValueError("Either 'transaction' or 'features' must be provided.")
        if self.transaction is None and self.features is not None and not self.tx_hash:
            raise ValueError("'tx_hash' must be provided when passing pre-computed 'features'.")
        if self.tx_hash:
            self.tx_hash = _validate_hex_hash(self.tx_hash)
        return self


class DecisionResponse(BaseModel):
    """Standardized response schema for M7 application-level simulated decisions."""

    model_config = ConfigDict(extra="forbid")

    tx_hash: str = Field(description="Transaction hash")
    decision: DecisionType = Field(description="Simulated intervention decision: ALLOW, FLAG, or HOLD")
    risk_score: float | None = Field(default=None, ge=0.0, le=100.0, description="Underlying model risk score")
    reason_codes: list[str] = Field(default_factory=list, description="Truthful reason codes supporting the decision")
    policy_version: str = Field(description="Policy engine version applied")
    model_name: str | None = Field(default=None, description="Model used for risk scoring")
    model_version: str | None = Field(default=None, description="Model version used")
    created_at: datetime = Field(description="UTC timestamp when decision was rendered")
    expires_at: datetime | None = Field(default=None, description="UTC expiration for temporary HOLD (or None)")
    is_simulated: bool = Field(default=True, description="Confirmation that decision is application-level simulation")
    audit_id: str | None = Field(default=None, description="UUID of persisted audit record")


class SimulateDecisionRequest(BaseModel):
    """Isolated demonstration/testing endpoint schema for manual threshold exploration."""

    model_config = ConfigDict(extra="forbid")

    tx_hash: str = Field(description="Transaction hash to evaluate")
    risk_score: float = Field(ge=0.0, le=100.0, description="Manually supplied numeric risk score [0.0, 100.0]")
    features: dict[str, Any] | None = Field(default=None, description="Optional feature vector for reason code generation")
    model_name: str = Field(default="ManualSimulation", description="Simulation identifier")
    idempotency: bool = Field(default=True, description="Enable duplicate decision suppression")

    @field_validator("tx_hash")
    @classmethod
    def check_tx_hash(cls, v: str) -> str:
        return _validate_hex_hash(v)


class AuditRecordItem(BaseModel):
    """Item representation of an immutable audit record."""

    model_config = ConfigDict(extra="forbid")

    audit_id: str
    tx_hash: str
    event_type: str
    timestamp: datetime
    risk_score: float | None = None
    decision: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    error_info: str | None = None


class AuditLookupResponse(BaseModel):
    """Response schema for transaction audit query."""

    model_config = ConfigDict(extra="forbid")

    tx_hash: str
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    audit_records: list[AuditRecordItem] = Field(default_factory=list)


class HealthResponse(BaseModel):
    """System health diagnostic response schema."""

    model_config = ConfigDict(extra="forbid")

    status: str = Field(description="Overall status: HEALTHY, DEGRADED, or UNHEALTHY")
    api: dict[str, Any] = Field(description="API service metadata")
    python: dict[str, Any] = Field(default_factory=dict, description="Python runtime diagnostics")
    database: dict[str, Any] = Field(description="SQLite database health and table metrics")
    models: dict[str, Any] = Field(description="Model registry status and loaded models")
    rpc: dict[str, Any] = Field(description="Local EVM node RPC connectivity status")


class ErrorResponse(BaseModel):
    """RFC-7807 compatible error response schema."""

    model_config = ConfigDict(extra="forbid")

    error: str = Field(description="Standardized machine-readable error code")
    detail: str = Field(description="Human-readable description of the error")
    timestamp: datetime = Field(default_factory=utc_now, description="UTC error timestamp")

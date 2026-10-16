"""Shared validated data contracts for MempoolGuard.

Defines Pydantic data models for transactions, feature vectors, risk
evaluations, simulated decisions, and audit events.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """Return the current time with explicit UTC timezone."""
    return datetime.now(timezone.utc)


class TransactionStatus(str, Enum):
    """Lifecycle status of an observed transaction."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


class DecisionType(str, Enum):
    """Application-level simulated intervention decision.

    Note: These decisions simulate risk actions and do NOT alter blockchain
    consensus.
    """

    ALLOW = "ALLOW"
    FLAG = "FLAG"
    HOLD = "HOLD"


class AuditEventType(str, Enum):
    """Categorization for audit log records."""

    TX_RECEIVED = "TX_RECEIVED"
    FEATURE_EXTRACTED = "FEATURE_EXTRACTED"
    RISK_PREDICTED = "RISK_PREDICTED"
    DECISION_ISSUED = "DECISION_ISSUED"
    TX_CONFIRMED = "TX_CONFIRMED"
    PIPELINE_ERROR = "PIPELINE_ERROR"
    FALLBACK_APPLIED = "FALLBACK_APPLIED"


class Transaction(BaseModel):
    """Core transaction contract representing an observed EVM transaction."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    tx_hash: str = Field(description="Unique 32-byte transaction hash (hex with 0x prefix)")
    chain_id: int = Field(ge=0, description="EIP-155 chain ID")
    sender: str = Field(description="20-byte sender address (hex with 0x prefix)")
    receiver: str | None = Field(
        default=None,
        description="20-byte recipient address, or None for contract creation",
    )
    value_wei: int = Field(ge=0, description="Transaction transfer value in wei")
    nonce: int = Field(ge=0, description="Sender sequence nonce")
    gas_limit: int = Field(ge=0, description="Maximum gas units allocated")
    gas_price_wei: int | None = Field(
        default=None, ge=0, description="Legacy gas price in wei, if applicable"
    )
    max_fee_per_gas_wei: int | None = Field(
        default=None, ge=0, description="EIP-1559 max fee per gas in wei, if applicable"
    )
    max_priority_fee_per_gas_wei: int | None = Field(
        default=None, ge=0, description="EIP-1559 max priority fee in wei, if applicable"
    )
    input_data: str = Field(default="0x", description="Transaction payload data (hex)")
    first_seen_at: datetime = Field(
        default_factory=utc_now,
        description="UTC timestamp when the transaction was first observed in mempool",
    )
    confirmed_at: datetime | None = Field(
        default=None,
        description="UTC timestamp when the transaction was included in a block",
    )
    block_number: int | None = Field(
        default=None, ge=0, description="Block number where confirmed, or None if pending"
    )
    transaction_status: TransactionStatus = Field(
        default=TransactionStatus.PENDING,
        description="Current observable transaction lifecycle status",
    )
    feature_availability: dict[str, bool] = Field(
        default_factory=lambda: {
            "transaction": True,
            "gas": True,
            "graph": True,
            "propagation": False,
        },
        description="Availability flags for distinct feature groups",
    )

    @field_validator("tx_hash")
    @classmethod
    def validate_tx_hash(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if not v_clean.startswith("0x") or len(v_clean) != 66:
            raise ValueError(f"Invalid transaction hash format: {v}")
        return v_clean

    @field_validator("sender")
    @classmethod
    def validate_sender(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if not v_clean.startswith("0x") or len(v_clean) != 42:
            raise ValueError(f"Invalid sender address format: {v}")
        return v_clean

    @field_validator("receiver")
    @classmethod
    def validate_receiver(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v_clean = v.strip().lower()
        if not v_clean.startswith("0x") or len(v_clean) != 42:
            raise ValueError(f"Invalid receiver address format: {v}")
        return v_clean

    @field_validator("first_seen_at", "confirmed_at")
    @classmethod
    def ensure_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class FeatureVector(BaseModel):
    """Calculated numeric and categorical features for a transaction."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    tx_hash: str = Field(description="Associated transaction hash")
    feature_timestamp: datetime = Field(
        default_factory=utc_now,
        description="UTC timestamp at which features were calculated",
    )
    features: dict[str, Any] = Field(
        default_factory=dict,
        description="Dictionary mapping feature names to their numeric/categorical values",
    )
    feature_names: list[str] = Field(
        default_factory=list,
        description="Ordered list of feature identifiers matching features dict",
    )
    feature_version: str = Field(
        default="1.0.0",
        description="Semantic version of the feature engineering pipeline",
    )
    availability_mask: dict[str, bool] = Field(
        default_factory=dict,
        description="Indicator of which feature groups were available at calculation time",
    )
    missing_features: list[str] = Field(
        default_factory=list,
        description="List of features that could not be calculated and were preserved as missing",
    )

    @field_validator("tx_hash")
    @classmethod
    def validate_tx_hash(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if not v_clean.startswith("0x") or len(v_clean) != 66:
            raise ValueError(f"Invalid transaction hash format: {v}")
        return v_clean

    @field_validator("feature_timestamp")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class RiskResult(BaseModel):
    """Risk prediction result emitted by a model or rule engine."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    tx_hash: str = Field(description="Associated transaction hash")
    risk_score: float = Field(
        ge=0.0,
        le=100.0,
        description="Estimated risk score on a normalized 0.0 to 100.0 scale",
    )
    predicted_class: int = Field(
        ge=0, le=1, description="Predicted class index (0 = benign, 1 = high risk/anomaly)"
    )
    model_name: str = Field(description="Name or identifier of the predictive model used")
    model_version: str = Field(description="Version of the model artifact")
    prediction_timestamp: datetime = Field(
        default_factory=utc_now,
        description="UTC timestamp when inference was completed",
    )
    feature_timestamp: datetime = Field(
        description="UTC timestamp of the feature vector used for prediction"
    )
    inference_latency_ms: float = Field(
        ge=0.0, description="Time taken to calculate inference in milliseconds"
    )
    reason_codes: list[str] = Field(
        default_factory=list,
        description="Standardized machine-readable explanation tags for the score",
    )
    explanation: str = Field(
        default="", description="Human-readable summary of risk drivers"
    )

    @field_validator("tx_hash")
    @classmethod
    def validate_tx_hash(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if not v_clean.startswith("0x") or len(v_clean) != 66:
            raise ValueError(f"Invalid transaction hash format: {v}")
        return v_clean

    @field_validator("prediction_timestamp", "feature_timestamp")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class Decision(BaseModel):
    """Simulated application-level intervention decision."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    tx_hash: str = Field(description="Associated transaction hash")
    decision: DecisionType = Field(description="Action decision: ALLOW, FLAG, or HOLD")
    reason_codes: list[str] = Field(
        default_factory=list,
        description="Truthful explanation codes supporting this decision",
    )
    policy_version: str = Field(
        default="1.0.0", description="Policy engine version applied"
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        description="UTC timestamp when the decision was rendered",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional UTC timestamp when a temporary HOLD expires",
    )
    is_simulated: bool = Field(
        default=True,
        description="Explicit flag confirming this decision is simulated (not on-chain blocking)",
    )

    @field_validator("tx_hash")
    @classmethod
    def validate_tx_hash(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if not v_clean.startswith("0x") or len(v_clean) != 66:
            raise ValueError(f"Invalid transaction hash format: {v}")
        return v_clean

    @field_validator("created_at", "expires_at")
    @classmethod
    def ensure_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)


class AuditRecord(BaseModel):
    """Immutable audit trail record for compliance and tracing."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    audit_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description="Unique identifier for the audit record",
    )
    tx_hash: str = Field(description="Associated transaction hash")
    event_type: AuditEventType = Field(description="Type of pipeline event")
    timestamp: datetime = Field(
        default_factory=utc_now,
        description="UTC timestamp when the event was logged",
    )
    risk_score: float | None = Field(
        default=None,
        ge=0.0,
        le=100.0,
        description="Associated risk score, if available",
    )
    decision: DecisionType | None = Field(
        default=None, description="Associated decision, if available"
    )
    details: dict[str, Any] = Field(
        default_factory=dict, description="Structured event payload and context"
    )
    error_info: str | None = Field(
        default=None, description="Error message or stack summary, if applicable"
    )

    @field_validator("tx_hash")
    @classmethod
    def validate_tx_hash(cls, v: str) -> str:
        v_clean = v.strip().lower()
        if not v_clean.startswith("0x") or len(v_clean) != 66:
            raise ValueError(f"Invalid transaction hash format: {v}")
        return v_clean

    @field_validator("timestamp")
    @classmethod
    def ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

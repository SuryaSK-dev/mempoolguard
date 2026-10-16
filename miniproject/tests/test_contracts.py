"""Unit tests for shared data contracts (core/contracts.py)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from core.contracts import (
    AuditEventType,
    AuditRecord,
    Decision,
    DecisionType,
    FeatureVector,
    RiskResult,
    Transaction,
    TransactionStatus,
)


def test_transaction_valid(sample_transaction: Transaction) -> None:
    """Test valid transaction creation and serialization."""
    assert sample_transaction.tx_hash.startswith("0x")
    assert len(sample_transaction.tx_hash) == 66
    assert sample_transaction.chain_id == 31337
    assert sample_transaction.value_wei == 10**18
    assert sample_transaction.transaction_status == TransactionStatus.PENDING
    assert sample_transaction.first_seen_at.tzinfo == timezone.utc

    # Test serialization to dict
    d = sample_transaction.model_dump()
    assert d["tx_hash"] == sample_transaction.tx_hash
    assert d["value_wei"] == 10**18


def test_transaction_invalid_hash() -> None:
    """Test rejection of malformed transaction hashes."""
    with pytest.raises(ValidationError, match="Invalid transaction hash format"):
        Transaction(
            tx_hash="0xshort",
            chain_id=31337,
            sender="0x" + "1" * 40,
            value_wei=0,
            nonce=0,
            gas_limit=21000,
        )


def test_transaction_invalid_sender() -> None:
    """Test rejection of malformed sender address."""
    with pytest.raises(ValidationError, match="Invalid sender address format"):
        Transaction(
            tx_hash="0x" + "a" * 64,
            chain_id=31337,
            sender="not_an_address",
            value_wei=0,
            nonce=0,
            gas_limit=21000,
        )


def test_transaction_negative_value() -> None:
    """Test rejection of negative transfer value."""
    with pytest.raises(ValidationError):
        Transaction(
            tx_hash="0x" + "a" * 64,
            chain_id=31337,
            sender="0x" + "1" * 40,
            value_wei=-1,
            nonce=0,
            gas_limit=21000,
        )


def test_feature_vector_validation(sample_feature_vector: FeatureVector) -> None:
    """Test FeatureVector integrity and timezone enforcement."""
    assert sample_feature_vector.feature_version == "1.0.0"
    assert "value_eth" in sample_feature_vector.features
    assert sample_feature_vector.feature_timestamp.tzinfo == timezone.utc


def test_risk_result_bounds(sample_risk_result: RiskResult) -> None:
    """Test RiskResult score bounds and class constraints."""
    assert 0.0 <= sample_risk_result.risk_score <= 100.0
    assert sample_risk_result.predicted_class in (0, 1)

    # Risk score > 100 should fail
    with pytest.raises(ValidationError):
        RiskResult(
            tx_hash=sample_risk_result.tx_hash,
            risk_score=105.0,
            predicted_class=1,
            model_name="test",
            model_version="1.0.0",
            feature_timestamp=datetime.now(timezone.utc),
            inference_latency_ms=1.0,
        )


def test_decision_contract(sample_decision: Decision) -> None:
    """Test Decision contract default simulation flag and types."""
    assert sample_decision.decision == DecisionType.HOLD
    assert sample_decision.is_simulated is True
    assert sample_decision.policy_version == "1.0.0"


def test_audit_record_contract(sample_audit_record: AuditRecord) -> None:
    """Test AuditRecord generation and validation."""
    assert sample_audit_record.event_type == AuditEventType.DECISION_ISSUED
    assert sample_audit_record.audit_id is not None
    assert len(sample_audit_record.audit_id) > 10
    assert sample_audit_record.timestamp.tzinfo == timezone.utc

"""Unit tests for SQLite transactional persistence (core/database.py)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

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
from core.database import Database


def test_schema_initialization(in_memory_db: Database) -> None:
    """Test that all tables and indexes are created successfully."""
    counts = in_memory_db.get_summary_counts()
    assert "raw_transactions" in counts
    assert "decoded_transactions" in counts
    assert "feature_vectors" in counts
    assert "model_predictions" in counts
    assert "decisions" in counts
    assert "audit_records" in counts
    for count in counts.values():
        assert count == 0


def test_raw_transaction_idempotency(in_memory_db: Database) -> None:
    """Verify duplicate raw transactions are ignored without throwing errors."""
    tx_hash = "0x" + "a" * 64
    now = datetime.now(timezone.utc)

    # First insert succeeds
    assert in_memory_db.insert_raw_transaction(tx_hash, '{"raw": 1}', now) is True

    # Duplicate insert ignored, returns False
    assert in_memory_db.insert_raw_transaction(tx_hash, '{"raw": 1}', now) is False

    counts = in_memory_db.get_summary_counts()
    assert counts["raw_transactions"] == 1


def test_insert_and_get_decoded_transaction(
    in_memory_db: Database, sample_transaction: Transaction
) -> None:
    """Verify storing and retrieving decoded transaction records."""
    in_memory_db.insert_decoded_transaction(sample_transaction)

    retrieved = in_memory_db.get_transaction(sample_transaction.tx_hash)
    assert retrieved is not None
    assert retrieved.tx_hash == sample_transaction.tx_hash
    assert retrieved.sender == sample_transaction.sender
    assert retrieved.receiver == sample_transaction.receiver
    assert retrieved.value_wei == sample_transaction.value_wei
    assert retrieved.transaction_status == TransactionStatus.PENDING

    # Test update on confirmation
    updated_tx = sample_transaction.model_copy(
        update={
            "transaction_status": TransactionStatus.CONFIRMED,
            "block_number": 100,
            "confirmed_at": datetime.now(timezone.utc),
        }
    )
    in_memory_db.insert_decoded_transaction(updated_tx)

    retrieved_updated = in_memory_db.get_transaction(sample_transaction.tx_hash)
    assert retrieved_updated is not None
    assert retrieved_updated.transaction_status == TransactionStatus.CONFIRMED
    assert retrieved_updated.block_number == 100
    assert retrieved_updated.confirmed_at is not None


def test_feature_vector_storage(
    in_memory_db: Database,
    sample_transaction: Transaction,
    sample_feature_vector: FeatureVector,
) -> None:
    """Verify feature vector persistence and retrieval."""
    in_memory_db.insert_decoded_transaction(sample_transaction)
    row_id = in_memory_db.insert_feature_vector(sample_feature_vector)
    assert row_id > 0

    retrieved_fv = in_memory_db.get_latest_feature_vector(sample_feature_vector.tx_hash)
    assert retrieved_fv is not None
    assert retrieved_fv.tx_hash == sample_feature_vector.tx_hash
    assert retrieved_fv.feature_version == sample_feature_vector.feature_version
    assert retrieved_fv.features["value_eth"] == 1.0


def test_risk_result_storage(
    in_memory_db: Database,
    sample_transaction: Transaction,
    sample_risk_result: RiskResult,
) -> None:
    """Verify model risk prediction persistence and retrieval."""
    in_memory_db.insert_decoded_transaction(sample_transaction)
    row_id = in_memory_db.insert_risk_result(sample_risk_result)
    assert row_id > 0

    retrieved = in_memory_db.get_latest_risk_result(sample_risk_result.tx_hash)
    assert retrieved is not None
    assert retrieved.risk_score == pytest.approx(78.5)
    assert retrieved.predicted_class == 1
    assert "HIGH_TRANSFER_VALUE" in retrieved.reason_codes


def test_decision_storage(
    in_memory_db: Database,
    sample_transaction: Transaction,
    sample_decision: Decision,
) -> None:
    """Verify decision persistence and retrieval."""
    in_memory_db.insert_decoded_transaction(sample_transaction)
    row_id = in_memory_db.insert_decision(sample_decision)
    assert row_id > 0

    retrieved = in_memory_db.get_latest_decision(sample_decision.tx_hash)
    assert retrieved is not None
    assert retrieved.decision == DecisionType.HOLD
    assert retrieved.is_simulated is True


def test_audit_trail_and_pagination(
    in_memory_db: Database, sample_audit_record: AuditRecord
) -> None:
    """Verify audit record insertion, ordering, and pagination."""
    # Insert multiple audit records
    in_memory_db.insert_audit_record(sample_audit_record)

    record2 = sample_audit_record.model_copy(
        update={
            "audit_id": "audit-2",
            "event_type": AuditEventType.TX_CONFIRMED,
            "timestamp": datetime.now(timezone.utc),
        }
    )
    in_memory_db.insert_audit_record(record2)

    records = in_memory_db.get_audit_records(limit=10, offset=0)
    assert len(records) == 2
    assert records[0].audit_id == "audit-2"  # Descending order by timestamp


def test_transaction_rollback_on_error(in_memory_db: Database) -> None:
    """Verify database transaction rolls back when an unhandled exception occurs."""
    tx_hash = "0x" + "b" * 64
    now = datetime.now(timezone.utc).isoformat()

    with pytest.raises(RuntimeError), in_memory_db.transaction() as cur:
        cur.execute(
            "INSERT INTO raw_transactions VALUES (?, ?, ?, ?)",
            (tx_hash, "{}", now, now),
        )
        # Deliberately raise exception to trigger rollback
        raise RuntimeError("Simulated failure")

    # Transaction should not be present
    counts = in_memory_db.get_summary_counts()
    assert counts["raw_transactions"] == 0

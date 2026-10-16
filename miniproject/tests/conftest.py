"""Shared pytest fixtures for MempoolGuard tests."""

from __future__ import annotations

import tempfile
from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from web3 import Web3

from core.anvil import AnvilController
from core.config import AppConfig, load_config
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


@pytest.fixture
def sample_transaction() -> Transaction:
    """Provide a validated sample Transaction."""
    return Transaction(
        tx_hash="0x" + "a" * 64,
        chain_id=31337,
        sender="0x" + "1" * 40,
        receiver="0x" + "2" * 40,
        value_wei=10**18,
        nonce=5,
        gas_limit=21000,
        gas_price_wei=20_000_000_000,
        max_fee_per_gas_wei=30_000_000_000,
        max_priority_fee_per_gas_wei=2_000_000_000,
        input_data="0x",
        first_seen_at=datetime.now(timezone.utc),
        transaction_status=TransactionStatus.PENDING,
    )


@pytest.fixture
def sample_feature_vector() -> FeatureVector:
    """Provide a validated sample FeatureVector."""
    return FeatureVector(
        tx_hash="0x" + "a" * 64,
        feature_timestamp=datetime.now(timezone.utc),
        features={"value_eth": 1.0, "gas_limit": 21000, "is_contract_call": 0},
        feature_names=["value_eth", "gas_limit", "is_contract_call"],
        feature_version="1.0.0",
        availability_mask={"transaction": True, "gas": True, "graph": True, "propagation": False},
        missing_features=[],
    )


@pytest.fixture
def sample_risk_result() -> RiskResult:
    """Provide a validated sample RiskResult."""
    return RiskResult(
        tx_hash="0x" + "a" * 64,
        risk_score=78.5,
        predicted_class=1,
        model_name="xgboost_classifier",
        model_version="1.0.0",
        prediction_timestamp=datetime.now(timezone.utc),
        feature_timestamp=datetime.now(timezone.utc),
        inference_latency_ms=1.42,
        reason_codes=["HIGH_TRANSFER_VALUE", "GAS_FEE_SPIKE"],
        explanation="Transaction exhibits abnormal gas spike and high value transfer.",
    )


@pytest.fixture
def sample_decision() -> Decision:
    """Provide a validated sample Decision."""
    return Decision(
        tx_hash="0x" + "a" * 64,
        decision=DecisionType.HOLD,
        reason_codes=["HIGH_TRANSFER_VALUE", "GAS_FEE_SPIKE"],
        policy_version="1.0.0",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc),
        is_simulated=True,
    )


@pytest.fixture
def sample_audit_record() -> AuditRecord:
    """Provide a validated sample AuditRecord."""
    return AuditRecord(
        tx_hash="0x" + "a" * 64,
        event_type=AuditEventType.DECISION_ISSUED,
        timestamp=datetime.now(timezone.utc),
        risk_score=78.5,
        decision=DecisionType.HOLD,
        details={"model": "xgboost_classifier", "latency_ms": 1.42},
        error_info=None,
    )


@pytest.fixture
def in_memory_db() -> Generator[Database, None, None]:
    """Provide an initialized in-memory Database instance."""
    db = Database(db_path=":memory:", enable_wal=False)
    db.init_schema()
    yield db


@pytest.fixture
def temp_db() -> Generator[Database, None, None]:
    """Provide an initialized file-backed Database instance in a temporary directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = f"{tmpdir}/test_mempoolguard.db"
        db = Database(db_path=db_file, enable_wal=True)
        db.init_schema()
        yield db


@pytest.fixture
def app_config() -> AppConfig:
    """Provide application configuration loaded from configs/config.yaml."""
    return load_config()


@pytest.fixture(scope="session")
def anvil_controller() -> Generator[AnvilController, None, None]:
    """Provide an active Anvil controller connected to local EVM."""
    controller = AnvilController()
    if not controller.is_running():
        controller.start()
    assert controller.is_running(), "Anvil must be running for blockchain tests"
    yield controller


@pytest.fixture
def w3(anvil_controller: AnvilController) -> Web3:
    """Provide configured Web3 instance."""
    return anvil_controller.get_web3()


"""Comprehensive unit and integration tests for Milestone M7: Decision Engine & Audit Log.

Tests:
1. Decision policy threshold boundaries (ALLOW, FLAG, HOLD, 0.0, 100.0).
2. Exact boundary semantics (score <= 30.0, 30.0 < score < 70.0, score >= 70.0).
3. Evidence-backed reason-code derivation (zero phantom codes).
4. Safe model failure fallback without pipeline crash.
5. Invalid risk score containment (NaN, negative, >100).
6. Idempotency and duplicate safety across repeated evaluations.
7. Audit record persistence and retrieval via Database layer.
8. Policy version and simulated intervention verification.
9. Integration with real M6 predictive models.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from core.contracts import AuditEventType, DecisionType
from core.database import Database
from decision.engine import DecisionEngine
from decision.policy import (
    REASON_BURST_ACTIVITY,
    REASON_CONTRACT_CALL,
    REASON_FALLBACK_POLICY,
    REASON_FAN_OUT,
    REASON_GAS_DEVIATION_LOW,
    REASON_GAS_SPIKE,
    REASON_HIGH_RISK_SCORE,
    REASON_LOW_RISK_SCORE,
    REASON_MEDIUM_RISK_SCORE,
    REASON_MODEL_FAILURE,
    REASON_REPEATED_TRANSFER,
    PolicyEngine,
)
from models.baseline import RuleBaseline
from models.logistic import LogisticRegressionModel


@pytest.fixture
def temp_db(tmp_path: Path) -> Database:
    """Create a temporary initialized database for testing."""
    db_file = tmp_path / "test_decision.db"
    db = Database(str(db_file))
    db.init_schema()
    return db


@pytest.fixture
def policy_engine() -> PolicyEngine:
    """Default PolicyEngine with 30.0 allow and 70.0 flag thresholds."""
    return PolicyEngine(
        threshold_allow=30.0,
        threshold_flag=70.0,
        default_fallback_decision=DecisionType.FLAG,
        policy_version="1.0.0",
        hold_duration_seconds=30.0,
    )


def test_policy_engine_exact_boundary_semantics(policy_engine: PolicyEngine) -> None:
    """Verify exact boundary semantics: <=30.0 is ALLOW, 30.0-70.0 is FLAG, >=70.0 is HOLD."""
    # 1. Lower bound (0.0) -> ALLOW
    assert policy_engine.evaluate_score(0.0) == DecisionType.ALLOW

    # 2. Below ALLOW threshold -> ALLOW
    assert policy_engine.evaluate_score(15.0) == DecisionType.ALLOW
    assert policy_engine.evaluate_score(29.99) == DecisionType.ALLOW

    # 3. Exactly at ALLOW threshold -> ALLOW
    assert policy_engine.evaluate_score(30.0) == DecisionType.ALLOW

    # 4. Just above ALLOW threshold -> FLAG
    assert policy_engine.evaluate_score(30.01) == DecisionType.FLAG

    # 5. Mid-range score -> FLAG
    assert policy_engine.evaluate_score(50.0) == DecisionType.FLAG
    assert policy_engine.evaluate_score(69.99) == DecisionType.FLAG

    # 6. Exactly at FLAG threshold -> HOLD
    assert policy_engine.evaluate_score(70.0) == DecisionType.HOLD

    # 7. Above FLAG threshold -> HOLD
    assert policy_engine.evaluate_score(85.5) == DecisionType.HOLD

    # 8. Upper bound (100.0) -> HOLD
    assert policy_engine.evaluate_score(100.0) == DecisionType.HOLD


def test_policy_engine_invalid_score_rejection(policy_engine: PolicyEngine) -> None:
    """Verify PolicyEngine raises ValueError on NaN, Inf, or out-of-bound scores."""
    with pytest.raises(ValueError, match="out of bounds"):
        policy_engine.evaluate_score(-0.01)

    with pytest.raises(ValueError, match="out of bounds"):
        policy_engine.evaluate_score(100.01)

    with pytest.raises(ValueError, match="Invalid risk score"):
        policy_engine.evaluate_score(float("nan"))

    with pytest.raises(ValueError, match="Invalid risk score"):
        policy_engine.evaluate_score(float("inf"))


def test_truthful_reason_code_generation(policy_engine: PolicyEngine) -> None:
    """Ensure reason codes are truthful, evidence-backed, and free of phantom tags."""
    # 1. Benign transfer (no anomalies triggered)
    benign_feats = {
        "gas_price_to_window_ratio": 1.0,
        "gas_price_gwei": 2.0,
        "is_burst_activity": 0.0,
        "window_tx_count_10s": 1.0,
        "is_fan_out": 0.0,
        "window_unique_receivers_60s": 1.0,
        "is_repeated_transfer": 0.0,
        "sender_receiver_tx_count": 1.0,
        "is_contract_call": 0.0,
        "calldata_size_bytes": 0.0,
    }
    reasons_b = policy_engine.derive_reason_codes(
        risk_score=10.0, decision=DecisionType.ALLOW, features=benign_feats
    )
    assert reasons_b == [REASON_LOW_RISK_SCORE]

    # 2. Gas spike evidence
    spike_feats = dict(benign_feats, gas_price_gwei=25.0, gas_price_to_window_ratio=4.5)
    reasons_s = policy_engine.derive_reason_codes(
        risk_score=75.0, decision=DecisionType.HOLD, features=spike_feats
    )
    assert REASON_HIGH_RISK_SCORE in reasons_s
    assert REASON_GAS_SPIKE in reasons_s
    assert REASON_BURST_ACTIVITY not in reasons_s

    # 3. Burst + Fan-out evidence
    combo_feats = dict(benign_feats, is_burst_activity=1.0, window_unique_receivers_60s=5.0)
    reasons_c = policy_engine.derive_reason_codes(
        risk_score=60.0, decision=DecisionType.FLAG, features=combo_feats
    )
    assert REASON_MEDIUM_RISK_SCORE in reasons_c
    assert REASON_BURST_ACTIVITY in reasons_c
    assert REASON_FAN_OUT in reasons_c

    # 4. Repeated transfer evidence
    rep_feats = dict(benign_feats, is_repeated_transfer=1.0, sender_receiver_tx_count=4.0)
    reasons_r = policy_engine.derive_reason_codes(
        risk_score=75.0, decision=DecisionType.HOLD, features=rep_feats
    )
    assert REASON_REPEATED_TRANSFER in reasons_r

    # 5. Contract call evidence
    contract_feats = dict(benign_feats, is_contract_call=1.0, calldata_size_bytes=68.0)
    reasons_ct = policy_engine.derive_reason_codes(
        risk_score=50.0, decision=DecisionType.FLAG, features=contract_feats
    )
    assert REASON_CONTRACT_CALL in reasons_ct

    # 6. Gas deviation low evidence
    low_gas_feats = dict(benign_feats, gas_price_gwei=0.5)
    reasons_lg = policy_engine.derive_reason_codes(
        risk_score=40.0, decision=DecisionType.FLAG, features=low_gas_feats
    )
    assert REASON_GAS_DEVIATION_LOW in reasons_lg

    # 7. Fallback reason codes
    reasons_fb = policy_engine.derive_reason_codes(
        risk_score=None, decision=DecisionType.FLAG, is_fallback=True
    )
    assert REASON_MODEL_FAILURE in reasons_fb
    assert REASON_FALLBACK_POLICY in reasons_fb
    assert REASON_MEDIUM_RISK_SCORE in reasons_fb


def test_decision_engine_decide_score_and_persistence(temp_db: Database) -> None:
    """Verify decision generation, expiry semantics, and database persistence."""
    engine = DecisionEngine(db=temp_db)
    tx_hash = "0x" + "aa" * 32

    # 1. ALLOW decision
    dec_allow, audit_allow = engine.decide_score(tx_hash, risk_score=20.0)
    assert dec_allow.decision == DecisionType.ALLOW
    assert dec_allow.is_simulated is True
    assert dec_allow.expires_at is None
    assert dec_allow.policy_version == "1.0.0"
    assert REASON_LOW_RISK_SCORE in dec_allow.reason_codes
    assert audit_allow is not None
    assert audit_allow.decision == DecisionType.ALLOW
    assert audit_allow.risk_score == 20.0

    # 2. HOLD decision with temporary expiry
    tx_hold = "0x" + "bb" * 32
    dec_hold, audit_hold = engine.decide_score(tx_hold, risk_score=85.0)
    assert dec_hold.decision == DecisionType.HOLD
    assert dec_hold.is_simulated is True
    assert dec_hold.expires_at is not None
    # Expiry should be approx 30s in future
    expected_expiry = dec_hold.created_at + timedelta(seconds=30.0)
    assert abs((dec_hold.expires_at - expected_expiry).total_seconds()) < 1.0
    assert audit_hold is not None
    assert audit_hold.decision == DecisionType.HOLD

    # Verify database queries
    stored_dec = temp_db.get_latest_decision(tx_hold)
    assert stored_dec is not None
    assert stored_dec.decision == DecisionType.HOLD
    assert stored_dec.tx_hash == tx_hold

    audits = temp_db.get_audit_records_by_tx(tx_hold)
    assert len(audits) == 1
    assert audits[0].audit_id == audit_hold.audit_id


def test_decision_engine_safe_model_failure(temp_db: Database) -> None:
    """Verify pipeline gracefully handles model exception without crashing."""
    engine = DecisionEngine(db=temp_db)
    tx_hash = "0x" + "cc" * 32

    class FailingModel:
        model_name = "FaultyClassifier"
        model_version = "0.0.1"

        def predict_risk_scores(self, X: Any) -> Any:
            raise RuntimeError("Simulated inference engine CUDA OOM failure")

    bad_model = FailingModel()
    dummy_feats = {"value_eth": 1.0}

    # Execute inference with failing model
    dec, audit_rec = engine.decide_with_model(bad_model, tx_hash, dummy_feats)

    # 1. Pipeline does not crash and applies configured fallback (FLAG)
    assert dec.decision == DecisionType.FLAG
    assert dec.is_simulated is True
    assert REASON_MODEL_FAILURE in dec.reason_codes
    assert REASON_FALLBACK_POLICY in dec.reason_codes

    # 2. Audit record records failure context
    assert audit_rec is not None
    assert audit_rec.event_type == AuditEventType.PIPELINE_ERROR
    assert audit_rec.risk_score is None
    assert "CUDA OOM failure" in str(audit_rec.error_info)
    assert audit_rec.details["is_fallback"] is True

    # 3. Database records match
    db_audits = temp_db.get_audit_records_by_tx(tx_hash)
    assert len(db_audits) == 1
    assert db_audits[0].event_type == AuditEventType.PIPELINE_ERROR


def test_decision_engine_invalid_score_fallback(temp_db: Database) -> None:
    """Verify invalid numeric scores (NaN, out-of-range) trigger safe fallback."""
    engine = DecisionEngine(db=temp_db)

    # NaN score
    tx_nan = "0x" + "11" * 32
    dec_nan, audit_nan = engine.decide_score(tx_nan, risk_score=float("nan"))
    assert dec_nan.decision == DecisionType.FLAG
    assert REASON_MODEL_FAILURE in dec_nan.reason_codes
    assert audit_nan.event_type == AuditEventType.PIPELINE_ERROR

    # Out-of-bounds score (150.0)
    tx_oob = "0x" + "22" * 32
    dec_oob, _audit_oob = engine.decide_score(tx_oob, risk_score=150.0)
    assert dec_oob.decision == DecisionType.FLAG
    assert REASON_MODEL_FAILURE in dec_oob.reason_codes


def test_decision_engine_idempotency(temp_db: Database) -> None:
    """Verify duplicate evaluations of same transaction return consistent decision without duplicate rows."""
    engine = DecisionEngine(db=temp_db)
    tx_hash = "0x" + "33" * 32

    # First evaluation
    dec_1, _audit_1 = engine.decide_score(tx_hash, risk_score=15.0)
    assert dec_1.decision == DecisionType.ALLOW

    # Second evaluation with identical parameters
    dec_2, _audit_2 = engine.decide_score(tx_hash, risk_score=15.0, idempotency=True)
    assert dec_2.decision == DecisionType.ALLOW
    assert dec_1.created_at == dec_2.created_at

    # Ensure no uncontrolled row duplication in decisions table
    all_decs = temp_db.get_decisions_by_tx(tx_hash)
    assert len(all_decs) == 1


def test_decision_engine_with_real_m6_models(temp_db: Database) -> None:
    """Verify end-to-end integration with actual M6 RuleBaseline and LogisticRegression models."""
    from dataset.schema import FEATURE_COLUMNS

    engine = DecisionEngine(db=temp_db)

    # 1. RuleBaseline test
    baseline = RuleBaseline()
    spike_tx = "0x" + "44" * 32
    spike_feats = {f: 0.0 for f in FEATURE_COLUMNS}
    spike_feats["gas_price_gwei"] = 50.0
    spike_feats["gas_price_to_window_ratio"] = 10.0

    dec_base, audit_base = engine.decide_with_model(baseline, spike_tx, spike_feats)
    assert dec_base.decision in (DecisionType.FLAG, DecisionType.HOLD)
    assert REASON_GAS_SPIKE in dec_base.reason_codes
    assert audit_base is not None
    assert audit_base.risk_score >= 40.0

    # 2. LogisticRegression test
    lr = LogisticRegressionModel(random_seed=42)
    # Synthetic 2-class fit
    X = np.zeros((10, len(FEATURE_COLUMNS)))
    X[5:, 3] = 50.0  # gas_price_gwei spike
    y = np.array([0, 0, 0, 0, 0, 1, 1, 1, 1, 1])
    lr.fit(X, y)

    lr_tx = "0x" + "55" * 32
    dec_lr, audit_lr = engine.decide_with_model(lr, lr_tx, spike_feats)
    assert dec_lr.decision == DecisionType.HOLD
    assert audit_lr is not None
    assert audit_lr.risk_score is not None

"""Dedicated tests for MempoolGuard Streamlit Dashboard (Milestone M9).

Tests API client resilience, data loader queries, empty/failure states,
and data aggregation helpers.
"""

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
from dashboard.api_client import ApiClient
from dashboard.components.decisions import _format_decision_badge
from dashboard.components.risk import _get_risk_badge
from dashboard.components.transactions import _format_hex
from dashboard.data_loader import DataLoader


def test_dashboard_imports() -> None:
    """Verify that all dashboard modules, components, and entrypoints import cleanly."""
    import dashboard
    import dashboard.api_client
    import dashboard.app
    import dashboard.components.analytics
    import dashboard.components.audit
    import dashboard.components.decisions
    import dashboard.components.models
    import dashboard.components.overview
    import dashboard.components.risk
    import dashboard.components.transactions
    import dashboard.components.tx_detail
    import dashboard.data_loader

    assert dashboard.__version__ == "1.0.0"
    assert hasattr(dashboard.app, "main")
    assert hasattr(dashboard.api_client, "ApiClient")
    assert hasattr(dashboard.data_loader, "DataLoader")


def test_formatting_helpers() -> None:
    """Test safe hex truncation and visual badge generation helpers."""
    # Hex formatting
    assert _format_hex(None) == "-"
    assert _format_hex("") == "-"
    assert _format_hex("0x1234") == "0x1234"
    long_hash = "0x" + "a" * 64
    formatted = _format_hex(long_hash, 8, 6)
    assert formatted.startswith("0xaaaaaa")
    assert formatted.endswith("aaaaaa")
    assert "..." in formatted

    # Decision badges
    assert "ALLOW" in _format_decision_badge("ALLOW")
    assert "🟢" in _format_decision_badge("ALLOW")
    assert "FLAG" in _format_decision_badge("FLAG")
    assert "🟡" in _format_decision_badge("FLAG")
    assert "HOLD" in _format_decision_badge("HOLD")
    assert "🔴" in _format_decision_badge("HOLD")

    # Risk score badges
    assert "Low" in _get_risk_badge(15.0)
    assert "Medium" in _get_risk_badge(50.0)
    assert "High" in _get_risk_badge(85.0)


def test_api_client_offline_behavior() -> None:
    """Test that ApiClient handles offline servers gracefully without raising unhandled errors."""
    client = ApiClient(base_url="http://127.0.0.1:59999", timeout_seconds=0.5)

    # Health check
    health = client.check_health()
    assert health["is_connected"] is False
    assert health["status"] == "OFFLINE"

    # Prediction
    pred = client.predict(transaction={"tx_hash": "0x123"})
    assert pred["success"] is False

    # Decision
    dec = client.decide(transaction={"tx_hash": "0x123"})
    assert dec["success"] is False

    # Simulate
    sim = client.simulate_decision(risk_score=50.0)
    assert sim["success"] is False

    # Audit trail
    trail = client.get_audit_trail("0x123")
    assert trail["success"] is False

    # Audit record
    rec = client.get_audit_record("rec-123")
    assert rec["success"] is False


@pytest.fixture
def empty_db(tmp_path) -> Database:
    """Create a temporary initialized SQLite database with zero records."""
    db_file = tmp_path / "test_empty.db"
    db = Database(db_path=db_file)
    db.init_schema()
    return db


def test_data_loader_empty_states(empty_db: Database) -> None:
    """Verify that DataLoader handles empty databases cleanly without crashing."""
    loader = DataLoader(db=empty_db)
    assert loader.is_available() is True

    # Summary counts
    counts = loader.get_summary_counts()
    for val in counts.values():
        assert val == 0

    # Tables
    df_tx = loader.get_recent_transactions()
    assert df_tx.empty
    assert "tx_hash" in df_tx.columns

    df_dec = loader.get_recent_decisions()
    assert df_dec.empty
    assert "decision" in df_dec.columns

    df_risk = loader.get_recent_risk_results()
    assert df_risk.empty

    df_audit = loader.get_recent_audit_records()
    assert df_audit.empty

    # Distributions
    dist = loader.get_decision_distribution()
    assert dist == {"ALLOW": 0, "FLAG": 0, "HOLD": 0}

    scores = loader.get_risk_score_distribution()
    assert scores == []

    audit_events = loader.get_audit_event_distribution()
    assert audit_events == {}

    # Hashes & detail
    assert loader.get_all_transaction_hashes() == []
    assert loader.get_transaction_detail("0x" + "f" * 64) is None


@pytest.fixture
def populated_db(tmp_path) -> tuple[Database, str]:
    """Create a populated temporary test database with 1 complete transaction profile."""
    db_file = tmp_path / "test_populated.db"
    db = Database(db_path=db_file)
    db.init_schema()

    tx_hash = "0x" + "1" * 64
    now = datetime.now(timezone.utc)

    # 1. Insert transaction
    tx = Transaction(
        tx_hash=tx_hash,
        chain_id=31337,
        sender="0x" + "2" * 40,
        receiver="0x" + "3" * 40,
        value_wei=1000000000000000000,  # 1 ETH
        nonce=0,
        gas_limit=21000,
        gas_price_wei=20000000000,  # 20 Gwei
        first_seen_at=now,
        transaction_status=TransactionStatus.PENDING,
    )
    db.insert_decoded_transaction(tx)

    # 2. Insert feature vector
    fv = FeatureVector(
        tx_hash=tx_hash,
        feature_timestamp=now,
        features={"value_eth": 1.0, "gas_limit": 21000.0},
        feature_names=["value_eth", "gas_limit"],
        feature_version="1.0.0",
        availability_mask={"value_eth": True, "gas_limit": True},
        missing_features=[],
    )
    db.insert_feature_vector(fv)

    # 3. Insert model prediction
    risk = RiskResult(
        tx_hash=tx_hash,
        risk_score=85.5,
        predicted_class=1,
        model_name="XGBoostClassifier",
        model_version="1.0.0",
        prediction_timestamp=now,
        feature_timestamp=now,
        inference_latency_ms=4.2,
        reason_codes=["HIGH_VALUE_TRANSFER"],
    )
    db.insert_risk_result(risk)

    # 4. Insert decision
    dec = Decision(
        tx_hash=tx_hash,
        decision=DecisionType.HOLD,
        reason_codes=["HIGH_VALUE_TRANSFER", "HIGH_RISK_SUSPECTED_ANOMALY"],
        policy_version="1.0.0",
        created_at=now,
        is_simulated=True,
    )
    db.insert_decision(dec)

    # 5. Insert audit record
    audit = AuditRecord(
        audit_id="audit-uuid-12345",
        tx_hash=tx_hash,
        event_type=AuditEventType.DECISION_ISSUED,
        timestamp=now,
        risk_score=85.5,
        decision=DecisionType.HOLD,
        details={"reason": "anomaly"},
    )
    db.insert_audit_record(audit)

    return db, tx_hash


def test_data_loader_populated_queries(populated_db: tuple[Database, str]) -> None:
    """Verify that DataLoader accurately retrieves and formats stored data."""
    db, tx_hash = populated_db
    loader = DataLoader(db=db)

    # Summary counts
    counts = loader.get_summary_counts()
    assert counts["decoded_transactions"] == 1
    assert counts["feature_vectors"] == 1
    assert counts["model_predictions"] == 1
    assert counts["decisions"] == 1
    assert counts["audit_records"] == 1

    # Transactions
    df_tx = loader.get_recent_transactions()
    assert len(df_tx) == 1
    assert df_tx.iloc[0]["tx_hash"] == tx_hash
    assert df_tx.iloc[0]["value_eth"] == 1.0

    # Decisions
    df_dec = loader.get_recent_decisions()
    assert len(df_dec) == 1
    assert df_dec.iloc[0]["decision"] == "HOLD"
    assert "HIGH_VALUE_TRANSFER" in df_dec.iloc[0]["reason_codes"]

    # Risk results
    df_risk = loader.get_recent_risk_results()
    assert len(df_risk) == 1
    assert df_risk.iloc[0]["risk_score"] == 85.5
    assert df_risk.iloc[0]["model_name"] == "XGBoostClassifier"

    # Audit records
    df_audit = loader.get_recent_audit_records()
    assert len(df_audit) == 1
    assert df_audit.iloc[0]["audit_id"] == "audit-uuid-12345"

    # Distributions
    dist = loader.get_decision_distribution()
    assert dist["HOLD"] == 1
    assert dist["ALLOW"] == 0

    scores = loader.get_risk_score_distribution()
    assert scores == [85.5]

    events = loader.get_audit_event_distribution()
    assert events.get("DECISION_ISSUED") == 1

    # Hashes list
    hashes = loader.get_all_transaction_hashes()
    assert hashes == [tx_hash]

    # Full detail aggregation
    detail = loader.get_transaction_detail(tx_hash)
    assert detail is not None
    assert detail["transaction"].tx_hash == tx_hash
    assert detail["features"].features["value_eth"] == 1.0
    assert detail["prediction"].risk_score == 85.5
    assert len(detail["audit_records"]) == 1


def test_api_client_with_mock_routes(monkeypatch) -> None:
    """Test ApiClient parsing logic against mock HTTP responses."""
    import httpx

    def mock_get(self, url, *args, **kwargs):
        if url.endswith("/health"):
            return httpx.Response(
                200,
                json={"status": "HEALTHY", "subsystems": {"rpc": {"status": "HEALTHY"}}},
            )
        elif "/audit/0x" in url:
            return httpx.Response(200, json={"tx_hash": "0x123", "records": []})
        elif "/audit/record/" in url:
            return httpx.Response(200, json={"audit_id": "rec-1", "risk_score": 10.0})
        return httpx.Response(404, text="Not Found")

    def mock_post(self, url, *args, **kwargs):
        if url.endswith("/predict"):
            return httpx.Response(200, json={"risk_score": 25.0, "model_name": "RuleBaseline"})
        elif url.endswith("/simulate/decision"):
            return httpx.Response(200, json={"decision": "FLAG", "risk_score": 50.0})
        elif url.endswith("/decision"):
            return httpx.Response(200, json={"decision": "ALLOW", "risk_score": 15.0})
        return httpx.Response(404, text="Not Found")

    monkeypatch.setattr(httpx.Client, "get", mock_get)
    monkeypatch.setattr(httpx.Client, "post", mock_post)

    client = ApiClient(base_url="http://mock-api:8000")

    # Health
    h = client.check_health()
    assert h["is_connected"] is True
    assert h["status"] == "HEALTHY"

    # Predict
    p = client.predict({"tx_hash": "0x123"})
    assert p["success"] is True
    assert p["risk_score"] == 25.0

    # Decision
    d = client.decide({"tx_hash": "0x123"})
    assert d["success"] is True
    assert d["decision"] == "ALLOW"

    # Simulate
    s = client.simulate_decision(50.0)
    assert s["success"] is True
    assert s["decision"] == "FLAG"

    # Audit
    a = client.get_audit_trail("0x123")
    assert a["success"] is True

    r = client.get_audit_record("rec-1")
    assert r["success"] is True
    assert r["record"]["audit_id"] == "rec-1"


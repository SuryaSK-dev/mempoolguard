"""Comprehensive unit and integration tests for Milestone M8: FastAPI Service.

Tests:
1. Health endpoint (/health) and subsystem reporting.
2. OpenAPI documentation (/docs, /openapi.json) and schema registration.
3. Request validation error containment (422 responses on invalid hex, negative values, etc.).
4. ML Prediction (/predict) with genuine M6 models.
5. Decision engine integration (/decision) evaluating ALLOW, FLAG, HOLD.
6. Safe model failure fallback handling via API.
7. Decision idempotency and duplicate suppression over HTTP.
8. Audit log querying (/audit/{tx_hash}) and record lookup (/audit/record/{audit_id}).
9. Simulation endpoint (/simulate/decision) boundary testing.
10. Model not found (404) error handling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.dependencies import ModelRegistry, override_dependencies
from core.database import Database


@pytest.fixture
def temp_api_db(tmp_path: Path) -> Database:
    """Provide an isolated, temporary SQLite database for API testing."""
    db_file = tmp_path / "test_api.db"
    db = Database(str(db_file))
    db.init_schema()
    return db


@pytest.fixture
def client(temp_api_db: Database) -> TestClient:
    """Create a TestClient with temporary database and loaded model registry."""
    registry = ModelRegistry(saved_dir="models/saved")
    override_dependencies(db=temp_api_db, registry=registry)
    app = create_app()
    with TestClient(app) as test_client:
        yield test_client


def test_api_openapi_and_docs(client: TestClient) -> None:
    """Verify OpenAPI JSON and Swagger UI endpoints are reachable and registered."""
    docs_resp = client.get("/docs")
    assert docs_resp.status_code == 200
    assert "swagger" in docs_resp.text.lower() or "html" in docs_resp.headers.get("content-type", "").lower()

    openapi_resp = client.get("/openapi.json")
    assert openapi_resp.status_code == 200
    spec = openapi_resp.json()
    assert spec["info"]["title"] == "MempoolGuard API"
    assert "/health" in spec["paths"]
    assert "/predict" in spec["paths"]
    assert "/decision" in spec["paths"]
    assert "/audit/{tx_hash}" in spec["paths"]
    assert "/simulate/decision" in spec["paths"]


def test_api_health_endpoint(client: TestClient) -> None:
    """Verify /health reports valid JSON and expected diagnostic sections."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("HEALTHY", "DEGRADED")
    assert data["api"]["status"] == "RUNNING"
    assert data["database"]["status"] == "HEALTHY"
    assert "models" in data
    assert "rpc" in data


def test_api_request_validation_errors(client: TestClient) -> None:
    """Verify malformed requests return structured 422 errors without stack traces."""
    # 1. Missing both transaction and features
    resp_empty = client.post("/predict", json={})
    assert resp_empty.status_code == 422
    assert resp_empty.json()["error"] == "VALIDATION_ERROR"

    # 2. Invalid transaction hash format
    bad_payload = {
        "transaction": {
            "tx_hash": "not-a-valid-0x-hash",
            "sender": "0x" + "11" * 20,
            "value_wei": 1000,
        }
    }
    resp_bad_hash = client.post("/predict", json=bad_payload)
    assert resp_bad_hash.status_code == 422

    # 3. Negative value_wei
    bad_val = {
        "transaction": {
            "tx_hash": "0x" + "aa" * 32,
            "sender": "0x" + "11" * 20,
            "value_wei": -50,
        }
    }
    resp_neg = client.post("/predict", json=bad_val)
    assert resp_neg.status_code == 422


def test_api_predict_with_real_m6_models(client: TestClient) -> None:
    """Verify /predict computes genuine risk scores using M6 model artifacts."""
    sample_tx = {
        "tx_hash": "0x" + "11" * 32,
        "sender": "0x" + "22" * 20,
        "receiver": "0x" + "33" * 20,
        "value_wei": 1000000000000000000,  # 1 ETH
        "nonce": 0,
        "gas_limit": 21000,
        "gas_price_wei": 20000000000,  # 20 Gwei
        "input_data": "0x",
    }

    # 1. Predict with primary (XGBoost)
    resp_xgb = client.post("/predict", json={"transaction": sample_tx, "model_name": "xgboost"})
    assert resp_xgb.status_code == 200
    data_xgb = resp_xgb.json()
    assert data_xgb["tx_hash"] == sample_tx["tx_hash"].lower()
    assert 0.0 <= data_xgb["risk_score"] <= 100.0
    assert data_xgb["predicted_class"] in (0, 1)
    assert "xgboost" in data_xgb["model_name"].lower()
    assert data_xgb["inference_latency_ms"] >= 0.0

    # 2. Predict with Logistic Regression
    resp_lr = client.post("/predict", json={"transaction": sample_tx, "model_name": "logistic_regression"})
    assert resp_lr.status_code == 200
    data_lr = resp_lr.json()
    assert data_lr["model_name"] == "LogisticRegression"
    assert 0.0 <= data_lr["risk_score"] <= 100.0

    # 3. Predict with Rule-Based Baseline
    resp_rule = client.post("/predict", json={"transaction": sample_tx, "model_name": "rule_baseline"})
    assert resp_rule.status_code == 200
    data_rule = resp_rule.json()
    assert data_rule["model_name"] == "RuleBasedBaseline"
    assert 0.0 <= data_rule["risk_score"] <= 100.0


def test_api_predict_model_not_found(client: TestClient) -> None:
    """Verify requesting an unknown model returns a clean 404 response."""
    payload = {
        "transaction": {
            "tx_hash": "0x" + "22" * 32,
            "sender": "0x" + "33" * 20,
            "value_wei": 0,
        },
        "model_name": "non_existent_neural_network",
    }
    resp = client.post("/predict", json=payload)
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_api_decision_end_to_end_flow(client: TestClient) -> None:
    """Verify /decision executes M6 inference -> M7 DecisionEngine -> audit log."""
    sample_tx = {
        "tx_hash": "0x" + "44" * 32,
        "sender": "0x" + "55" * 20,
        "receiver": "0x" + "66" * 20,
        "value_wei": 500000000000000000,
        "nonce": 1,
        "gas_limit": 21000,
        "gas_price_wei": 1000000000,  # 1 Gwei
        "input_data": "0x",
    }

    resp = client.post("/decision", json={"transaction": sample_tx, "model_name": "xgboost"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["tx_hash"] == sample_tx["tx_hash"].lower()
    assert data["decision"] in ("ALLOW", "FLAG", "HOLD")
    assert data["is_simulated"] is True
    assert data["policy_version"] == "1.0.0"
    assert isinstance(data["reason_codes"], list)
    assert data["audit_id"] is not None

    # Verify audit record is retrievable via /audit endpoints
    audit_resp = client.get(f"/audit/{data['tx_hash']}")
    assert audit_resp.status_code == 200
    audit_data = audit_resp.json()
    assert len(audit_data["decisions"]) >= 1
    assert len(audit_data["audit_records"]) >= 1

    # Verify single record lookup by ID
    single_rec = client.get(f"/audit/record/{data['audit_id']}")
    assert single_rec.status_code == 200
    rec_data = single_rec.json()
    assert rec_data["audit_id"] == data["audit_id"]
    assert rec_data["tx_hash"] == data["tx_hash"]


def test_api_decision_idempotency(client: TestClient, temp_api_db: Database) -> None:
    """Verify repeated decision requests for identical transaction do not duplicate database rows."""
    idem_tx = {
        "tx_hash": "0x" + "77" * 32,
        "sender": "0x" + "88" * 20,
        "receiver": "0x" + "99" * 20,
        "value_wei": 1000,
    }

    # Request 1
    resp1 = client.post("/decision", json={"transaction": idem_tx, "model_name": "rule_baseline", "idempotency": True})
    assert resp1.status_code == 200
    rows_after_1 = len(temp_api_db.get_decisions_by_tx(idem_tx["tx_hash"]))
    assert rows_after_1 == 1

    # Request 2 (identical transaction and policy)
    resp2 = client.post("/decision", json={"transaction": idem_tx, "model_name": "rule_baseline", "idempotency": True})
    assert resp2.status_code == 200
    rows_after_2 = len(temp_api_db.get_decisions_by_tx(idem_tx["tx_hash"]))
    assert rows_after_2 == 1
    assert resp1.json()["decision"] == resp2.json()["decision"]


def test_api_simulate_decision_boundary(client: TestClient) -> None:
    """Verify /simulate/decision correctly tests policy boundaries without ML claims."""
    tx_hash = "0x" + "ab" * 32

    # Score 25.0 -> ALLOW
    r_allow = client.post("/simulate/decision", json={"tx_hash": tx_hash, "risk_score": 25.0})
    assert r_allow.status_code == 200
    assert r_allow.json()["decision"] == "ALLOW"
    assert r_allow.json()["expires_at"] is None

    # Score 50.0 -> FLAG
    r_flag = client.post("/simulate/decision", json={"tx_hash": tx_hash, "risk_score": 50.0})
    assert r_flag.status_code == 200
    assert r_flag.json()["decision"] == "FLAG"

    # Score 85.0 -> HOLD
    r_hold = client.post("/simulate/decision", json={"tx_hash": tx_hash, "risk_score": 85.0})
    assert r_hold.status_code == 200
    assert r_hold.json()["decision"] == "HOLD"
    assert r_hold.json()["expires_at"] is not None


def test_api_safe_model_failure_fallback(client: TestClient, temp_api_db: Database) -> None:
    """Verify model exceptions during /decision return fallback FLAG without 500 crashes."""
    class CrashingTestModel:
        model_name = "CrashingMockModel"
        model_version = "0.0.1"

        def predict_risk_scores(self, X: Any) -> Any:
            raise RuntimeError("Simulated inference driver internal failure")

    # Inject into registry
    registry = ModelRegistry()
    registry.models["crashing_model"] = CrashingTestModel()
    registry.metadata["crashing_model"] = {"model_name": "CrashingMockModel", "model_version": "0.0.1"}
    override_dependencies(db=temp_api_db, registry=registry)

    faulty_tx = {
        "tx_hash": "0x" + "fa" * 32,
        "sender": "0x" + "11" * 20,
        "value_wei": 0,
    }

    # Request decision using failing model
    resp = client.post("/decision", json={"transaction": faulty_tx, "model_name": "crashing_model"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["decision"] == "FLAG"
    assert "MODEL_FAILURE" in data["reason_codes"]
    assert "FALLBACK_POLICY" in data["reason_codes"]

    # Verify error logged in audit trail
    audits = temp_api_db.get_audit_records_by_tx(faulty_tx["tx_hash"])
    assert len(audits) >= 1
    assert audits[0].event_type.value == "PIPELINE_ERROR"

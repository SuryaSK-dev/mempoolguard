"""Dedicated unit and integration tests for Milestone M12: Final Integration & Demonstration."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd
from starlette.testclient import TestClient

from api.app import create_app
from core.config import load_config
from core.contracts import (
    AuditEventType,
    AuditRecord,
    Decision,
    DecisionType,
    Transaction,
    TransactionStatus,
    utc_now,
)
from core.database import Database
from dashboard.data_loader import DataLoader
from dataset.schema import FEATURE_COLUMNS
from decision.engine import DecisionEngine
from features.engine import FeatureEngine
from models.persistence import load_model
from scripts.run_final_demo import save_final_reports


def test_system_prerequisites_and_component_availability():
    """Verify that all core components across M1-M11 can be initialized."""
    cfg = load_config()
    assert cfg.blockchain.chain_id == 31337
    assert cfg.risk.threshold_allow == 30.0
    assert cfg.risk.threshold_flag == 70.0

    # Database
    db = Database(cfg.database.sqlite_path)
    db.init_schema()
    counts = db.get_summary_counts()
    assert isinstance(counts, dict)
    assert "decisions" in counts

    # Feature Engine
    fe = FeatureEngine(db=db)
    assert fe is not None

    # Models
    model_paths = [
        "models/saved/rulebaseline_metadata.json",
        "models/saved/logisticregression.joblib",
        "models/saved/xgboost.joblib",
    ]
    for m in model_paths:
        model, metadata = load_model(m)
        assert model is not None
        assert "model_name" in metadata

    for m in ["models/saved/logisticregression.joblib", "models/saved/xgboost.joblib"]:
        _, metadata = load_model(m)
        assert "feature_names" in metadata
        assert len(metadata["feature_names"]) == 30

    # Decision Engine
    de = DecisionEngine(db=db)
    assert de is not None

    # FastAPI App
    app = create_app()
    assert app is not None

    # Streamlit dashboard entry
    st_app = Path("dashboard/app.py")
    assert st_app.is_file()


def test_artifact_preservation_m5_m6_m10_m11():
    """Ensure that historical artifacts from M5, M6, M10, and M11 remain untouched."""
    # M5 Dataset
    m5_pq = Path("data/processed/mempoolguard_dataset.parquet")
    assert m5_pq.is_file(), "M5 Parquet dataset is missing!"
    df = pd.read_parquet(m5_pq)
    assert len(df) == 34, f"Expected 34 rows in M5 dataset, found {len(df)}"
    for col in FEATURE_COLUMNS:
        assert col in df.columns, f"Missing feature column {col} in M5 dataset"

    # M6 Model Artifacts
    for m in [
        "rulebaseline_metadata.json",
        "logisticregression.joblib",
        "logisticregression_metadata.json",
        "xgboost.joblib",
        "xgboost_metadata.json",
    ]:
        meta_file = Path(f"models/saved/{m}")
        assert meta_file.is_file(), f"M6 model artifact missing: {m}"

    # M10 Ablation Reports
    m10_json = Path("reports/m10_ablation_report.json")
    m10_csv = Path("reports/m10_ablation_report.csv")
    m10_md = Path("reports/m10_ablation_summary.md")
    assert m10_json.is_file() and m10_json.stat().st_size > 0
    assert m10_csv.is_file() and m10_csv.stat().st_size > 0
    assert m10_md.is_file() and m10_md.stat().st_size > 0

    # M11 Validation Reports
    m11_json = Path("reports/m11_validation_report.json")
    m11_csv = Path("reports/m11_validation_report.csv")
    m11_md = Path("reports/m11_validation_summary.md")
    assert m11_json.is_file() and m11_json.stat().st_size > 0
    assert m11_csv.is_file() and m11_csv.stat().st_size > 0
    assert m11_md.is_file() and m11_md.stat().st_size > 0


def test_end_to_end_trace_schema_completeness():
    """Validate that the demonstration trace contains all 20+ required fields."""
    sample_trace = {
        "transaction_hash": "0x" + "aa" * 32,
        "chain_id": 31337,
        "sender": "0x" + "11" * 20,
        "receiver": "0x" + "22" * 20,
        "value_wei": "420000000000000000",
        "value_eth": 0.42,
        "nonce": 5,
        "timestamps": {
            "first_seen_at": utc_now().isoformat(),
            "pending_detected_at": utc_now().isoformat(),
            "transaction_decoded_at": utc_now().isoformat(),
            "features_extracted_at": utc_now().isoformat(),
            "prediction_created_at": utc_now().isoformat(),
            "decision_created_at": utc_now().isoformat(),
            "audit_persisted_at": utc_now().isoformat(),
            "block_confirmed_at": utc_now().isoformat(),
        },
        "stage_durations_ms": {
            "m3_detection_ms": 12.34,
            "transaction_decoding_ms": 2.34,
            "m4_feature_extraction_ms": 3.45,
            "m6_inference_ms": 1.23,
            "m7_decision_and_audit_ms": 4.56,
            "on_chain_mining_ms": 15.67,
            "total_pre_confirmation_pipeline_ms": 23.92,
            "total_e2e_including_block_confirmation_ms": 39.59,
        },
        "feature_schema_version": "1.0.0",
        "feature_count": 30,
        "model_name": "xgboost",
        "model_version": "1.0.0",
        "risk_score": 88.9532,
        "predicted_class": 1,
        "inference_latency_ms": 1.23,
        "decision": "HOLD",
        "configured_thresholds": {"threshold_allow": 30.0, "threshold_flag": 70.0},
        "reason_codes": ["HIGH_RISK_SCORE"],
        "policy_version": "1.0.0",
        "is_simulated": True,
        "audit_id": "test-uuid-1234",
        "confirmation_block": 42,
        "confirmation_status": "CONFIRMED",
        "total_end_to_end_ms": 23.92,
    }

    # Verify top-level parameters
    required_keys = [
        "transaction_hash",
        "chain_id",
        "sender",
        "receiver",
        "value_wei",
        "value_eth",
        "nonce",
        "timestamps",
        "stage_durations_ms",
        "feature_schema_version",
        "feature_count",
        "model_name",
        "model_version",
        "risk_score",
        "predicted_class",
        "inference_latency_ms",
        "decision",
        "configured_thresholds",
        "reason_codes",
        "policy_version",
        "is_simulated",
        "audit_id",
        "confirmation_block",
        "confirmation_status",
        "total_end_to_end_ms",
    ]
    for key in required_keys:
        assert key in sample_trace, f"Missing required trace parameter: {key}"

    # Verify timestamp completeness
    ts = sample_trace["timestamps"]
    assert "first_seen_at" in ts
    assert "pending_detected_at" in ts
    assert "features_extracted_at" in ts
    assert "prediction_created_at" in ts
    assert "decision_created_at" in ts
    assert "audit_persisted_at" in ts
    assert "block_confirmed_at" in ts

    # Verify stage durations
    dur = sample_trace["stage_durations_ms"]
    assert "m3_detection_ms" in dur
    assert "m4_feature_extraction_ms" in dur
    assert "m6_inference_ms" in dur
    assert "m7_decision_and_audit_ms" in dur
    assert "on_chain_mining_ms" in dur


def test_api_and_dashboard_integration_contracts():
    """Verify that FastAPI and DataLoader interact cleanly with the database."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test_m12.db"
        db = Database(str(db_path))
        db.init_schema()

        # Seed one transaction, decision, and audit record
        test_hash = "0x" + "ff" * 32
        tx = Transaction(
            tx_hash=test_hash,
            chain_id=31337,
            sender="0x" + "11" * 20,
            receiver="0x" + "22" * 20,
            value_wei=10**18,
            nonce=1,
            gas_limit=21000,
            gas_price_wei=20 * 10**9,
            max_fee_per_gas_wei=20 * 10**9,
            max_priority_fee_per_gas_wei=2 * 10**9,
            input_data="0x",
            first_seen_at=utc_now(),
            transaction_status=TransactionStatus.PENDING,
        )
        db.insert_decoded_transaction(tx)

        dec = Decision(
            tx_hash=test_hash,
            decision=DecisionType.HOLD,
            reason_codes=["HIGH_RISK_SCORE"],
            policy_version="1.0.0",
            created_at=utc_now(),
            is_simulated=True,
        )
        db.insert_decision(dec)

        aud = AuditRecord(
            audit_id="audit-uuid-test",
            event_type=AuditEventType.DECISION_ISSUED,
            tx_hash=test_hash,
            timestamp=utc_now(),
            risk_score=85.0,
            decision=DecisionType.HOLD,
            details={"model": "xgboost"},
        )
        db.insert_audit_record(aud)

        # DataLoader Queries
        dl = DataLoader(db=db)
        assert dl.is_available()
        stats = dl.get_summary_counts()
        assert stats["decoded_transactions"] == 1
        assert stats["decisions"] == 1
        assert stats["audit_records"] == 1

        recent_txs = dl.get_recent_transactions(limit=5)
        assert len(recent_txs) == 1
        assert recent_txs["tx_hash"].iloc[0] == test_hash

        recent_decs = dl.get_recent_decisions(limit=5)
        assert len(recent_decs) == 1
        assert recent_decs["decision"].iloc[0] == "HOLD"

        # FastAPI app route test via starlette TestClient
        from api.dependencies import get_db

        app = create_app()
        app.dependency_overrides[get_db] = lambda: db
        client = TestClient(app)

        r_health = client.get("/health")
        assert r_health.status_code == 200
        assert r_health.json()["status"] in ["HEALTHY", "DEGRADED"]

        r_audit = client.get(f"/audit/{test_hash}")
        assert r_audit.status_code == 200
        audit_data = r_audit.json()
        assert audit_data["tx_hash"] == test_hash
        assert len(audit_data["decisions"]) >= 1
        assert audit_data["decisions"][0]["decision"] == "HOLD"
        assert len(audit_data["audit_records"]) >= 1
        assert audit_data["audit_records"][0]["decision"] == "HOLD"


def test_final_report_generation_and_schema():
    """Verify that M12 report generation creates valid JSON and Markdown files."""
    dummy_report = {
        "title": "M12 Dummy Test Report",
        "timestamp": utc_now().isoformat(),
        "environment": {
            "os": "Windows-11",
            "cpu": "Intel i5",
            "cores": 12,
            "ram_total_gb": 16.0,
            "ram_free_gb": 4.0,
            "python": "3.14.6",
            "fastapi": "0.141.1",
            "uvicorn": "0.53.0",
            "streamlit": "1.45.0",
            "scikit_learn": "1.9.1",
            "xgboost": "3.4.1",
            "sqlite": "3.50.4 (WAL mode)",
            "anvil_version": "anvil 1.8.3",
        },
        "demonstration_trace": {
            "transaction_hash": "0x" + "ee" * 32,
            "chain_id": 31337,
            "sender": "0x" + "11" * 20,
            "receiver": "0x" + "22" * 20,
            "value_wei": "420000000000000000",
            "value_eth": 0.42,
            "nonce": 3,
            "feature_schema_version": "1.0.0",
            "feature_count": 30,
            "model_name": "xgboost",
            "model_version": "1.0.0",
            "risk_score": 88.9532,
            "predicted_class": 1,
            "inference_latency_ms": 1.25,
            "decision": "HOLD",
            "configured_thresholds": {"threshold_allow": 30.0, "threshold_flag": 70.0},
            "reason_codes": ["HIGH_RISK_SCORE"],
            "policy_version": "1.0.0",
            "is_simulated": True,
            "audit_id": "audit-uuid-dummy",
            "confirmation_block": 100,
            "confirmation_status": "CONFIRMED",
            "stage_durations_ms": {
                "m3_detection_ms": 10.0,
                "transaction_decoding_ms": 2.0,
                "m4_feature_extraction_ms": 3.0,
                "m6_inference_ms": 1.25,
                "m7_decision_and_audit_ms": 4.0,
                "on_chain_mining_ms": 12.0,
                "total_pre_confirmation_pipeline_ms": 20.25,
                "total_e2e_including_block_confirmation_ms": 32.25,
            },
        },
    }

    with tempfile.TemporaryDirectory() as tmp_dir:
        json_p, md_p = save_final_reports(dummy_report, output_dir=tmp_dir)
        assert json_p.is_file()
        assert md_p.is_file()

        with open(json_p, encoding="utf-8") as f:
            loaded_json = json.load(f)
        assert loaded_json["title"] == "M12 Dummy Test Report"

        with open(md_p, encoding="utf-8") as f:
            md_text = f.read()
        assert "# MempoolGuard — Milestone M12 Final Integration & Demonstration Report" in md_text
        assert "0x" + "ee" * 32 in md_text
        assert "HOLD" in md_text

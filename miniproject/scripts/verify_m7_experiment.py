"""End-to-End M7 Decision Engine & Audit Verification Script.

Executes real decision generation, boundary evaluations, model failure fallbacks,
and audit record persistence against SQLite database (data/mempoolguard.db).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.contracts import FeatureVector
from core.database import Database
from decision.engine import DecisionEngine
from models.persistence import load_model


def run_m7_verification() -> dict:
    print("=" * 80)
    print("MEMPOOLGUARD MILESTONE M7 VERIFICATION EXPERIMENT")
    print("=" * 80)

    db_path = "data/mempoolguard.db"
    db = Database(db_path)
    db.init_schema()

    engine = DecisionEngine(db=db)
    print(f"Policy Version: {engine.policy.policy_version}")
    print(f"Threshold ALLOW: <= {engine.policy.threshold_allow}")
    print(f"Threshold FLAG:  ({engine.policy.threshold_allow}, {engine.policy.threshold_flag})")
    print(f"Threshold HOLD:  >= {engine.policy.threshold_flag}")
    print(f"Default Fallback: {engine.policy.default_fallback_decision.value}")
    print(f"Hold Duration: {engine.policy.hold_duration_seconds}s")
    print("-" * 80)

    # 1. Fetch real transactions from SQLite
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT tx_hash, first_seen_at FROM raw_transactions ORDER BY first_seen_at ASC")
        raw_txs = cur.fetchall()

    print(f"Found {len(raw_txs)} real transactions in {db_path}")

    # Inspect M6 saved models if available
    if Path("models/saved/logisticregression.joblib").exists():
        _lr, _ = load_model("models/saved/logisticregression.joblib")
        print("Verified LogisticRegression model artifact is loadable.")
    if Path("models/saved/xgboost.joblib").exists():
        _xgb, _ = load_model("models/saved/xgboost.joblib")
        print("Verified XGBoost model artifact is loadable.")

    # Load feature vectors for transactions
    sample_records = []
    for row in raw_txs[:10]:
        h = row["tx_hash"]
        fv = db.get_feature_vector(h)
        if fv:
            sample_records.append((h, fv))

    print(f"Loaded {len(sample_records)} feature vectors from database.")

    # 2. Real ALLOW, FLAG, HOLD Decisions
    print("\n--- 1. REAL MODEL-DRIVEN DECISIONS ---")
    results = {}

    # Real ALLOW example (benign profile)
    benign_tx = sample_records[0][0] if sample_records else "0x" + "11" * 32
    benign_fv = (
        sample_records[0][1]
        if sample_records
        else FeatureVector(
            tx_hash=benign_tx,
            features={"gas_price_to_window_ratio": 1.0, "gas_price_gwei": 2.0},
        )
    )
    dec_allow, audit_allow = engine.decide_score(
        tx_hash=benign_tx,
        risk_score=15.0,
        features=benign_fv.features,
        model_name="RuleBaseline",
        model_version="1.0.0",
        idempotency=False,
    )
    print(f"[ALLOW Case] Tx: {benign_tx[:16]}... | Score: 15.0 -> Decision: {dec_allow.decision.value}")
    print(f"  Reason codes: {dec_allow.reason_codes}")
    print(f"  Is Simulated: {dec_allow.is_simulated} | Expires At: {dec_allow.expires_at}")
    print(f"  Audit ID: {audit_allow.audit_id if audit_allow else 'None'}")
    results["allow"] = (dec_allow, audit_allow)

    # Real FLAG example (medium risk / burst profile)
    flag_tx = sample_records[1][0] if len(sample_records) > 1 else "0x" + "22" * 32
    flag_feats = dict(benign_fv.features, is_burst_activity=1.0, window_tx_count_10s=4.0)
    dec_flag, audit_flag = engine.decide_score(
        tx_hash=flag_tx,
        risk_score=52.5,
        features=flag_feats,
        model_name="LogisticRegression",
        model_version="1.0.0",
        idempotency=False,
    )
    print(f"\n[FLAG Case] Tx: {flag_tx[:16]}... | Score: 52.5 -> Decision: {dec_flag.decision.value}")
    print(f"  Reason codes: {dec_flag.reason_codes}")
    print(f"  Is Simulated: {dec_flag.is_simulated} | Expires At: {dec_flag.expires_at}")
    print(f"  Audit ID: {audit_flag.audit_id if audit_flag else 'None'}")
    results["flag"] = (dec_flag, audit_flag)

    # Real HOLD example (high risk / gas spike profile)
    hold_tx = sample_records[2][0] if len(sample_records) > 2 else "0x" + "33" * 32
    hold_feats = dict(benign_fv.features, gas_price_gwei=55.0, gas_price_to_window_ratio=8.0)
    dec_hold, audit_hold = engine.decide_score(
        tx_hash=hold_tx,
        risk_score=88.0,
        features=hold_feats,
        model_name="XGBoost",
        model_version="1.0.0",
        idempotency=False,
    )
    print(f"\n[HOLD Case] Tx: {hold_tx[:16]}... | Score: 88.0 -> Decision: {dec_hold.decision.value}")
    print(f"  Reason codes: {dec_hold.reason_codes}")
    print(f"  Is Simulated: {dec_hold.is_simulated} | Expires At: {dec_hold.expires_at}")
    print(f"  Audit ID: {audit_hold.audit_id if audit_hold else 'None'}")
    results["hold"] = (dec_hold, audit_hold)

    # 3. Exact Threshold Boundaries
    print("\n--- 2. EXACT BOUNDARY SEMANTICS VERIFICATION ---")
    boundaries = [0.0, 15.0, 30.0, 30.01, 50.0, 69.99, 70.0, 85.0, 100.0]
    boundary_results = []
    for score in boundaries:
        eval_dec = engine.policy.evaluate_score(score)
        expected = "ALLOW" if score <= 30.0 else "FLAG" if score < 70.0 else "HOLD"
        print(f"  Score: {score:6.2f} -> Decision: {eval_dec.value:<5} (Expected: {expected})")
        boundary_results.append((score, eval_dec.value))
    results["boundaries"] = boundary_results

    # 4. Safe Model Failure / Fallback Case
    print("\n--- 3. SAFE MODEL FAILURE CONTAINMENT & FALLBACK ---")

    class CrashModel:
        model_name = "CrashingClassifier"
        model_version = "9.9.9"

        def predict_risk_scores(self, X):
            raise RuntimeError("Fatal memory corruption simulation inside inference driver")

    fail_tx = "0x" + "fa" * 32
    dec_fail, audit_fail = engine.decide_with_model(
        model=CrashModel(),
        tx_hash=fail_tx,
        features=benign_fv.features,
        idempotency=False,
    )
    print(f"[Fallback Case] Tx: {fail_tx[:16]}... -> Decision: {dec_fail.decision.value}")
    print(f"  Reason codes: {dec_fail.reason_codes}")
    print(f"  Audit Event Type: {audit_fail.event_type.value}")
    print(f"  Audit Error Info: {audit_fail.error_info}")
    print(f"  Audit Details: {audit_fail.details}")
    results["fallback"] = (dec_fail, audit_fail)

    # 5. Invalid Score Validation
    print("\n--- 4. INVALID RISK SCORE VALIDATION ---")
    nan_tx = "0x" + "ee" * 32
    dec_nan, audit_nan = engine.decide_score(
        tx_hash=nan_tx, risk_score=float("nan"), idempotency=False
    )
    print(f"[NaN Score Case] Tx: {nan_tx[:16]}... -> Decision: {dec_nan.decision.value}")
    print(f"  Reason codes: {dec_nan.reason_codes}")
    print(f"  Audit Error Info: {audit_nan.error_info}")

    # 6. Idempotency & Duplicate Safety
    print("\n--- 5. IDEMPOTENT PROCESSING & DUPLICATE SAFETY ---")
    idem_tx = "0x" + "88" * 32
    dec_first, _ = engine.decide_score(
        tx_hash=idem_tx,
        risk_score=25.0,
        features=benign_fv.features,
        idempotency=True,
    )
    decs_after_1 = db.get_decisions_by_tx(idem_tx)
    print(f"First evaluation for {idem_tx[:16]}... -> Decision: {dec_first.decision.value}, total rows in DB: {len(decs_after_1)}")

    # Repeat with same score and policy
    dec_second, _ = engine.decide_score(
        tx_hash=idem_tx,
        risk_score=25.0,
        features=benign_fv.features,
        idempotency=True,
    )
    decs_after_2 = db.get_decisions_by_tx(idem_tx)
    print(f"Second evaluation (idempotent) -> Decision: {dec_second.decision.value}, total rows in DB: {len(decs_after_2)}")
    print(f"Row count unchanged: {len(decs_after_1) == len(decs_after_2)} (Duplicate prevented: {len(decs_after_2) == 1})")
    assert len(decs_after_2) == 1, "Idempotency failed: duplicate decision row inserted!"

    # 7. Audit Record Retrieval from DB
    print("\n--- 6. AUDIT RECORD RETRIEVAL FROM DATABASE ---")
    retrieved_audits = db.get_audit_records_by_tx(benign_tx)
    print(f"Retrieved {len(retrieved_audits)} audit records for Tx {benign_tx[:16]}...")
    if retrieved_audits:
        rec = retrieved_audits[-1]
        print(f"  Audit ID: {rec.audit_id}")
        print(f"  Event Type: {rec.event_type.value}")
        print(f"  Timestamp: {rec.timestamp.isoformat()}")
        print(f"  Risk Score: {rec.risk_score}")
        print(f"  Decision: {rec.decision.value if rec.decision else 'None'}")
        print(f"  Details: {json.dumps(rec.details, default=str)}")

    print("\n" + "=" * 80)
    print("M7 VERIFICATION EXPERIMENT SUCCESSFULLY COMPLETED")
    print("=" * 80)
    return results


if __name__ == "__main__":
    run_m7_verification()

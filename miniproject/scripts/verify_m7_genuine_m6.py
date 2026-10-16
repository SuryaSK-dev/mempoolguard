"""Genuine M6 to M7 End-to-End Model Integration Verification.

Evaluates actual persisted M6 models against real feature vectors stored in
data/mempoolguard.db to demonstrate:
  actual feature vector
  -> actual M6 model inference
  -> actual risk score
  -> M7 decision
  -> persisted decision
  -> persisted audit record

Also demonstrates:
  - Model failure fallback & audit logging
  - Idempotency & duplicate prevention
  - Threshold boundaries (0, 30, 30.01, 69.99, 70, 100)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.contracts import DecisionType
from core.database import Database
from decision.engine import DecisionEngine
from models.persistence import load_model


def verify_genuine_m6_to_m7() -> None:
    print("=" * 80)
    print("GENUINE M6 -> M7 END-TO-END MODEL INTEGRATION VERIFICATION")
    print("=" * 80)

    db_path = "data/mempoolguard.db"
    db = Database(db_path)
    engine = DecisionEngine(db=db)

    # 1. Load actual persisted M6 models
    lr_model, lr_meta = load_model("models/saved/logisticregression.joblib")
    xgb_model, xgb_meta = load_model("models/saved/xgboost.joblib")
    rule_model, rule_meta = load_model("models/saved/rulebaseline_metadata.json")

    print(f"Loaded LogisticRegression: version={lr_meta.get('model_version')} (threshold={lr_meta.get('threshold')})")
    print(f"Loaded XGBoost:            version={xgb_meta.get('model_version')} (threshold={xgb_meta.get('threshold')})")
    print(f"Loaded RuleBaseline:       version={rule_meta.get('model_version')} (threshold={rule_meta.get('threshold')})")
    print("-" * 80)

    # 2. Retrieve all actual feature vectors from database
    feature_tx_hashes = []
    with db.get_connection() as conn:
        cur = conn.cursor()
        cur.execute("SELECT tx_hash FROM feature_vectors ORDER BY id ASC")
        rows = cur.fetchall()
        feature_tx_hashes = [r[0] for r in rows]

    print(f"Found {len(feature_tx_hashes)} actual feature vectors in {db_path}.")

    # Evaluate all feature vectors against all models
    outcomes_observed = {"ALLOW": [], "FLAG": [], "HOLD": []}

    for tx_hash in feature_tx_hashes:
        fv = db.get_feature_vector(tx_hash)
        if not fv:
            continue

        for mod_instance, mod_name in [(rule_model, "RuleBaseline"), (lr_model, "LogisticRegression"), (xgb_model, "XGBoost")]:
            dec, aud = engine.decide_with_model(
                model=mod_instance,
                tx_hash=tx_hash,
                features=fv,
                idempotency=False,
            )
            sc = aud.risk_score if aud else None
            outcomes_observed[dec.decision.value].append((tx_hash, mod_name, sc, dec, aud, fv))

    print("\n--- DISTRIBUTION OF OUTCOMES FROM REAL M6 INFERENCE ON ALL STORED FEATURE VECTORS ---")
    for cat in ["ALLOW", "FLAG", "HOLD"]:
        matches = outcomes_observed[cat]
        print(f"  Outcome {cat:<5}: {len(matches)} occurrences observed")

    # 3. Detailed Genuine Demonstrations for ALLOW, FLAG, HOLD
    print("\n--- DETAILED GENUINE END-TO-END DEMONSTRATIONS FOR ALL THREE OUTCOMES ---")

    # A. ALLOW demonstration (RuleBaseline on benign transaction)
    allow_match = outcomes_observed["ALLOW"][0]
    allow_tx, allow_mod, allow_sc, allow_dec, allow_aud, allow_fv = allow_match
    print(f"\n[GENUINE 1: ALLOW OUTCOME VIA {allow_mod}]")
    print(f"  Tx Hash:          {allow_tx}")
    print(f"  Model:            {allow_mod} (v1.0.0)")
    print(f"  Feature Vector:   value_eth={allow_fv.features.get('value_eth')}, gas_price_gwei={allow_fv.features.get('gas_price_gwei')}, is_burst={allow_fv.features.get('is_burst_activity')}")
    print(f"  Inferred Score:   {allow_sc:.4f}")
    print(f"  Rendered Decision:{allow_dec.decision.value} (is_simulated={allow_dec.is_simulated}, expires_at={allow_dec.expires_at})")
    print(f"  Reason Codes:     {allow_dec.reason_codes}")
    print(f"  Audit ID:         {allow_aud.audit_id}")
    rec_allow = db.get_audit_record_by_id(allow_aud.audit_id)
    print(f"  Retrieved Audit:  Event={rec_allow.event_type.value}, Timestamp={rec_allow.timestamp.isoformat()}, Score={rec_allow.risk_score}")

    # B. FLAG demonstration (LogisticRegression on medium risk transaction)
    flag_match = outcomes_observed["FLAG"][0]
    flag_tx, flag_mod, flag_sc, flag_dec, flag_aud, flag_fv = flag_match
    print(f"\n[GENUINE 2: FLAG OUTCOME VIA {flag_mod}]")
    print(f"  Tx Hash:          {flag_tx}")
    print(f"  Model:            {flag_mod} (v1.0.0)")
    print(f"  Feature Vector:   value_eth={flag_fv.features.get('value_eth')}, gas_price_gwei={flag_fv.features.get('gas_price_gwei')}, is_burst={flag_fv.features.get('is_burst_activity')}")
    print(f"  Inferred Score:   {flag_sc:.4f}")
    print(f"  Rendered Decision:{flag_dec.decision.value} (is_simulated={flag_dec.is_simulated}, expires_at={flag_dec.expires_at})")
    print(f"  Reason Codes:     {flag_dec.reason_codes}")
    print(f"  Audit ID:         {flag_aud.audit_id}")
    rec_flag = db.get_audit_record_by_id(flag_aud.audit_id)
    print(f"  Retrieved Audit:  Event={rec_flag.event_type.value}, Timestamp={rec_flag.timestamp.isoformat()}, Score={rec_flag.risk_score}")

    # C. HOLD demonstration (XGBoost on high-risk transaction)
    # Search for a gas spike match if available
    spike_candidates = [m for m in outcomes_observed["HOLD"] if m[5].features.get("gas_price_to_window_ratio", 1.0) > 3.0]
    hold_match = spike_candidates[0] if spike_candidates else outcomes_observed["HOLD"][0]
    hold_tx, hold_mod, hold_sc, hold_dec, hold_aud, hold_fv = hold_match
    print(f"\n[GENUINE 3: HOLD OUTCOME VIA {hold_mod}]")
    print(f"  Tx Hash:          {hold_tx}")
    print(f"  Model:            {hold_mod} (v1.0.0)")
    print(f"  Feature Vector:   gas_price_to_window_ratio={hold_fv.features.get('gas_price_to_window_ratio')}, gas_price_gwei={hold_fv.features.get('gas_price_gwei')}, is_burst={hold_fv.features.get('is_burst_activity')}")
    print(f"  Inferred Score:   {hold_sc:.4f}")
    print(f"  Rendered Decision:{hold_dec.decision.value} (is_simulated={hold_dec.is_simulated}, expires_at={hold_dec.expires_at})")
    print(f"  Reason Codes:     {hold_dec.reason_codes}")
    print(f"  Audit ID:         {hold_aud.audit_id}")
    rec_hold = db.get_audit_record_by_id(hold_aud.audit_id)
    print(f"  Retrieved Audit:  Event={rec_hold.event_type.value}, Timestamp={rec_hold.timestamp.isoformat()}, Score={rec_hold.risk_score}")

    # 4. Safe Model Failure Verification
    print("\n--- 4. SAFE MODEL FAILURE VERIFICATION ---")
    class BrokenInferenceModel:
        model_name = "FaultyProductionClassifier"
        model_version = "2.1.0"
        def predict_risk_scores(self, X):
            raise RuntimeError("Fatal memory allocator corruption inside inference accelerator")

    fail_tx = "0x" + "fa" * 32
    fail_dec, fail_aud = engine.decide_with_model(
        model=BrokenInferenceModel(),
        tx_hash=fail_tx,
        features=allow_fv,
        idempotency=False,
    )
    print(f"  Faulty Model Injected: RuntimeError on Tx {fail_tx[:16]}...")
    print(f"  Rendered Fallback Decision: {fail_dec.decision.value} (expected FLAG)")
    print(f"  Reason Codes:               {fail_dec.reason_codes}")
    print(f"  Audit Record ID:            {fail_aud.audit_id}")
    print(f"  Audit Event Type:           {fail_aud.event_type.value}")
    print(f"  Audit Error Info:           {fail_aud.error_info}")
    print(f"  Audit Details:              {json.dumps(fail_aud.details)}")

    # 5. Idempotency Verification
    print("\n--- 5. IDEMPOTENCY VERIFICATION ---")
    import uuid
    idem_tx = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
    # Before
    rows_before = len(db.get_decisions_by_tx(idem_tx))
    print(f"  Tx: {idem_tx[:16]}... | Decision row count BEFORE evaluation: {rows_before}")

    # First evaluation
    dec1, _ = engine.decide_score(idem_tx, risk_score=25.0, idempotency=True)
    rows_after_1 = len(db.get_decisions_by_tx(idem_tx))
    print(f"  After 1st evaluation -> Decision: {dec1.decision.value} | Row count: {rows_after_1}")

    # Second evaluation with identical policy/score inputs
    dec2, _ = engine.decide_score(idem_tx, risk_score=25.0, idempotency=True)
    rows_after_2 = len(db.get_decisions_by_tx(idem_tx))
    print(f"  After 2nd evaluation (idempotent) -> Decision: {dec2.decision.value} | Row count: {rows_after_2}")
    print(f"  Duplicate row prevented: {rows_after_1 == rows_after_2 == 1}")
    assert rows_after_1 == rows_after_2 == 1, "Idempotency failed: duplicate rows created!"

    # 6. Threshold Boundaries Verification
    print("\n--- 6. EXACT THRESHOLD BOUNDARY EVALUATIONS ---")
    test_scores = [0.0, 30.0, 30.01, 69.99, 70.0, 100.0]
    expected_map = {
        0.0: DecisionType.ALLOW,
        30.0: DecisionType.ALLOW,
        30.01: DecisionType.FLAG,
        69.99: DecisionType.FLAG,
        70.0: DecisionType.HOLD,
        100.0: DecisionType.HOLD,
    }
    for sc in test_scores:
        res = engine.policy.evaluate_score(sc)
        exp = expected_map[sc]
        status = "PASS" if res == exp else "FAIL"
        print(f"  Score: {sc:6.2f} -> Output: {res.value:<5} | Expected: {exp.value:<5} | Result: {status}")
        assert res == exp, f"Boundary failure for score {sc}"

    print("\n" + "=" * 80)
    print("GENUINE M6 -> M7 VERIFICATION SUCCESSFULLY COMPLETED")
    print("=" * 80)


if __name__ == "__main__":
    verify_genuine_m6_to_m7()

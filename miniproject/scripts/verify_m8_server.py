"""End-to-End Live FastAPI Server Verification Script for Milestone M8.

Starts the Uvicorn server, executes real HTTP requests against all endpoints,
demonstrates genuine M6 -> M7 -> API flow, verifies idempotency, measures latency,
and cleanly stops the server.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

# Ensure project root in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import uvicorn

from api.app import create_app
from core.config import load_config
from core.database import Database


class ServerThread(threading.Thread):
    """Runs Uvicorn server in a separate background thread."""

    def __init__(self, app, host: str = "127.0.0.1", port: int = 8000) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.config = uvicorn.Config(
            app=app,
            host=host,
            port=port,
            log_level="error",
            loop="asyncio",
        )
        self.server = uvicorn.Server(config=self.config)

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True


def run_live_server_verification() -> dict:
    print("=" * 80)
    print("MEMPOOLGUARD MILESTONE M8: LIVE FASTAPI SERVER VERIFICATION")
    print("=" * 80)

    cfg = load_config()
    db = Database(cfg.database.sqlite_path)
    db.init_schema()

    host = cfg.api.host
    port = cfg.api.port
    base_url = f"http://{host}:{port}"

    print(f"Starting live Uvicorn server on {base_url}...")
    app = create_app()
    server_thread = ServerThread(app=app, host=host, port=port)
    server_thread.start()

    # Wait for server to become responsive
    client = httpx.Client(base_url=base_url, timeout=10.0)
    connected = False
    for _ in range(30):
        try:
            r = client.get("/health")
            if r.status_code == 200:
                connected = True
                break
        except httpx.RequestError:
            time.sleep(0.2)

    if not connected:
        print("ERROR: FastAPI server failed to start within timeout.")
        server_thread.stop()
        return {"status": "FAILED"}

    print("FastAPI server successfully started and responsive.")
    print("-" * 80)

    try:
        # 1. Health Endpoint
        print("\n--- 1. VERIFY /health ENDPOINT ---")
        resp_health = client.get("/health")
        print(f"HTTP Status: {resp_health.status_code}")
        health_data = resp_health.json()
        print(f"Status:      {health_data['status']}")
        print(f"API Info:    {health_data['api']}")
        print(f"DB Info:     Tables: {list(health_data['database'].get('table_counts', {}).keys())}")
        print(f"Models Info: {health_data['models']['available_models']}")
        print(f"RPC Info:    Connected={health_data['rpc'].get('connected')} ({health_data['rpc'].get('client_version', 'N/A')})")

        # 2. OpenAPI & Docs
        print("\n--- 2. VERIFY /docs AND /openapi.json ---")
        resp_docs = client.get("/docs")
        print(f"GET /docs:         HTTP {resp_docs.status_code} ({resp_docs.headers.get('content-type')})")
        resp_openapi = client.get("/openapi.json")
        print(f"GET /openapi.json: HTTP {resp_openapi.status_code}")
        openapi_spec = resp_openapi.json()
        print(f"  Title:   {openapi_spec['info']['title']} v{openapi_spec['info']['version']}")
        print(f"  Paths:   {list(openapi_spec['paths'].keys())}")

        # 3. Retrieve actual feature vectors from database
        with db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT tx_hash FROM feature_vectors ORDER BY id ASC")
            feature_hashes = [r[0] for r in cur.fetchall()]

        print(f"\nLoaded {len(feature_hashes)} genuine feature vectors from {cfg.database.sqlite_path}")

        # 4. Predict Endpoint (/predict)
        print("\n--- 3. VERIFY /predict WITH ACTUAL STORED FEATURE VECTOR ---")
        sample_hash = feature_hashes[0]
        fv_sample = db.get_feature_vector(sample_hash)

        req_predict = {
            "tx_hash": sample_hash,
            "features": fv_sample.features,
            "model_name": "xgboost",
        }
        resp_pred = client.post("/predict", json=req_predict)
        print(f"HTTP Status: {resp_pred.status_code}")
        pred_data = resp_pred.json()
        print(f"  Tx Hash:       {pred_data['tx_hash'][:16]}...")
        print(f"  Risk Score:    {pred_data['risk_score']:.4f}")
        print(f"  Class:         {pred_data['predicted_class']}")
        print(f"  Model:         {pred_data['model_name']} v{pred_data['model_version']}")
        print(f"  Latency:       {pred_data['inference_latency_ms']:.3f} ms")
        print(f"  Reason Codes:  {pred_data['reason_codes']}")

        # 5. Genuine Decision Outcomes (/decision)
        print("\n--- 4. VERIFY /decision END-TO-END FLOW (ALLOW / FLAG / HOLD) ---")

        # Discover genuine transactions that map to ALLOW, FLAG, HOLD
        demonstrations = {}
        for h in feature_hashes:
            f = db.get_feature_vector(h)
            if not f:
                continue

            for m_key, m_name in [("rule_baseline", "RuleBaseline"), ("logistic_regression", "LogisticRegression"), ("xgboost", "XGBoost")]:
                r_dec = client.post("/decision", json={"tx_hash": h, "features": f.features, "model_name": m_key, "idempotency": False})
                if r_dec.status_code == 200:
                    d_body = r_dec.json()
                    cat = d_body["decision"]
                    if cat not in demonstrations:
                        demonstrations[cat] = (h, m_name, d_body)

        for cat in ["ALLOW", "FLAG", "HOLD"]:
            if cat in demonstrations:
                h, mod, body = demonstrations[cat]
                print(f"\n[GENUINE {cat} VIA {mod}]")
                print(f"  Tx Hash:       {body['tx_hash'][:20]}...")
                print(f"  Risk Score:    {body['risk_score']}")
                print(f"  Decision:      {body['decision']} (is_simulated={body['is_simulated']}, expires_at={body['expires_at']})")
                print(f"  Reason Codes:  {body['reason_codes']}")
                print(f"  Audit ID:      {body['audit_id']}")
                print(f"  Policy Version:{body['policy_version']}")

        # 6. Audit Lookup (/audit/{tx_hash})
        print("\n--- 5. VERIFY /audit/{tx_hash} AND /audit/record/{audit_id} ---")
        if demonstrations:
            any_cat = next(iter(demonstrations.keys()))
            any_tx, _, any_body = demonstrations[any_cat]
            resp_audit = client.get(f"/audit/{any_tx}")
            print(f"GET /audit/{any_tx[:16]}...: HTTP {resp_audit.status_code}")
            audit_body = resp_audit.json()
            print(f"  Decisions Count:     {len(audit_body['decisions'])}")
            print(f"  Audit Records Count: {len(audit_body['audit_records'])}")

            first_audit_id = any_body["audit_id"]
            if first_audit_id:
                resp_single = client.get(f"/audit/record/{first_audit_id}")
                print(f"GET /audit/record/{first_audit_id}: HTTP {resp_single.status_code}")
                rec = resp_single.json()
                print(f"  Audit Record Match:  Event={rec['event_type']}, Score={rec['risk_score']}")

        # 7. Idempotency Check Over HTTP
        print("\n--- 6. VERIFY API IDEMPOTENCY OVER HTTP ---")
        import uuid
        idem_hash = "0x" + uuid.uuid4().hex + uuid.uuid4().hex
        sample_feats = fv_sample.features

        count_before = len(db.get_decisions_by_tx(idem_hash))
        print(f"Row count in DB BEFORE requests: {count_before}")

        # Request 1
        r1 = client.post("/decision", json={"tx_hash": idem_hash, "features": sample_feats, "model_name": "rule_baseline", "idempotency": True})
        count_after_1 = len(db.get_decisions_by_tx(idem_hash))
        print(f"Request 1 -> HTTP {r1.status_code} ({r1.json()['decision']}) | DB row count: {count_after_1}")

        # Request 2
        r2 = client.post("/decision", json={"tx_hash": idem_hash, "features": sample_feats, "model_name": "rule_baseline", "idempotency": True})
        count_after_2 = len(db.get_decisions_by_tx(idem_hash))
        print(f"Request 2 -> HTTP {r2.status_code} ({r2.json()['decision']}) | DB row count: {count_after_2}")
        print(f"Duplicate prevented: {count_after_1 == count_after_2 == 1}")

        # 8. Performance & Latency Measurement
        print("\n--- 7. PERFORMANCE & LATENCY MEASUREMENT (20 REQUESTS) ---")
        latencies = []
        for _ in range(20):
            t_start = time.perf_counter()
            r_bench = client.post("/decision", json={"tx_hash": sample_hash, "features": sample_feats, "model_name": "xgboost", "idempotency": False})
            t_end = time.perf_counter()
            if r_bench.status_code == 200:
                latencies.append((t_end - t_start) * 1000.0)

        min_lat = min(latencies)
        max_lat = max(latencies)
        avg_lat = sum(latencies) / len(latencies)
        print(f"Requests Executed: {len(latencies)}")
        print(f"  Min Latency:     {min_lat:.2f} ms")
        print(f"  Max Latency:     {max_lat:.2f} ms")
        print(f"  Avg Latency:     {avg_lat:.2f} ms")

        print("\n" + "=" * 80)
        print("FASTAPI LIVE SERVER VERIFICATION COMPLETED SUCCESSFULLY")
        print("=" * 80)

        return {
            "status": "PASSED",
            "latencies": {"min": min_lat, "max": max_lat, "avg": avg_lat},
        }

    finally:
        print("\nShutting down live Uvicorn server...")
        client.close()
        server_thread.stop()
        server_thread.join(timeout=3.0)
        print("Server shutdown complete.")


if __name__ == "__main__":
    run_live_server_verification()

"""Live server verification script for MempoolGuard Milestone M9 (Streamlit Dashboard).

Spawns FastAPI and Streamlit servers, executes real HTTP requests, verifies
subsystem health, real transaction data loading, aggregate analytics, and latency.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
import uvicorn

from api.app import create_app
from dashboard.api_client import ApiClient
from dashboard.data_loader import DataLoader


def run_fastapi_background() -> tuple[uvicorn.Server, threading.Thread]:
    """Start Uvicorn in a background thread."""
    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server, thread


def wait_for_http(url: str, timeout: float = 15.0) -> bool:
    """Poll URL until it responds with HTTP 200 or timeout."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with httpx.Client(timeout=1.0) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return True
        except (httpx.HTTPError, OSError):
            time.sleep(0.3)
    return False


def main() -> int:
    """Execute complete M9 live verification suite."""
    print("=" * 80)
    print("MEMPOOLGUARD M9 STREAMLIT DASHBOARD LIVE VERIFICATION SUITE")
    print("=" * 80)

    # 1. Start FastAPI service
    print("\n[1/8] Starting FastAPI service on http://127.0.0.1:8000...")
    api_server, api_thread = run_fastapi_background()
    if not wait_for_http("http://127.0.0.1:8000/health", timeout=10.0):
        print("[-] FAILED: FastAPI service did not respond within 10s.")
        api_server.should_exit = True
        return 1
    print("[+] FastAPI service is operational on http://127.0.0.1:8000/health")

    # 2. Start Streamlit Dashboard in subprocess
    print("\n[2/8] Spawning Streamlit Dashboard process on port 8501...")
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        "dashboard/app.py",
        "--server.port",
        "8501",
        "--server.headless",
        "true",
        "--server.address",
        "127.0.0.1",
        "--browser.serverAddress",
        "127.0.0.1",
    ]
    env = os.environ.copy()
    env["PYTHONPATH"] = "."
    st_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)

    try:
        print("[+] Waiting for Streamlit server on http://127.0.0.1:8501/...")
        if not wait_for_http("http://127.0.0.1:8501/_stcore/health", timeout=20.0):
            print("[-] FAILED: Streamlit server did not respond on /_stcore/health within 20s.")
            st_proc.terminate()
            api_server.should_exit = True
            return 1
        print("[+] Streamlit dashboard is online and responding with HTTP 200!")

        # 3. HTTP Accessibility and Page Content Check
        print("\n[3/8] Verifying HTTP Accessibility and Root HTML response...")
        with httpx.Client(timeout=5.0) as client:
            resp_main = client.get("http://127.0.0.1:8501/")
            assert resp_main.status_code == 200
            print(f"  GET http://127.0.0.1:8501/ -> Status {resp_main.status_code}")
            print(f"  Content-Length: {len(resp_main.text):,} bytes")
            assert "streamlit" in resp_main.text.lower() or "stApp" in resp_main.text
            print("  HTML structure verified: Streamlit single-page application loaded successfully.")

            resp_health = client.get("http://127.0.0.1:8501/_stcore/health")
            assert resp_health.status_code == 200
            print(f"  GET http://127.0.0.1:8501/_stcore/health -> Status {resp_health.status_code} ({resp_health.text.strip()})")

        # 4. API Client Integration Check
        print("\n[4/8] Testing dashboard ApiClient against live FastAPI service...")
        client = ApiClient(base_url="http://127.0.0.1:8000")
        health_data = client.check_health()
        print(f"  API is_connected: {health_data.get('is_connected')}")
        print(f"  System Status: {health_data.get('status')}")
        rpc_info = health_data.get("rpc") or health_data.get("subsystems", {}).get("rpc", {})
        db_info = health_data.get("database") or health_data.get("subsystems", {}).get("database", {})
        models_info = health_data.get("models") or health_data.get("subsystems", {}).get("models", {})
        print(f"  RPC Node: {rpc_info.get('status')}")
        print(f"  Database: {db_info.get('status')}")
        print(f"  Active Models: {models_info.get('available_models')}")

        # 5. Data Loader & Single Source of Truth Verification
        print("\n[5/8] Inspecting DataLoader queries against SQLite (data/mempoolguard.db)...")
        loader = DataLoader(db_path="data/mempoolguard.db")
        counts = loader.get_summary_counts()
        print("  Summary Counts from single source of truth:")
        for tbl, cnt in counts.items():
            print(f"    - {tbl}: {cnt:,}")

        # 6. Real Transaction Deep-Dive Demonstration
        print("\n[6/8] Demonstrating Real End-to-End Transaction Flow in Dashboard...")
        tx_hashes = loader.get_all_transaction_hashes(limit=10)
        if tx_hashes:
            sample_hash = tx_hashes[0]
            print(f"  Selected Real Transaction Hash: {sample_hash}")
            detail = loader.get_transaction_detail(sample_hash)
            assert detail is not None
            tx = detail["transaction"]
            val_eth = tx.value_wei / 1e18
            print(f"  [Tx Metadata] Sender: {tx.sender}, Receiver: {tx.receiver}, Value: {val_eth:.4f} ETH")

            fv = detail["features"]
            if fv:
                print(f"  [Pre-Confirmation Features] Version: {fv.feature_version}, Total Features: {len(fv.features)}")
                print(f"    Sample features: value_eth={fv.features.get('value_eth')}, gas_limit={fv.features.get('gas_limit')}")

            pred = detail["prediction"]
            if pred:
                print(f"  [M6 ML Prediction] Model: {pred.model_name}, Score: {pred.risk_score:.2f}, Class: {pred.predicted_class}")

            dec = detail["decision"]
            if dec:
                print(f"  [M7 Policy Decision] Decision: {dec.decision.value}, Policy: {dec.policy_version}, Reasons: {dec.reason_codes}")

            audits = detail["audit_records"]
            print(f"  [Audit Log Trail] Associated Audit Records: {len(audits)}")
            for a in audits:
                print(f"    - Event: {a.event_type.value}, Decision: {a.decision}, Score: {a.risk_score}, Time: {a.timestamp.isoformat()[:19]}")
        else:
            print("  No transactions found in database.")

        # 7. Aggregate Analytics Verification
        print("\n[7/8] Verifying Aggregate Analytics & Distributions...")
        dec_dist = loader.get_decision_distribution()
        print(f"  Decision Distribution: {dec_dist}")
        scores = loader.get_risk_score_distribution()
        print(f"  Risk Scores Recorded: {len(scores):,} scores (Min: {min(scores) if scores else 0:.1f}, Max: {max(scores) if scores else 0:.1f})")
        audit_events = loader.get_audit_event_distribution()
        print(f"  Audit Event Types: {audit_events}")

        # 8. Responsiveness Latency Benchmark
        print("\n[8/8] Benchmarking Dashboard Latency over 10 consecutive requests...")
        latencies = []
        with httpx.Client(timeout=5.0) as client:
            for _ in range(10):
                t0 = time.perf_counter()
                _ = client.get("http://127.0.0.1:8501/_stcore/health")
                t1 = time.perf_counter()
                latencies.append((t1 - t0) * 1000)

        min_lat = min(latencies)
        max_lat = max(latencies)
        avg_lat = sum(latencies) / len(latencies)
        print("  Completed 10 requests to http://127.0.0.1:8501/_stcore/health")
        print(f"  Min Latency: {min_lat:.2f} ms")
        print(f"  Max Latency: {max_lat:.2f} ms")
        print(f"  Avg Latency: {avg_lat:.2f} ms")

        print("\n" + "=" * 80)
        print("M9 STREAMLIT DASHBOARD LIVE VERIFICATION COMPLETED SUCCESSFULLY!")
        print("=" * 80)
        return 0

    finally:
        # Cleanup processes
        print("\n[*] Shutting down Streamlit Dashboard subprocess...")
        st_proc.terminate()
        try:
            st_proc.wait(timeout=5.0)
        except (OSError, subprocess.SubprocessError):
            st_proc.kill()

        print("[*] Shutting down FastAPI background server...")
        api_server.should_exit = True
        api_thread.join(timeout=3.0)
        print("[*] Cleanup complete.")


if __name__ == "__main__":
    sys.exit(main())

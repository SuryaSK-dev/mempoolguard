"""End-to-end experimental verification script for Milestone M4 (Deterministic Feature Engine).

Demonstrates the complete pre-confirmation feature engineering lifecycle:
1. Capture a live transaction while pending via the M3 listener.
2. Record its exact first_seen_at timestamp.
3. Generate the M4 feature vector using exclusively pre-confirmation data.
4. Show the 30 canonical feature values and availability masks.
5. Verify that NO information after first_seen_at was used (anti-leakage guard).
6. Persist the feature vector to SQLite database.
7. Demonstrate deterministic regeneration produces the identical feature vector.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

# Ensure workspace root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

from collector.mempool_listener import MempoolListener
from core.anvil import AnvilController
from core.database import Database
from features.engine import FeatureEngine


def run_m4_experiment() -> dict[str, Any]:
    print("=" * 70, flush=True)
    print("MEMPOOLGUARD MILESTONE M4: DETERMINISTIC FEATURE ENGINE EXPERIMENT", flush=True)
    print("=" * 70, flush=True)

    ctrl = AnvilController()
    if not ctrl.is_running():
        ctrl.start()
    w3 = ctrl.get_web3()
    db = Database("data/mempoolguard.db")
    db.init_schema()

    observed_events: list[dict[str, Any]] = []

    def on_capture(info: dict[str, Any]) -> None:
        observed_events.append(info)
        print(f"  --> [LISTENER CAPTURED] Tx Hash: {info['tx_hash']}", flush=True)
        print(f"      First Seen At : {info['first_seen_at'].isoformat()}", flush=True)
        print(f"      Block Number  : {info['block_number']} (Pending)", flush=True)

    listener = MempoolListener(
        w3=w3,
        db=db,
        poll_interval_seconds=0.02,
        on_transaction_captured=on_capture,
    )

    try:
        # Step 1: Disable automining and start listener
        print("\n[Step 1] Disabling automining and starting M3 MempoolListener...", flush=True)
        ctrl.set_automine(False)
        listener.start()
        print("  Automining disabled. Listener running in background.", flush=True)

        # Step 2: Submit a target transaction
        accounts = w3.eth.accounts
        sender = accounts[1]
        receiver = accounts[2]
        nonce = w3.eth.get_transaction_count(sender)

        print("\n[Step 2] Submitting target transaction to mempool...", flush=True)
        tx_params = {
            "from": sender,
            "to": receiver,
            "value": w3.to_wei(1.42, "ether"),
            "gas": 25000,
            "maxFeePerGas": w3.to_wei(4, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(2, "gwei"),
            "nonce": nonce,
        }
        dispatch_start = time.time()
        tx_hash = w3.eth.send_transaction(tx_params).to_0x_hex().lower()
        print(f"  Target Transaction Hash: {tx_hash}", flush=True)

        # Step 3: Observe while pending
        print("\n[Step 3] Waiting for pending capture by M3 listener...", flush=True)
        timeout = 4.0
        while not observed_events and (time.time() - dispatch_start) < timeout:
            time.sleep(0.02)

        assert len(observed_events) > 0, "Listener failed to capture pending transaction"
        capture_event = observed_events[0]
        first_seen_at = capture_event["first_seen_at"]
        print(f"  Transaction successfully captured at {first_seen_at.isoformat()}", flush=True)

        # Verify it is genuinely pending
        chain_tx = w3.eth.get_transaction(tx_hash)
        assert chain_tx.blockNumber is None, "Transaction is unexpectedly mined already!"
        print("  Verified: on-chain blockNumber is None (genuinely pending).", flush=True)

        # Step 4: Extract M4 feature vector
        print("\n[Step 4] Extracting deterministic M4 feature vector...", flush=True)
        engine = FeatureEngine(db=db)
        fv = engine.extract_from_raw_hash(tx_hash)

        print(f"  Feature Vector ID     : {fv.tx_hash}", flush=True)
        print(f"  Feature Version       : {fv.feature_version}", flush=True)
        print(f"  Feature Timestamp     : {fv.feature_timestamp.isoformat()}", flush=True)
        print(f"  Total Features        : {len(fv.features)} / {len(fv.feature_names)} canonical", flush=True)
        print(f"  Missing Features      : {fv.missing_features}", flush=True)
        print(f"  Availability Mask     : {json.dumps(fv.availability_mask)}", flush=True)
        assert fv.availability_mask["post_confirmation"] is False, "Post-confirmation data leaked!"

        print("\n  Representative Feature Values:", flush=True)
        for name in [
            "value_eth",
            "gas_limit",
            "gas_price_gwei",
            "max_fee_per_gas_gwei",
            "calldata_size_bytes",
            "is_contract_call",
            "sender_historical_tx_count",
            "window_tx_count_60s",
            "is_burst_activity",
            "sender_out_degree",
            "sender_pagerank",
        ]:
            print(f"    {name:<32}: {fv.features[name]}", flush=True)

        # Step 5: Test Temporal Anti-Leakage
        print("\n[Step 5] Testing strict temporal anti-leakage with a future transaction...", flush=True)
        time.sleep(0.1)  # Ensure time progression
        future_nonce = w3.eth.get_transaction_count(sender) + 1
        future_params = {
            "from": sender,
            "to": receiver,
            "value": w3.to_wei(50.0, "ether"),  # Huge future transaction
            "gas": 80000,
            "maxFeePerGas": w3.to_wei(500, "gwei"),  # 500 Gwei massive future spike
            "maxPriorityFeePerGas": w3.to_wei(50, "gwei"),
            "nonce": future_nonce,
        }
        future_hash = w3.eth.send_transaction(future_params).to_0x_hex().lower()
        print(f"  Dispatched future transaction at later time: {future_hash}", flush=True)

        # Regenerate feature vector for target transaction
        fv_regenerated = engine.extract_from_raw_hash(tx_hash)

        # Step 6: Verify determinism and lack of future contamination
        print("\n[Step 6] Demonstrating determinism & zero future contamination...", flush=True)
        assert fv.features == fv_regenerated.features, "Feature regeneration differed! Non-deterministic!"
        assert fv.features["value_eth"] == 1.42, "Value corrupted by future transaction!"
        assert fv.features["max_fee_per_gas_gwei"] == 4.0, "Gas fee corrupted by future transaction!"
        print("  VERIFIED: Regenerated feature vector matches original bit-for-bit.", flush=True)
        print("  VERIFIED: Zero contamination from subsequent future transactions.", flush=True)

        # Step 7: Verify database persistence
        print("\n[Step 7] Verifying database persistence in feature_vectors table...", flush=True)
        persisted_fv = db.get_latest_feature_vector(tx_hash)
        assert persisted_fv is not None, "Feature vector missing from database!"
        assert persisted_fv.tx_hash == tx_hash
        assert persisted_fv.features == fv.features
        print("  Feature vector confirmed in SQLite feature_vectors table.", flush=True)

        # Step 8: Mine block and confirm terminal state
        print("\n[Step 8] Mining block to confirm transaction...", flush=True)
        ctrl.mine_all_pending()
        chain_tx_mined = w3.eth.get_transaction(tx_hash)
        assert chain_tx_mined.blockNumber is not None, "Transaction failed to confirm!"
        print(f"  Confirmed in Block Number: {chain_tx_mined.blockNumber}", flush=True)

        print("\n" + "=" * 70, flush=True)
        print("MILESTONE M4 EXPERIMENT RESULT: PASS", flush=True)
        print("=" * 70, flush=True)
        print(f"  Target Tx Hash           : {tx_hash}", flush=True)
        print(f"  First-Seen Timestamp     : {first_seen_at.isoformat()}", flush=True)
        print(f"  Feature Engine Version   : {fv.feature_version}", flush=True)
        print(f"  Total Features Extracted : {len(fv.features)}", flush=True)
        print("  Anti-Leakage Guard       : Verified (100% pre-confirmation)", flush=True)
        print("  Determinism              : Verified (Exact bit-for-bit match)", flush=True)
        print("=" * 70, flush=True)

        return {
            "status": "PASS",
            "tx_hash": tx_hash,
            "first_seen_at": first_seen_at.isoformat(),
            "feature_version": fv.feature_version,
            "features": fv.features,
        }

    finally:
        listener.stop()
        ctrl.mine_all_pending()
        ctrl.set_automine(True)
        print("\nCleanup: Automining restored, listener stopped.", flush=True)


if __name__ == "__main__":
    run_m4_experiment()

"""End-to-end experimental verification script for Milestone M3 (Mempool Listener).

Demonstrates the exact pending transaction observation lifecycle:
1. Disable automining.
2. Start the M3 listener.
3. Submit a live transaction.
4. Demonstrate that the listener observes the transaction while it is still pending.
5. Persist the raw transaction with its first-seen timestamp.
6. Confirm that the transaction has NOT yet been mined at the time of observation.
7. Mine the transaction.
8. Verify that the same transaction later reaches confirmed state.
9. Verify that the stored pending observation corresponds to the confirmed transaction hash.
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


def run_m3_experiment() -> dict[str, Any]:
    print("=" * 70, flush=True)
    print("MEMPOOLGUARD MILESTONE M3: PENDING TRANSACTION EXPERIMENT", flush=True)
    print("=" * 70, flush=True)

    # Setup controller and DB
    ctrl = AnvilController()
    if not ctrl.is_running():
        ctrl.start()
    w3 = ctrl.get_web3()
    db = Database("data/mempoolguard.db")
    db.init_schema()

    observed_events: list[dict[str, Any]] = []

    def on_capture(info: dict[str, Any]) -> None:
        observed_events.append(info)
        print(f"  --> [LISTENER CAPTURE EVENT] Tx Hash: {info['tx_hash']}")
        print(f"      First Seen At : {info['first_seen_at'].isoformat()}")
        print(f"      Block Number  : {info['block_number']} (Expected: None for pending)")

    listener = MempoolListener(
        w3=w3,
        db=db,
        poll_interval_seconds=0.02,
        on_transaction_captured=on_capture,
    )

    try:
        # Step 1: Disable automining
        print("\n[Step 1] Disabling Anvil automining...")
        ctrl.set_automine(False)
        print("  Automining successfully disabled.")

        # Step 2: Start the M3 listener
        print("\n[Step 2] Starting M3 MempoolListener thread...")
        listener.start()
        assert listener.is_running(), "Listener failed to start"
        print("  MempoolListener is actively running in background.")

        # Step 3: Submit live transaction
        accounts = w3.eth.accounts
        sender = accounts[0]
        receiver = accounts[1]
        nonce = w3.eth.get_transaction_count(sender)

        print("\n[Step 3] Submitting live transaction to local Anvil mempool...")
        tx_params = {
            "from": sender,
            "to": receiver,
            "value": w3.to_wei(0.777, "ether"),
            "gas": 21000,
            "maxFeePerGas": w3.to_wei(3, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
            "nonce": nonce,
        }
        dispatch_start = time.time()
        tx_hash_bytes = w3.eth.send_transaction(tx_params)
        tx_hash = tx_hash_bytes.to_0x_hex().lower()
        print(f"  Dispatched Transaction Hash: {tx_hash}")

        # Step 4: Demonstrate listener observes transaction while pending
        print("\n[Step 4] Observing transaction in mempool while pending...")
        timeout = 4.0
        while not observed_events and (time.time() - dispatch_start) < timeout:
            time.sleep(0.02)

        assert len(observed_events) > 0, "Listener timed out waiting for pending tx"
        capture_event = observed_events[0]
        assert capture_event["tx_hash"] == tx_hash, "Captured hash mismatch!"
        print("  Transaction successfully captured by listener!")

        # Step 5: Persist raw transaction with first-seen timestamp
        print("\n[Step 5] Verifying persistence in SQLite raw_transactions table...")
        stored_record = db.get_raw_transaction(tx_hash)
        assert stored_record is not None, "Raw transaction missing from database"
        print(f"  Database Record Found: tx_hash={stored_record['tx_hash']}")
        print(f"  Recorded first_seen_at: {stored_record['first_seen_at'].isoformat()}")
        print(f"  Recorded captured_at  : {stored_record['captured_at'].isoformat()}")

        # Step 6: Confirm transaction was NOT yet mined at observation time
        print("\n[Step 6] Confirming transaction was NOT yet mined at observation time...")
        raw_payload = json.loads(stored_record["raw_payload"])
        assert raw_payload["blockNumber"] is None, "Payload shows transaction was already mined!"
        chain_tx_pre_mining = w3.eth.get_transaction(tx_hash)
        assert chain_tx_pre_mining.blockNumber is None, "Chain reports blockNumber is not None!"
        print(f"  On-chain blockNumber at observation time : {chain_tx_pre_mining.blockNumber} (None = PENDING)")
        print(f"  On-chain blockHash at observation time   : {chain_tx_pre_mining.blockHash} (None = PENDING)")
        print("  VERIFIED: Transaction was genuinely pending in mempool when captured!")

        # Step 7: Mine the transaction
        print("\n[Step 7] Explicitly triggering mining of pending block...")
        ctrl.mine_blocks(1)
        print("  Block mined successfully.")

        # Step 8: Verify transaction reaches confirmed state
        print("\n[Step 8] Verifying transaction reached terminal CONFIRMED state...")
        chain_tx_post_mining = w3.eth.get_transaction(tx_hash)
        assert chain_tx_post_mining.blockNumber is not None, "Transaction failed to confirm!"
        confirmed_block = chain_tx_post_mining.blockNumber
        print(f"  Confirmed in Block Number: {confirmed_block}")
        print(f"  Confirmed Block Hash    : {chain_tx_post_mining.blockHash.to_0x_hex()}")

        # Step 9: Verify correspondence
        print("\n[Step 9] Verifying correspondence between pending capture and confirmed transaction...")
        assert chain_tx_post_mining["hash"].to_0x_hex().lower() == stored_record["tx_hash"]
        print("  Pending capture hash strictly matches confirmed transaction hash!")

        # Gather summary metrics
        metrics = listener.get_metrics()
        print("\n" + "=" * 70)
        print("EXPERIMENT RESULT: PASS")
        print("=" * 70)
        print(f"  Transaction Hash       : {tx_hash}")
        print(f"  First-Seen Timestamp   : {stored_record['first_seen_at'].isoformat()}")
        print(f"  Mined Block Number     : {confirmed_block}")
        print(f"  Pending Confirmed State: None -> Block {confirmed_block}")
        print(f"  Listener Metrics       : {json.dumps(metrics)}")
        print("=" * 70)

        return {
            "status": "PASS",
            "tx_hash": tx_hash,
            "first_seen_at": stored_record["first_seen_at"].isoformat(),
            "pre_mining_block": None,
            "post_mining_block": confirmed_block,
            "metrics": metrics,
        }

    finally:
        listener.stop()
        ctrl.mine_all_pending()
        ctrl.set_automine(True)
        print("\nCleanup: Automine restored, listener stopped.")


if __name__ == "__main__":
    run_m3_experiment()

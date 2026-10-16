"""Tests for Milestone M3: Pending Transaction Listener.

Verifies pending transaction capture, raw persistence, deduplication,
missing transaction handling, callback notifications, and full end-to-end
mempool lifecycle (observation before confirmation).
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import MagicMock

from web3 import Web3
from web3.exceptions import TransactionNotFound

from collector.mempool_listener import MempoolListener, serialize_raw_transaction
from core.anvil import AnvilController
from core.contracts import utc_now
from core.database import Database


def test_listener_lifecycle(w3: Web3, in_memory_db: Database) -> None:
    """Verify listener starting, stopping, and running state transitions."""
    listener = MempoolListener(w3=w3, db=in_memory_db, poll_interval_seconds=0.05)
    assert listener.is_running() is False

    listener.start()
    assert listener.is_running() is True

    # Starting already running listener is idempotent
    listener.start()
    assert listener.is_running() is True

    listener.stop()
    assert listener.is_running() is False

    # Stopping already stopped listener is safe
    listener.stop()
    assert listener.is_running() is False


def test_serialize_raw_transaction() -> None:
    """Verify serialization handles hex bytes, timestamps, and primitives safely."""
    sample_tx = {
        "hash": "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        "nonce": 4,
        "value": 1000000000000000000,
        "blockNumber": None,
        "blockHash": None,
        "timestamp": utc_now(),
    }
    serialized = serialize_raw_transaction(sample_tx)
    assert isinstance(serialized, str)
    deserialized = json.loads(serialized)
    assert deserialized["nonce"] == 4
    assert deserialized["blockNumber"] is None
    assert deserialized["value"] == 1000000000000000000


def test_deduplication_handling(w3: Web3, in_memory_db: Database) -> None:
    """Verify in-memory and database-level deduplication prevents redundant writes."""
    listener = MempoolListener(w3=w3, db=in_memory_db, max_seen_cache=100)

    tx_hash = "0x" + "a" * 64
    # First time mark_seen
    assert listener._mark_seen(tx_hash) is True
    # Second time mark_seen should detect duplicate
    assert listener._mark_seen(tx_hash) is False
    assert listener.duplicate_count == 1

    # Database level deduplication (insert_raw_transaction idempotency)
    first_seen = utc_now()
    inserted_first = in_memory_db.insert_raw_transaction(tx_hash, "{}", first_seen)
    assert inserted_first is True

    inserted_second = in_memory_db.insert_raw_transaction(tx_hash, "{}", first_seen)
    assert inserted_second is False


def test_missing_and_malformed_transactions(in_memory_db: Database) -> None:
    """Verify listener handles TransactionNotFound without crashing."""
    # Mock Web3 get_transaction to raise TransactionNotFound
    mock_w3 = MagicMock()
    mock_w3.eth.filter.return_value.get_new_entries.return_value = ["0x" + "b" * 64]
    mock_w3.eth.get_transaction.side_effect = TransactionNotFound("Tx dropped from pool")

    listener_with_mock = MempoolListener(w3=mock_w3, db=in_memory_db)
    captured = listener_with_mock.poll_once()

    assert captured == []
    assert listener_with_mock.dropped_count == 1


def test_on_transaction_captured_callback(
    anvil_controller: AnvilController, w3: Web3, in_memory_db: Database
) -> None:
    """Verify that the registered callback receives captured pending transaction metadata."""
    anvil_controller.set_automine(False)
    captured_payloads: list[dict[str, Any]] = []

    def on_captured(tx_info: dict[str, Any]) -> None:
        captured_payloads.append(tx_info)

    listener = MempoolListener(
        w3=w3,
        db=in_memory_db,
        poll_interval_seconds=0.05,
        on_transaction_captured=on_captured,
    )

    try:
        accounts = w3.eth.accounts
        tx_hash = w3.eth.send_transaction(
            {
                "from": accounts[0],
                "to": accounts[1],
                "value": w3.to_wei(0.01, "ether"),
                "gas": 21000,
                "maxFeePerGas": w3.to_wei(2, "gwei"),
                "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
                "nonce": w3.eth.get_transaction_count(accounts[0]),
            }
        ).to_0x_hex()

        # Poll to capture
        newly_captured = listener.poll_once()
        assert tx_hash.lower() in newly_captured

        assert len(captured_payloads) == 1
        record = captured_payloads[0]
        assert record["tx_hash"] == tx_hash.lower()
        assert record["block_number"] is None  # Must be pending!
    finally:
        anvil_controller.mine_all_pending()
        anvil_controller.set_automine(True)
        listener.stop()


def test_end_to_end_pending_transaction_experiment(
    anvil_controller: AnvilController, w3: Web3, in_memory_db: Database
) -> None:
    """Demonstrate end-to-end pending transaction observation and confirmation lifecycle.

    Execution Sequence:
    1. Disable automining.
    2. Start the M3 listener in background thread.
    3. Submit a live transaction.
    4. Verify listener observes transaction while STILL pending (blockNumber is None).
    5. Verify raw transaction persisted in database with valid first_seen_at.
    6. Verify on-chain status confirms blockNumber is None at observation time.
    7. Mine the block explicitly.
    8. Verify transaction reaches confirmed state (blockNumber is integer >= 1).
    9. Verify stored pending record corresponds to confirmed transaction hash.
    """
    # 1. Disable automining
    anvil_controller.set_automine(False)
    accounts = w3.eth.accounts
    sender = accounts[2]
    receiver = accounts[3]

    observed_hashes: list[str] = []

    def capture_hook(info: dict[str, Any]) -> None:
        observed_hashes.append(info["tx_hash"])

    # 2. Start listener
    listener = MempoolListener(
        w3=w3,
        db=in_memory_db,
        poll_interval_seconds=0.02,
        on_transaction_captured=capture_hook,
    )
    listener.start()

    try:
        # 3. Submit live transaction to mempool
        tx_params = {
            "from": sender,
            "to": receiver,
            "value": w3.to_wei(0.25, "ether"),
            "gas": 21000,
            "maxFeePerGas": w3.to_wei(3, "gwei"),
            "maxPriorityFeePerGas": w3.to_wei(1, "gwei"),
            "nonce": w3.eth.get_transaction_count(sender),
        }
        tx_hash_bytes = w3.eth.send_transaction(tx_params)
        tx_hash = tx_hash_bytes.to_0x_hex().lower()

        # 4. Wait briefly for background listener to observe transaction
        timeout = 3.0
        start_time = time.time()
        while tx_hash not in observed_hashes and (time.time() - start_time) < timeout:
            time.sleep(0.05)

        assert tx_hash in observed_hashes, f"Listener failed to observe pending tx {tx_hash}"

        # 5. Retrieve persisted raw record from database
        stored_raw = in_memory_db.get_raw_transaction(tx_hash)
        assert stored_raw is not None, "Raw transaction was not persisted in database"
        assert stored_raw["tx_hash"] == tx_hash
        assert stored_raw["first_seen_at"] is not None

        # 6. Verify transaction was NOT yet mined at time of observation
        raw_payload = json.loads(stored_raw["raw_payload"])
        assert raw_payload["blockNumber"] is None, "Observed tx was already mined in payload!"
        chain_tx_pending = w3.eth.get_transaction(tx_hash)
        assert chain_tx_pending.blockNumber is None, "Chain transaction is already mined!"

        # 7. Mine the transaction
        anvil_controller.mine_all_pending()

        # 8. Verify transaction reaches confirmed state
        chain_tx_confirmed = w3.eth.get_transaction(tx_hash)
        assert chain_tx_confirmed.blockNumber is not None, "Transaction failed to confirm!"
        assert chain_tx_confirmed.blockNumber >= 1

        # 9. Verify correspondence between pending capture and confirmed transaction
        assert chain_tx_confirmed["hash"].to_0x_hex().lower() == stored_raw["tx_hash"]

    finally:
        # Cleanup
        listener.stop()
        anvil_controller.mine_all_pending()
        anvil_controller.set_automine(True)

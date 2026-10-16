"""Dedicated unit and integration tests for Milestone M4: Deterministic Feature Engine.

Verifies:
1. Transaction-level feature extraction (value, gas, calldata, selector, types)
2. Historical address features (prior count, avg value, relationship)
3. Sliding window and burst activity indicators
4. Fan-out and repeated transfer detection
5. Graph-derived features using NetworkX (in/out degree, PageRank)
6. Missing/unavailable data and contract-creation handling
7. Determinism and reproducibility (identical bit-for-bit results)
8. Feature persistence in SQLite
9. Strict temporal anti-leakage prevention (zero future transaction contamination)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.contracts import Transaction, TransactionStatus
from core.database import Database
from features.engine import FEATURE_NAMES, FeatureEngine


def make_test_tx(
    tx_hash: str,
    sender: str,
    receiver: str | None,
    value_wei: int,
    first_seen_at: datetime,
    gas_limit: int = 21000,
    gas_price_wei: int | None = 20_000_000_000,
    max_fee_per_gas_wei: int | None = 30_000_000_000,
    max_priority_fee_per_gas_wei: int | None = 2_000_000_000,
    nonce: int = 0,
    input_data: str = "0x",
) -> Transaction:
    """Helper to construct a validated pre-confirmation Transaction."""
    return Transaction(
        tx_hash=tx_hash.lower(),
        chain_id=31337,
        sender=sender.lower(),
        receiver=receiver.lower() if receiver else None,
        value_wei=value_wei,
        nonce=nonce,
        gas_limit=gas_limit,
        gas_price_wei=gas_price_wei,
        max_fee_per_gas_wei=max_fee_per_gas_wei,
        max_priority_fee_per_gas_wei=max_priority_fee_per_gas_wei,
        input_data=input_data,
        first_seen_at=first_seen_at.astimezone(timezone.utc),
        confirmed_at=None,
        block_number=None,
        transaction_status=TransactionStatus.PENDING,
    )


def test_feature_names_and_ordering(in_memory_db: Database) -> None:
    """Verify all 30 feature names are present, ordered, and versioned."""
    engine = FeatureEngine(in_memory_db)
    assert len(FEATURE_NAMES) == 30
    assert engine.feature_version == "1.0.0"

    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    tx = make_test_tx("0x" + "1" * 64, "0x" + "a" * 40, "0x" + "b" * 40, 10**18, t0)
    fv = engine.extract_features(tx)

    assert fv.feature_names == list(FEATURE_NAMES)
    assert len(fv.features) == 30
    assert list(fv.features.keys()) == list(FEATURE_NAMES)


def test_transaction_level_feature_extraction(in_memory_db: Database) -> None:
    """Verify transaction-level attributes, gas fees, calldata, and types."""
    engine = FeatureEngine(in_memory_db)
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Standard EIP-1559 transfer
    tx = make_test_tx(
        tx_hash="0x" + "1" * 64,
        sender="0x" + "a" * 40,
        receiver="0x" + "b" * 40,
        value_wei=2_500_000_000_000_000_000,  # 2.5 ETH
        first_seen_at=t0,
        gas_limit=50000,
        gas_price_wei=None,
        max_fee_per_gas_wei=40_000_000_000,
        max_priority_fee_per_gas_wei=3_000_000_000,
        nonce=7,
        input_data="0xa9059cbb0000000000000000000000001111111111111111111111111111111111111111",
    )
    fv = engine.extract_features(tx)
    f = fv.features

    assert f["value_eth"] == 2.5
    assert f["gas_limit"] == 50000
    assert f["gas_price_gwei"] == 40.0
    assert f["max_fee_per_gas_gwei"] == 40.0
    assert f["max_priority_fee_per_gas_gwei"] == 3.0
    assert f["calldata_size_bytes"] == 36
    assert f["is_contract_call"] == 1
    assert f["is_contract_creation"] == 0
    assert f["function_selector_hash"] > 0.0
    assert f["nonce"] == 7
    assert f["tx_type"] == 2


def test_historical_address_features(in_memory_db: Database) -> None:
    """Verify historical counts and averages prior to first_seen_at."""
    engine = FeatureEngine(in_memory_db)
    sender = "0x" + "a" * 40
    receiver = "0x" + "b" * 40
    base_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Insert 2 prior transactions from sender to receiver
    tx_past1 = make_test_tx("0x" + "1" * 64, sender, receiver, 10**18, base_time - timedelta(seconds=120))
    tx_past2 = make_test_tx("0x" + "2" * 64, sender, receiver, 3 * 10**18, base_time - timedelta(seconds=80))
    in_memory_db.insert_decoded_transaction(tx_past1)
    in_memory_db.insert_decoded_transaction(tx_past2)

    # Target transaction at base_time
    tx_target = make_test_tx("0x" + "3" * 64, sender, receiver, 2 * 10**18, base_time)
    fv = engine.extract_features(tx_target)
    f = fv.features

    assert f["sender_historical_tx_count"] == 2
    assert f["receiver_historical_tx_count"] == 2
    assert f["sender_receiver_tx_count"] == 2
    assert f["sender_receiver_existing_relationship"] == 1
    assert f["sender_historical_avg_value_eth"] == 2.0  # (1 + 3) / 2


def test_sliding_window_burst_and_repeated_transfers(in_memory_db: Database) -> None:
    """Verify burst activity and repeated transfer detection within sliding window."""
    engine = FeatureEngine(in_memory_db, window_seconds=60.0, burst_window_seconds=10.0)
    sender = "0x" + "a" * 40
    receiver = "0x" + "b" * 40
    target_time = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)

    # Insert 3 rapid transactions within the last 5 seconds (burst window)
    for i in range(1, 4):
        past_tx = make_test_tx(
            f"0x{i:02x}" + "0" * 62,
            sender,
            receiver,
            10**17,  # 0.1 ETH
            target_time - timedelta(seconds=i),
        )
        in_memory_db.insert_decoded_transaction(past_tx)

    # Target transaction has identical value (repeated transfer)
    target_tx = make_test_tx("0x" + "9" * 64, sender, receiver, 10**17, target_time)
    fv = engine.extract_features(target_tx)
    f = fv.features

    assert f["window_tx_count_60s"] == 3
    assert f["window_tx_count_10s"] == 3
    assert f["is_burst_activity"] == 1
    assert f["is_repeated_transfer"] == 1


def test_fan_out_detection(in_memory_db: Database) -> None:
    """Verify fan-out detection when sender targets >= 3 distinct receivers."""
    engine = FeatureEngine(in_memory_db, window_seconds=60.0)
    sender = "0x" + "a" * 40
    target_time = datetime(2026, 1, 1, 12, 0, 30, tzinfo=timezone.utc)

    # Disperse transfers to 3 different receivers in window
    for i in range(1, 4):
        rec = "0x" + f"{i}" * 40
        tx = make_test_tx(f"0x{i:02x}" + "5" * 62, sender, rec, 10**16, target_time - timedelta(seconds=10 * i))
        in_memory_db.insert_decoded_transaction(tx)

    # Target transaction to a 4th receiver
    target_tx = make_test_tx("0x" + "8" * 64, sender, "0x" + "4" * 40, 10**16, target_time)
    fv = engine.extract_features(target_tx)

    assert fv.features["window_unique_receivers_60s"] == 3
    assert fv.features["is_fan_out"] == 1


def test_graph_derived_features(in_memory_db: Database) -> None:
    """Verify directed transaction graph degrees and PageRank using NetworkX."""
    engine = FeatureEngine(in_memory_db)
    target_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    node_a = "0x" + "a" * 40
    node_b = "0x" + "b" * 40
    node_c = "0x" + "c" * 40

    # Build graph edges: A -> B and A -> C
    tx1 = make_test_tx("0x" + "1" * 64, node_a, node_b, 10**18, target_time - timedelta(seconds=20))
    tx2 = make_test_tx("0x" + "2" * 64, node_a, node_c, 10**18, target_time - timedelta(seconds=10))
    in_memory_db.insert_decoded_transaction(tx1)
    in_memory_db.insert_decoded_transaction(tx2)

    # Target transaction: A -> B
    target_tx = make_test_tx("0x" + "3" * 64, node_a, node_b, 10**18, target_time)
    fv = engine.extract_features(target_tx)
    f = fv.features

    assert f["sender_out_degree"] >= 2
    assert f["receiver_in_degree"] >= 1
    assert 0.0 <= f["sender_pagerank"] <= 1.0
    assert 0.0 <= f["receiver_pagerank"] <= 1.0


def test_missing_and_contract_creation_handling(in_memory_db: Database) -> None:
    """Verify receiver is None (contract creation) is handled with explicit missing features."""
    engine = FeatureEngine(in_memory_db)
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    # Contract creation transaction has receiver=None
    tx = make_test_tx(
        tx_hash="0x" + "7" * 64,
        sender="0x" + "a" * 40,
        receiver=None,
        value_wei=0,
        first_seen_at=t0,
        input_data="0x608060405234801561001057600080fd5b50",
    )
    fv = engine.extract_features(tx)
    f = fv.features

    assert f["is_contract_creation"] == 1
    assert f["is_contract_call"] == 0
    assert f["receiver_historical_tx_count"] == 0
    assert f["receiver_in_degree"] == 0
    assert f["receiver_pagerank"] == 0.0

    # Ensure missing features are cataloged
    assert "receiver_historical_tx_count" in fv.missing_features
    assert "receiver_in_degree" in fv.missing_features
    assert "receiver_pagerank" in fv.missing_features
    assert fv.availability_mask["post_confirmation"] is False


def test_feature_persistence_and_retrieval(in_memory_db: Database) -> None:
    """Verify feature vector persistence to SQLite and round-trip fidelity."""
    engine = FeatureEngine(in_memory_db)
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    tx = make_test_tx("0x" + "9" * 64, "0x" + "a" * 40, "0x" + "b" * 40, 10**18, t0)

    fv_saved = engine.extract_and_persist(tx)
    fv_retrieved = in_memory_db.get_latest_feature_vector(tx.tx_hash)

    assert fv_retrieved is not None
    assert fv_retrieved.tx_hash == fv_saved.tx_hash
    assert fv_retrieved.feature_version == fv_saved.feature_version
    assert fv_retrieved.feature_names == fv_saved.feature_names
    assert fv_retrieved.features == fv_saved.features
    assert fv_retrieved.availability_mask == fv_saved.availability_mask


def test_determinism_and_reproducibility(in_memory_db: Database) -> None:
    """Verify that multiple extractions produce identical bit-for-bit feature vectors."""
    engine = FeatureEngine(in_memory_db)
    t0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    tx = make_test_tx("0x" + "5" * 64, "0x" + "a" * 40, "0x" + "b" * 40, 5 * 10**17, t0)

    fv1 = engine.extract_features(tx)
    fv2 = engine.extract_features(tx)

    assert fv1.features == fv2.features
    assert fv1.feature_names == fv2.feature_names
    assert fv1.feature_timestamp == fv2.feature_timestamp
    assert fv1.missing_features == fv2.missing_features


def test_strict_temporal_anti_leakage(in_memory_db: Database) -> None:
    """Verify that NO transactions observed after first_seen_at contaminate features.

    Scenario:
    - T_past   at t = 100
    - T_target at t = 200
    - T_future at t = 300 (massive gas spike & 100 ETH transfer from same sender)

    Assert:
    - T_target features reflect T_past ONLY.
    - T_future is completely invisible to T_target's feature vector.
    """
    engine = FeatureEngine(in_memory_db)
    sender = "0x" + "a" * 40
    receiver = "0x" + "b" * 40

    t_past = datetime(2026, 1, 1, 12, 1, 40, tzinfo=timezone.utc)
    t_target = datetime(2026, 1, 1, 12, 3, 20, tzinfo=timezone.utc)
    t_future = datetime(2026, 1, 1, 12, 5, 0, tzinfo=timezone.utc)

    # Past tx: 1 ETH, 20 Gwei
    tx_past = make_test_tx("0x" + "1" * 64, sender, receiver, 10**18, t_past, gas_price_wei=20_000_000_000)
    in_memory_db.insert_decoded_transaction(tx_past)

    # Future tx: 100 ETH, 500 Gwei (severe burst & spike)
    tx_future = make_test_tx("0x" + "3" * 64, sender, receiver, 100 * 10**18, t_future, gas_price_wei=500_000_000_000)
    in_memory_db.insert_decoded_transaction(tx_future)

    # Target tx: 2 ETH at t_target
    tx_target = make_test_tx("0x" + "2" * 64, sender, receiver, 2 * 10**18, t_target, gas_price_wei=25_000_000_000)

    # Extract features for target
    fv = engine.extract_features(tx_target)
    f = fv.features

    # 1. Historical count must be EXACTLY 1 (T_past only, NOT 2!)
    assert f["sender_historical_tx_count"] == 1, (
        f"Temporal leakage detected! Expected 1 past transaction, got {f['sender_historical_tx_count']}"
    )

    # 2. Historical average value must be 1.0 ETH (T_past only, NOT polluted by T_future's 100 ETH!)
    assert f["sender_historical_avg_value_eth"] == 1.0, (
        f"Temporal leakage detected! Value corrupted by future tx: {f['sender_historical_avg_value_eth']}"
    )

    # 3. Future gas price (500 Gwei) must NOT leak into target's window baseline
    assert f["window_gas_price_mean_gwei"] < 100.0, (
        f"Temporal leakage detected! Window gas corrupted by future tx: {f['window_gas_price_mean_gwei']}"
    )

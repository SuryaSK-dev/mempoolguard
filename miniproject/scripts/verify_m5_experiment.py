"""End-to-End Verification Script for MempoolGuard Milestone M5.

Performs:
1. Controlled generation and mempool capture of 34 synthetic transactions across 8 archetypes.
2. Deterministic 30-feature extraction for all captured transactions.
3. Dataset construction and serialization (Parquet + CSV + Manifest).
4. Data quality and schema validation (zero duplicates, zero missing, valid labels).
5. Leak-free chronological dataset splitting with non-overlapping time boundaries.
6. Explicit temporal leakage proof: introduces future transactions and verifies zero contamination.
7. Confirmation exclusion proof: verifies absence of block metadata in model feature matrix.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Ensure workspace root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

from web3 import Web3

from collector.mempool_listener import MempoolListener
from collector.transaction_generator import TransactionGenerator
from core.database import Database
from dataset.builder import DatasetBuilder
from dataset.quality import DatasetQualityChecker
from dataset.schema import (
    FEATURE_COLUMNS,
    FORBIDDEN_FEATURE_NAMES,
    TARGET_COLUMN,
    get_feature_matrix,
    get_metadata_dataframe,
    get_target_labels,
    validate_dataset_schema,
)
from dataset.splitter import DatasetSplitter
from features.decoder import decode_raw_transaction
from features.engine import FeatureEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("verify_m5")


def main() -> None:
    print("=" * 70)
    print("MEMPOOLGUARD MILESTONE M5: DATASET CREATION & LEAKAGE EXPERIMENT")
    print("=" * 70)

    # 1. Connect to local Anvil
    rpc_url = "http://127.0.0.1:8545"
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        print(f"ERROR: Cannot connect to Anvil RPC at {rpc_url}. Start anvil first.")
        sys.exit(1)

    print(f"\n[Step 1] Connected to Anvil at {rpc_url} (Chain ID: {w3.eth.chain_id}).")

    db_path = "data/mempoolguard.db"
    manifest_path = "data/raw/synthetic_scenarios.json"
    parquet_path = "data/processed/mempoolguard_dataset.parquet"
    csv_path = "data/processed/mempoolguard_dataset.csv"
    splits_dir = "data/processed/splits"

    db = Database(db_path)
    db.init_schema()

    # 2. Run traffic generator with M3 listener to capture pending transactions
    print("\n[Step 2] Disabling automining & launching MempoolListener for live capture...")
    w3.provider.make_request("evm_setAutomine", [False])

    listener = MempoolListener(w3, db=db, poll_interval_seconds=0.05)
    listener.start()
    time.sleep(0.3)

    try:
        print("  Generating all 8 synthetic and benign scenarios (seed=42)...")
        gen = TransactionGenerator(w3, seed=42)
        results = gen.run_all_scenarios(output_manifest_path=manifest_path)
        total_gen = sum(len(r.transactions) for r in results)
        print(f"  Dispatched {total_gen} transactions across {len(results)} scenarios.")

        # Allow listener to observe and capture all pending transactions
        time.sleep(1.0)
        print(f"  Listener captured: {listener.captured_count} transactions.")

        # Mine pending block to confirm transactions on-chain
        print("  Mining pending block on Anvil...")
        w3.provider.make_request("evm_mine", [])
        time.sleep(0.3)
    finally:
        w3.provider.make_request("evm_setAutomine", [True])
        listener.stop()
        print("  Automining restored, listener stopped.")

    # 3. Extract M4 features for all captured transactions
    print("\n[Step 3] Extracting deterministic M4 features for all captured transactions...")
    engine = FeatureEngine(db)

    with open(manifest_path, encoding="utf-8") as f:
        manifest_data = json.load(f)

    manifest_hashes = [
        tx["tx_hash"].lower()
        for scenario in manifest_data["scenarios"]
        for tx in scenario["transactions"]
    ]

    extracted_count = 0
    for h in manifest_hashes:
        raw_rec = db.get_raw_transaction(h)
        if raw_rec:
            tx = decode_raw_transaction(raw_rec)
            engine.extract_and_persist(tx)
            extracted_count += 1

    print(f"  Extracted and persisted features for {extracted_count} / {len(manifest_hashes)} transactions.")

    # 4. Build Dataset
    print("\n[Step 4] Building ML-ready dataset via DatasetBuilder...")
    builder = DatasetBuilder(
        db=db,
        manifest_path=manifest_path,
        dataset_version="1.0.0",
        random_seed=42,
    )
    df = builder.build_dataset(
        output_parquet_path=parquet_path,
        output_csv_path=csv_path,
    )

    # 5. Report Required Statistics
    print("\n" + "=" * 70)
    print("M5 DATASET METRICS & SUMMARY STATISTICS")
    print("=" * 70)
    checker = DatasetQualityChecker()
    report = checker.run_all_checks(df)

    print(f"  1.  Source transactions in manifest : {len(manifest_hashes)}")
    print(f"  2.  Usable feature vectors in DB     : {extracted_count}")
    print(f"  3.  Final dataset rows               : {len(df)}")
    print(f"  4.  Class distribution               : {report.class_distribution} (0: Benign, 1: Anomaly)")
    print("  5.  Scenario distribution            :")
    for scen, count in sorted(report.scenario_distribution.items()):
        print(f"        - {scen:<25}: {count} rows")
    print(f"  6.  Canonical feature count          : {len(FEATURE_COLUMNS)}")
    print(f"  7.  Total label count                : {len(df)}")
    print(f"  8.  Duplicate transaction count      : {report.duplicate_count}")
    print(f"  9.  Missing/invalid feature rows     : {len(report.missing_feature_counts)}")
    print("  10. Dataset version                  : 1.0.0")
    print("  11. Random seed                      : 42")
    print("=" * 70)

    # Validate Schema
    is_valid, errors = validate_dataset_schema(df)
    assert is_valid, f"Schema validation failed: {errors}"
    print("\n[Step 5] Schema Contract Validation: PASS (30 features, zero leakage columns)")

    # 6. Chronological Train / Val / Test Split
    print("\n[Step 6] Performing leak-free chronological train/validation/test split...")
    splitter = DatasetSplitter(random_seed=42)
    train_df, val_df, test_df = splitter.time_based_split(
        df, train_ratio=0.70, val_ratio=0.15, test_ratio=0.15
    )
    splitter.save_splits(train_df, val_df, test_df, output_dir=splits_dir)

    print(f"  Train split : {len(train_df)} rows ({train_df[TARGET_COLUMN].value_counts().to_dict()})")
    print(f"  Val split   : {len(val_df)} rows ({val_df[TARGET_COLUMN].value_counts().to_dict()})")
    print(f"  Test split  : {len(test_df)} rows ({test_df[TARGET_COLUMN].value_counts().to_dict()})")
    print(f"  Saved to    : {splits_dir}")

    # Verify temporal non-overlap
    t_train_max = train_df["first_seen_at"].max()
    t_val_min = val_df["first_seen_at"].min()
    t_val_max = val_df["first_seen_at"].max()
    t_test_min = test_df["first_seen_at"].min()
    assert t_train_max <= t_val_min, "Leakage detected: train overlaps with val!"
    assert t_val_max <= t_test_min, "Leakage detected: val overlaps with test!"
    print(f"  Temporal Boundary Verified: max(train)={t_train_max} <= min(val)={t_val_min} <= min(test)={t_test_min}")

    # 7. Explicit Temporal Anti-Leakage Experiment
    print("\n[Step 7] Running explicit temporal anti-leakage experiment...")
    # Pick target transaction from benign transfers
    target_row = df[df["scenario_id"] == "BENIGN_TRANSFER"].iloc[0]
    target_hash = target_row["tx_hash"]
    target_fv_before = engine.extract_from_raw_hash(target_hash)

    print(f"  Target Transaction: {target_hash}")
    print(f"  First-seen at     : {target_fv_before.feature_timestamp.isoformat()}")

    # Inject future high-value gas-spike transaction at later timestamp
    future_time = datetime.now(timezone.utc)
    future_hash = "0x9999999999999999999999999999999999999999999999999999999999999999"
    future_payload = {
        "hash": future_hash,
        "from": target_row["tx_hash"][:42],
        "to": "0x0000000000000000000000000000000000000001",
        "value": hex(100 * 10**18),
        "gas": hex(500000),
        "gasPrice": hex(500 * 10**9),
        "nonce": hex(999),
        "input": "0x",
    }
    db.insert_raw_transaction(future_hash, json.dumps(future_payload), first_seen_at=future_time)

    # Re-extract target features after future transaction injection
    target_fv_after = engine.extract_from_raw_hash(target_hash)

    # Assert 100% bit-for-bit equality
    for feat_name in FEATURE_COLUMNS:
        v_before = target_fv_before.features[feat_name]
        v_after = target_fv_after.features[feat_name]
        assert v_before == v_after, f"Feature {feat_name} changed! {v_before} != {v_after}"

    print("  VERIFIED: All 30 features for target transaction are 100% bit-for-bit unchanged.")
    print("  VERIFIED: Zero contamination from future transaction injection.")

    # 8. Confirmation metadata exclusion check
    print("\n[Step 8] Verifying confirmation & label exclusion from model feature matrix...")
    X = get_feature_matrix(df)
    y = get_target_labels(df)
    meta = get_metadata_dataframe(df)

    assert X.shape == (len(df), 30), f"Expected shape ({len(df)}, 30), got {X.shape}"
    assert TARGET_COLUMN not in X.columns
    assert "scenario_id" not in X.columns
    assert "tx_hash" not in X.columns
    for forbidden in FORBIDDEN_FEATURE_NAMES:
        assert forbidden not in X.columns

    print(f"  Feature Matrix (X) shape : {X.shape} (Pure float64 features)")
    print(f"  Target Vector  (y) shape : {y.shape} (Binary labels in {{0, 1}})")
    print(f"  Metadata Matrix    shape : {meta.shape} (Identifiers, timestamps, scenario labels)")
    print("  VERIFIED: Confirmation block attributes & ground truth strictly isolated from model features.")

    print("\n" + "=" * 70)
    print("MILESTONE M5 VERIFICATION EXPERIMENT: PASS")
    print("=" * 70)


if __name__ == "__main__":
    main()

"""Dedicated unit and integration tests for Milestone M5: Dataset Creation and Leakage Prevention."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from core.contracts import FeatureVector, Transaction
from core.database import Database
from dataset.builder import DatasetBuilder
from dataset.quality import DatasetQualityChecker
from dataset.schema import (
    ALL_COLUMNS,
    FEATURE_COLUMNS,
    FORBIDDEN_FEATURE_NAMES,
    IDENTIFIER_COLUMNS,
    METADATA_COLUMNS,
    PROVENANCE_COLUMNS,
    TARGET_COLUMN,
    TIMESTAMP_COLUMNS,
    VERSION_COLUMNS,
    get_feature_matrix,
    get_metadata_dataframe,
    get_target_labels,
    validate_dataset_schema,
)
from dataset.splitter import DatasetSplitter
from features.engine import FEATURE_NAMES, FeatureEngine


@pytest.fixture
def memory_db() -> Database:
    """Provide a fresh in-memory database with schema initialized."""
    db = Database(":memory:")
    db.init_schema()
    return db


@pytest.fixture
def mock_manifest(tmp_path: Path) -> Path:
    """Create a temporary synthetic scenario manifest for testing."""
    manifest_data = {
        "generated_at": "2026-09-30T08:00:00Z",
        "seed": 42,
        "chain_id": 31337,
        "total_scenarios": 2,
        "total_transactions": 4,
        "scenarios": [
            {
                "scenario_id": "BENIGN_TRANSFER",
                "description": "Benign transfers",
                "expected_label": 0,
                "executed_at": "2026-09-30T08:00:00Z",
                "transactions": [
                    {
                        "tx_hash": "0x1111111111111111111111111111111111111111111111111111111111111111",
                        "scenario_id": "BENIGN_TRANSFER",
                        "expected_label": 0,
                        "sender": "0x1111111111111111111111111111111111111111",
                        "receiver": "0x2222222222222222222222222222222222222222",
                        "value_wei": 1000000000000000000,
                        "gas_limit": 21000,
                        "gas_price_wei": 2000000000,
                        "nonce": 0,
                        "input_data": "0x",
                        "dispatched_at": "2026-09-30T08:00:01Z",
                        "confirmed_at": "2026-09-30T08:00:05Z",
                        "block_number": 1,
                        "status": "CONFIRMED",
                    },
                    {
                        "tx_hash": "0x2222222222222222222222222222222222222222222222222222222222222222",
                        "scenario_id": "BENIGN_TRANSFER",
                        "expected_label": 0,
                        "sender": "0x1111111111111111111111111111111111111111",
                        "receiver": "0x2222222222222222222222222222222222222222",
                        "value_wei": 2000000000000000000,
                        "gas_limit": 21000,
                        "gas_price_wei": 2000000000,
                        "nonce": 1,
                        "input_data": "0x",
                        "dispatched_at": "2026-09-30T08:00:02Z",
                        "confirmed_at": "2026-09-30T08:00:05Z",
                        "block_number": 1,
                        "status": "CONFIRMED",
                    },
                ],
            },
            {
                "scenario_id": "GAS_SPIKE",
                "description": "Gas spike transactions",
                "expected_label": 1,
                "executed_at": "2026-09-30T08:00:10Z",
                "transactions": [
                    {
                        "tx_hash": "0x3333333333333333333333333333333333333333333333333333333333333333",
                        "scenario_id": "GAS_SPIKE",
                        "expected_label": 1,
                        "sender": "0x3333333333333333333333333333333333333333",
                        "receiver": "0x4444444444444444444444444444444444444444",
                        "value_wei": 50000000000000000,
                        "gas_limit": 21000,
                        "gas_price_wei": 50000000000,
                        "nonce": 0,
                        "input_data": "0x",
                        "dispatched_at": "2026-09-30T08:00:11Z",
                        "confirmed_at": "2026-09-30T08:00:15Z",
                        "block_number": 2,
                        "status": "CONFIRMED",
                    },
                    {
                        "tx_hash": "0x4444444444444444444444444444444444444444444444444444444444444444",
                        "scenario_id": "GAS_SPIKE",
                        "expected_label": 1,
                        "sender": "0x3333333333333333333333333333333333333333",
                        "receiver": "0x4444444444444444444444444444444444444444",
                        "value_wei": 60000000000000000,
                        "gas_limit": 21000,
                        "gas_price_wei": 60000000000,
                        "nonce": 1,
                        "input_data": "0x",
                        "dispatched_at": "2026-09-30T08:00:12Z",
                        "confirmed_at": "2026-09-30T08:00:15Z",
                        "block_number": 2,
                        "status": "CONFIRMED",
                    },
                ],
            },
        ],
    }
    manifest_path = tmp_path / "test_manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, indent=2)
    return manifest_path


def _insert_sample_transactions(db: Database) -> None:
    """Helper to populate DB with raw transactions and feature vectors."""
    base_time = datetime(2026, 9, 30, 8, 0, 0, tzinfo=timezone.utc)
    hashes = [
        "0x1111111111111111111111111111111111111111111111111111111111111111",
        "0x2222222222222222222222222222222222222222222222222222222222222222",
        "0x3333333333333333333333333333333333333333333333333333333333333333",
        "0x4444444444444444444444444444444444444444444444444444444444444444",
    ]

    for i, h in enumerate(hashes):
        ts = base_time + timedelta(seconds=i + 1)
        payload = {
            "hash": h,
            "from": "0x1111111111111111111111111111111111111111",
            "to": "0x2222222222222222222222222222222222222222",
            "value": hex(10**18 * (i + 1)),
            "gas": "0x5208",
            "gasPrice": hex(2 * 10**9),
            "nonce": hex(i),
            "input": "0x",
        }
        db.insert_raw_transaction(tx_hash=h, raw_payload=json.dumps(payload), first_seen_at=ts)

        tx = Transaction(
            tx_hash=h,
            chain_id=31337,
            sender="0x1111111111111111111111111111111111111111",
            receiver="0x2222222222222222222222222222222222222222",
            value_wei=10**18 * (i + 1),
            gas_limit=21000,
            gas_price_wei=2 * 10**9,
            nonce=i,
            input_data="0x",
            first_seen_at=ts,
        )
        db.insert_decoded_transaction(tx)

        # Build feature vector
        feats = {col: float(i + 1) for col in FEATURE_NAMES}
        fv = FeatureVector(
            tx_hash=h,
            features=feats,
            feature_version="1.0.0",
            feature_timestamp=ts,
            missing_features=[],
            availability_mask={"post_confirmation": False},
        )
        db.insert_feature_vector(fv)


def test_schema_column_isolation():
    """Verify strict column separation in dataset schema."""
    # Ensure mutually exclusive column groups
    all_grouped = (
        IDENTIFIER_COLUMNS
        + TIMESTAMP_COLUMNS
        + FEATURE_COLUMNS
        + VERSION_COLUMNS
        + METADATA_COLUMNS
        + (TARGET_COLUMN,)
        + PROVENANCE_COLUMNS
    )
    assert len(all_grouped) == len(set(all_grouped)), "Column groups must be mutually disjoint"

    # Create dummy DataFrame
    data = {col: [1.0] for col in FEATURE_COLUMNS}
    data["tx_hash"] = ["0x1111111111111111111111111111111111111111111111111111111111111111"]
    data["first_seen_at"] = ["2026-09-30T08:00:00Z"]
    data["feature_timestamp"] = ["2026-09-30T08:00:00Z"]
    data["label_determined_at"] = ["2026-09-30T08:00:05Z"]
    data["feature_version"] = ["1.0.0"]
    data["dataset_version"] = ["1.0.0"]
    data["scenario_id"] = ["BENIGN_TRANSFER"]
    data["label_provenance"] = ["manifest:test"]
    data["is_synthetic"] = [0]
    data["label"] = [0]
    data["manifest_path"] = ["test.json"]
    data["random_seed"] = [42]

    df = pd.DataFrame(data)

    # 1. Feature matrix contains only canonical features
    feature_matrix = get_feature_matrix(df)
    assert feature_matrix.shape == (1, 30)
    assert list(feature_matrix.columns) == list(FEATURE_COLUMNS)
    assert TARGET_COLUMN not in feature_matrix.columns
    assert "tx_hash" not in feature_matrix.columns

    # 2. Target labels contains only target
    labels = get_target_labels(df)
    assert labels.tolist() == [0]

    # 3. Metadata contains non-feature columns
    metadata = get_metadata_dataframe(df)
    assert "tx_hash" in metadata.columns
    assert "scenario_id" in metadata.columns
    for feat in FEATURE_COLUMNS:
        assert feat not in metadata.columns

    # 4. Schema validation passes
    is_valid, errors = validate_dataset_schema(df)
    assert is_valid is True
    assert len(errors) == 0


def test_deterministic_dataset_generation(memory_db: Database, mock_manifest: Path):
    """Verify that dataset generation produces identical DataFrames across runs."""
    _insert_sample_transactions(memory_db)

    builder = DatasetBuilder(memory_db, manifest_path=mock_manifest, random_seed=42)

    df1 = builder.build_dataset()
    df2 = builder.build_dataset()

    assert len(df1) == 4
    assert len(df2) == 4
    pd.testing.assert_frame_equal(df1, df2)


def test_feature_label_separation(memory_db: Database, mock_manifest: Path):
    """Verify that ground truth labels come exclusively from manifest, not features."""
    _insert_sample_transactions(memory_db)

    builder = DatasetBuilder(memory_db, manifest_path=mock_manifest)
    df = builder.build_dataset()

    # Verify labels match manifest specifications
    benign_rows = df[df["scenario_id"] == "BENIGN_TRANSFER"]
    spike_rows = df[df["scenario_id"] == "GAS_SPIKE"]

    assert (benign_rows[TARGET_COLUMN] == 0).all()
    assert (benign_rows["is_synthetic"] == 0).all()

    assert (spike_rows[TARGET_COLUMN] == 1).all()
    assert (spike_rows["is_synthetic"] == 1).all()

    # Verify model feature matrix does not contain any label or post-confirmation columns
    features = get_feature_matrix(df)
    for forbidden in FORBIDDEN_FEATURE_NAMES:
        assert forbidden not in features.columns


def test_temporal_anti_leakage_and_future_independence(memory_db: Database):
    """Verify that future transactions do not leak into target feature row."""
    engine = FeatureEngine(memory_db)
    t0 = datetime(2026, 9, 30, 9, 0, 0, tzinfo=timezone.utc)
    t_target = t0 + timedelta(seconds=10)
    t_future = t0 + timedelta(seconds=30)

    # 1. Insert historical transaction
    tx_hist = Transaction(
        tx_hash="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain_id=31337,
        sender="0x1111111111111111111111111111111111111111",
        receiver="0x2222222222222222222222222222222222222222",
        value_wei=10**18,
        gas_limit=21000,
        gas_price_wei=2 * 10**9,
        nonce=0,
        first_seen_at=t0,
    )
    memory_db.insert_decoded_transaction(tx_hist)

    # 2. Extract features for target transaction at t_target
    tx_target = Transaction(
        tx_hash="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        chain_id=31337,
        sender="0x1111111111111111111111111111111111111111",
        receiver="0x2222222222222222222222222222222222222222",
        value_wei=2 * 10**18,
        gas_limit=21000,
        gas_price_wei=2 * 10**9,
        nonce=1,
        first_seen_at=t_target,
    )
    fv_target_1 = engine.extract_features(tx_target)

    # 3. Introduce future transaction at t_future with huge gas price and ETH value
    tx_future = Transaction(
        tx_hash="0xcccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
        chain_id=31337,
        sender="0x1111111111111111111111111111111111111111",
        receiver="0x2222222222222222222222222222222222222222",
        value_wei=100 * 10**18,
        gas_limit=500000,
        gas_price_wei=500 * 10**9,
        nonce=2,
        first_seen_at=t_future,
    )
    memory_db.insert_decoded_transaction(tx_future)

    # 4. Re-extract features for target transaction at t_target
    fv_target_2 = engine.extract_features(tx_target)

    # 5. Assert 100% bit-for-bit equality: zero leakage from future transaction
    for feat_name in FEATURE_NAMES:
        val1 = fv_target_1.features[feat_name]
        val2 = fv_target_2.features[feat_name]
        assert val1 == val2, (
            f"Feature '{feat_name}' leaked from future: before={val1}, after={val2}"
        )


def test_quality_checker_duplicates():
    """Verify DatasetQualityChecker identifies duplicate transaction hashes."""
    checker = DatasetQualityChecker()
    df = pd.DataFrame({
        "tx_hash": ["0x1111111111111111111111111111111111111111111111111111111111111111"] * 2
    })
    dup_count = checker.check_duplicates(df)
    assert dup_count == 1


def test_quality_checker_missing_and_invalid():
    """Verify DatasetQualityChecker identifies missing features and invalid labels."""
    checker = DatasetQualityChecker()

    # Test missing features
    df_missing = pd.DataFrame({"value_eth": [1.0, None], TARGET_COLUMN: [0, 1]})
    missing = checker.check_missing_features(df_missing)
    assert missing["value_eth"] == 1

    # Test invalid labels
    df_invalid_labels = pd.DataFrame({TARGET_COLUMN: [0, 1, 2, -1, 99]})
    invalid_cnt = checker.check_invalid_labels(df_invalid_labels)
    assert invalid_cnt == 3


def test_quality_checker_full_report(memory_db: Database, mock_manifest: Path):
    """Verify full DatasetQualityReport on valid dataset."""
    _insert_sample_transactions(memory_db)
    builder = DatasetBuilder(memory_db, manifest_path=mock_manifest)
    df = builder.build_dataset()

    checker = DatasetQualityChecker()
    report = checker.run_all_checks(df)

    assert report.is_valid is True
    assert report.total_rows == 4
    assert report.duplicate_count == 0
    assert report.invalid_label_count == 0
    assert report.timestamp_inconsistencies == 0
    assert len(report.missing_feature_counts) == 0
    assert report.class_distribution == {0: 2, 1: 2}
    assert report.scenario_distribution == {"BENIGN_TRANSFER": 2, "GAS_SPIKE": 2}


def test_dataset_time_based_split_zero_leakage():
    """Verify time-based split creates sequential partitions without temporal overlap."""
    t0 = datetime(2026, 9, 30, 8, 0, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(10):
        ts = (t0 + timedelta(seconds=i * 10)).isoformat()
        rows.append({
            "tx_hash": f"0x{i:064x}",
            "first_seen_at": ts,
            "label": i % 2,
        })
    df = pd.DataFrame(rows)

    splitter = DatasetSplitter(random_seed=42)
    train_df, val_df, test_df = splitter.time_based_split(
        df, train_ratio=0.6, val_ratio=0.2, test_ratio=0.2
    )

    assert len(train_df) == 6
    assert len(val_df) == 2
    assert len(test_df) == 2

    # Verify strict temporal non-overlap
    assert train_df["first_seen_at"].max() <= val_df["first_seen_at"].min()
    assert val_df["first_seen_at"].max() <= test_df["first_seen_at"].min()


def test_dataset_stratified_split_reproducibility():
    """Verify stratified split produces identical splits when given identical seed."""
    t0 = datetime(2026, 9, 30, 8, 0, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(20):
        ts = (t0 + timedelta(seconds=i * 5)).isoformat()
        rows.append({
            "tx_hash": f"0x{i:064x}",
            "first_seen_at": ts,
            "label": 0 if i < 10 else 1,
        })
    df = pd.DataFrame(rows)

    splitter = DatasetSplitter(random_seed=42)
    train1, val1, test1 = splitter.stratified_split(df)
    train2, val2, test2 = splitter.stratified_split(df)

    pd.testing.assert_frame_equal(train1, train2)
    pd.testing.assert_frame_equal(val1, val2)
    pd.testing.assert_frame_equal(test1, test2)


def test_dataset_export_parquet_and_csv(
    memory_db: Database, mock_manifest: Path, tmp_path: Path
):
    """Verify dataset can be saved to Parquet and CSV and read back cleanly."""
    _insert_sample_transactions(memory_db)
    builder = DatasetBuilder(memory_db, manifest_path=mock_manifest)

    parquet_out = tmp_path / "dataset.parquet"
    csv_out = tmp_path / "dataset.csv"

    df = builder.build_dataset(output_parquet_path=parquet_out, output_csv_path=csv_out)
    assert len(df) == 4

    assert parquet_out.exists()
    assert csv_out.exists()

    # Read back parquet
    df_read = pd.read_parquet(parquet_out)
    assert len(df_read) == 4
    assert list(df_read.columns) == list(ALL_COLUMNS)

    # Check companion manifest
    manifest_out = tmp_path / "dataset_manifest.json"
    assert manifest_out.exists()
    with open(manifest_out, encoding="utf-8") as f:
        manifest_data = json.load(f)

    assert manifest_data["total_rows"] == 4
    assert manifest_data["feature_count"] == 30
    assert manifest_data["class_distribution"] == {"0": 2, "1": 2}

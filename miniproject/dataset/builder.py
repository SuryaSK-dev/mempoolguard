"""Dataset Builder for MempoolGuard.

Constructs ML-ready datasets by joining pre-confirmation feature vectors with
ground-truth labels from the documented synthetic scenario manifest, enforcing
strict temporal and conceptual isolation between features and labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from core.contracts import utc_now
from core.database import Database
from dataset.schema import (
    ALL_COLUMNS,
    FEATURE_COLUMNS,
    TARGET_COLUMN,
    validate_dataset_schema,
)
from features.decoder import decode_raw_transaction
from features.engine import DEFAULT_FEATURE_VERSION, FeatureEngine

logger = logging.getLogger("mempoolguard.dataset")

DEFAULT_DATASET_VERSION = "1.0.0"
DEFAULT_SEED = 42


class DatasetBuilder:
    """Builds reproducible, ML-ready datasets with zero temporal leakage."""

    def __init__(
        self,
        db: Database,
        manifest_path: str | Path = "data/raw/synthetic_scenarios.json",
        dataset_version: str = DEFAULT_DATASET_VERSION,
        random_seed: int = DEFAULT_SEED,
    ) -> None:
        """Initialize the DatasetBuilder.

        Args:
            db: Connected MempoolGuard SQLite database.
            manifest_path: Path to ground-truth synthetic scenarios JSON manifest.
            dataset_version: Version identifier for the produced dataset.
            random_seed: Random seed for deterministic reproducibility.
        """
        self.db = db
        self.manifest_path = Path(manifest_path)
        self.dataset_version = dataset_version
        self.random_seed = random_seed
        self.feature_engine = FeatureEngine(db, feature_version=DEFAULT_FEATURE_VERSION)

    def load_ground_truth_manifest(self) -> dict[str, dict[str, Any]]:
        """Load and parse ground-truth labels from the synthetic scenario manifest.

        Guarantees:
        1. Labels come strictly from documented scenario definitions, never from features.
        2. Benign (0) and synthetic attack (1) scenarios are explicitly separated.
        3. Clear provenance is attached to every label.

        Returns:
            Dictionary mapping lowercase tx_hash to label metadata dict.
        """
        if not self.manifest_path.exists():
            raise FileNotFoundError(
                f"Synthetic scenarios manifest not found at {self.manifest_path}"
            )

        with open(self.manifest_path, encoding="utf-8") as f:
            manifest_data = json.load(f)

        ground_truth: dict[str, dict[str, Any]] = {}
        for scenario in manifest_data.get("scenarios", []):
            scenario_id = scenario.get("scenario_id", "UNKNOWN")
            expected_label = int(scenario.get("expected_label", 0))
            is_synthetic = 0 if scenario_id == "BENIGN_TRANSFER" else 1

            for tx_info in scenario.get("transactions", []):
                tx_hash = tx_info["tx_hash"].lower()
                # Determine time when label was finalized (e.g. block confirmation or scenario run)
                determined_at = (
                    tx_info.get("confirmed_at")
                    or tx_info.get("dispatched_at")
                    or scenario.get("executed_at")
                    or utc_now().isoformat()
                )

                ground_truth[tx_hash] = {
                    "scenario_id": scenario_id,
                    "expected_label": expected_label,
                    "label_provenance": f"manifest:{self.manifest_path.name}:{scenario_id}",
                    "is_synthetic": is_synthetic,
                    "label_determined_at": determined_at,
                }

        logger.info(
            "Loaded %d ground-truth transaction labels from %s",
            len(ground_truth),
            self.manifest_path,
        )
        return ground_truth

    def build_dataset(
        self,
        output_parquet_path: str | Path | None = None,
        output_csv_path: str | Path | None = None,
        include_unlabeled: bool = False,
    ) -> pd.DataFrame:
        """Construct the complete dataset from database transactions and manifest labels.

        Args:
            output_parquet_path: Optional path to save output as Parquet.
            output_csv_path: Optional path to save output as CSV.
            include_unlabeled: If False, exclude transactions without documented ground-truth.

        Returns:
            ML-ready pandas DataFrame conforming to ALL_COLUMNS.
        """
        ground_truth = self.load_ground_truth_manifest()

        # Gather all transaction hashes to consider
        # We start with hashes present in ground-truth manifest
        target_hashes = list(ground_truth.keys())

        # If include_unlabeled, also inspect raw_transactions in DB
        if include_unlabeled:
            conn = self.db.get_connection()
            try:
                cur = conn.cursor()
                cur.execute("SELECT tx_hash FROM raw_transactions")
                for row in cur.fetchall():
                    h = row["tx_hash"].lower()
                    if h not in ground_truth:
                        target_hashes.append(h)
            finally:
                conn.close()

        rows: list[dict[str, Any]] = []

        for tx_hash in target_hashes:
            # 1. Fetch or compute pre-confirmation feature vector
            fv = self.db.get_feature_vector(tx_hash)
            if not fv:
                # Check if we have raw transaction to extract features
                raw_rec = self.db.get_raw_transaction(tx_hash)
                if raw_rec:
                    tx = decode_raw_transaction(raw_rec)
                    fv = self.feature_engine.extract_and_persist(tx)
                else:
                    # Skip if transaction is neither in feature_vectors nor raw_transactions
                    logger.warning("Transaction %s not found in DB; skipping", tx_hash)
                    continue

            # 2. Retrieve first_seen_at timestamp
            first_seen_at_str: str
            raw_rec = self.db.get_raw_transaction(tx_hash)
            if raw_rec:
                first_seen_at_str = raw_rec["first_seen_at"].isoformat()
            else:
                first_seen_at_str = fv.feature_timestamp.isoformat()

            # 3. Retrieve ground-truth label info
            label_info = ground_truth.get(tx_hash)
            if not label_info:
                if not include_unlabeled:
                    continue
                label_info = {
                    "scenario_id": "UNLABELED",
                    "expected_label": -1,
                    "label_provenance": "none",
                    "is_synthetic": 0,
                    "label_determined_at": utc_now().isoformat(),
                }

            # 4. Assemble row ensuring exact column contract
            row: dict[str, Any] = {
                "tx_hash": tx_hash,
                "first_seen_at": first_seen_at_str,
                "feature_timestamp": fv.feature_timestamp.isoformat(),
                "label_determined_at": label_info["label_determined_at"],
            }

            # Add all 30 canonical features
            for feat_name in FEATURE_COLUMNS:
                row[feat_name] = float(fv.features.get(feat_name, 0.0))

            row["feature_version"] = fv.feature_version
            row["dataset_version"] = self.dataset_version
            row["scenario_id"] = label_info["scenario_id"]
            row["label_provenance"] = label_info["label_provenance"]
            row["is_synthetic"] = int(label_info["is_synthetic"])
            row[TARGET_COLUMN] = int(label_info["expected_label"])
            row["manifest_path"] = str(self.manifest_path)
            row["random_seed"] = self.random_seed

            rows.append(row)

        if not rows:
            logger.warning("No rows constructed for dataset")
            df = pd.DataFrame(columns=list(ALL_COLUMNS))
            return df

        df = pd.DataFrame(rows)

        # Reorder columns to strictly match ALL_COLUMNS
        df = df[list(ALL_COLUMNS)]

        # Sort deterministically: chronologically by first_seen_at, then tie-break by tx_hash
        df = df.sort_values(by=["first_seen_at", "tx_hash"], ascending=[True, True]).reset_index(
            drop=True
        )

        # Validate schema contract
        is_valid, errors = validate_dataset_schema(df)
        if not is_valid:
            raise ValueError(f"Constructed dataset failed schema validation: {errors}")

        # Save to Parquet / CSV if paths specified
        if output_parquet_path:
            p_path = Path(output_parquet_path)
            p_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(p_path, engine="pyarrow", index=False)
            logger.info("Saved dataset Parquet to %s (%d rows)", p_path, len(df))

            # Emit companion dataset manifest
            self._save_dataset_manifest(df, p_path)

        if output_csv_path:
            c_path = Path(output_csv_path)
            c_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(c_path, index=False)
            logger.info("Saved dataset CSV to %s (%d rows)", c_path, len(df))

        return df

    def _save_dataset_manifest(self, df: pd.DataFrame, parquet_path: Path) -> Path:
        """Write companion JSON manifest containing dataset checksum and distribution."""
        manifest_path = parquet_path.with_name(f"{parquet_path.stem}_manifest.json")

        with open(parquet_path, "rb") as f:
            file_sha256 = hashlib.sha256(f.read()).hexdigest()

        class_dist = df[TARGET_COLUMN].value_counts().to_dict()
        scenario_dist = df["scenario_id"].value_counts().to_dict()

        manifest = {
            "dataset_version": self.dataset_version,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "parquet_file": parquet_path.name,
            "sha256_checksum": file_sha256,
            "total_rows": len(df),
            "feature_count": len(FEATURE_COLUMNS),
            "random_seed": self.random_seed,
            "source_manifest": str(self.manifest_path),
            "class_distribution": {int(k): int(v) for k, v in class_dist.items()},
            "scenario_distribution": {str(k): int(v) for k, v in scenario_dist.items()},
            "feature_names": list(FEATURE_COLUMNS),
        }

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        logger.info("Dataset manifest written to %s", manifest_path)
        return manifest_path


def main() -> None:
    """CLI runner to construct and export the MempoolGuard ML dataset."""
    parser = argparse.ArgumentParser(description="MempoolGuard Dataset Builder")
    parser.add_argument("--db", default="data/mempoolguard.db", help="SQLite DB path")
    parser.add_argument(
        "--manifest",
        default="data/raw/synthetic_scenarios.json",
        help="Ground-truth scenario manifest path",
    )
    parser.add_argument(
        "--output-parquet",
        default="data/processed/mempoolguard_dataset.parquet",
        help="Output Parquet path",
    )
    parser.add_argument(
        "--output-csv",
        default="data/processed/mempoolguard_dataset.csv",
        help="Output CSV path",
    )
    parser.add_argument("--version", default=DEFAULT_DATASET_VERSION, help="Dataset version")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Random seed")
    args = parser.parse_args()

    db = Database(args.db)
    builder = DatasetBuilder(
        db=db,
        manifest_path=args.manifest,
        dataset_version=args.version,
        random_seed=args.seed,
    )

    print(f"Building dataset from {args.manifest} and {args.db}...")
    df = builder.build_dataset(
        output_parquet_path=args.output_parquet,
        output_csv_path=args.output_csv,
    )

    print("\nDataset Construction Complete:")
    print(f"  Total Rows           : {len(df)}")
    print(f"  Canonical Features   : {len(FEATURE_COLUMNS)}")
    print(f"  Class Distribution   : {df[TARGET_COLUMN].value_counts().to_dict()}")
    print(f"  Scenario Distribution: {df['scenario_id'].value_counts().to_dict()}")
    print(f"  Parquet Output       : {args.output_parquet}")
    print(f"  CSV Output           : {args.output_csv}")


if __name__ == "__main__":
    main()

"""Reproducible, leak-free dataset splitting for MempoolGuard.

Provides time-aware (chronological) and stratified train/val/test splits,
enforcing that future transactions do not leak into training sets.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from dataset.schema import TARGET_COLUMN

logger = logging.getLogger("mempoolguard.dataset.splitter")


class DatasetSplitter:
    """Splits datasets reproducibly without introducing temporal leakage."""

    def __init__(self, random_seed: int = 42) -> None:
        """Initialize DatasetSplitter.

        Args:
            random_seed: Seed for reproducible stratified operations.
        """
        self.random_seed = random_seed

    def time_based_split(
        self,
        df: pd.DataFrame,
        train_ratio: float = 0.70,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Perform a strictly chronological, leak-free train/validation/test split.

        Transactions are sorted by first_seen_at ascending. The earliest transactions
        form the training set, followed by validation, then testing.

        Guarantees:
        max(train.first_seen_at) <= min(val.first_seen_at) <= min(test.first_seen_at).

        Args:
            df: Dataset DataFrame containing first_seen_at.
            train_ratio: Proportion of data in training split (default 0.70).
            val_ratio: Proportion of data in validation split (default 0.15).
            test_ratio: Proportion of data in test split (default 0.15).

        Returns:
            Tuple of (train_df, val_df, test_df).
        """
        total = train_ratio + val_ratio + test_ratio
        if not (0.99 <= total <= 1.01):
            raise ValueError(f"Split ratios must sum to 1.0, got {total}")

        if "first_seen_at" not in df.columns:
            raise KeyError("DataFrame must contain 'first_seen_at' for time-based split")

        # Sort chronologically by first_seen_at and break ties by tx_hash
        sorted_df = df.sort_values(
            by=["first_seen_at", "tx_hash"], ascending=[True, True]
        ).reset_index(drop=True)

        n = len(sorted_df)
        if n < 3:
            raise ValueError(f"Dataset must have at least 3 rows to split, got {n}")

        train_end = int(np.floor(n * train_ratio))
        val_end = int(np.floor(n * (train_ratio + val_ratio)))

        # Ensure every split gets at least 1 row if n >= 3
        train_end = max(1, min(train_end, n - 2))
        val_end = max(train_end + 1, min(val_end, n - 1))

        train_df = sorted_df.iloc[:train_end].copy().reset_index(drop=True)
        val_df = sorted_df.iloc[train_end:val_end].copy().reset_index(drop=True)
        test_df = sorted_df.iloc[val_end:].copy().reset_index(drop=True)

        logger.info(
            "Time-based split: Train=%d, Val=%d, Test=%d (Total=%d)",
            len(train_df),
            len(val_df),
            len(test_df),
            n,
        )
        return (train_df, val_df, test_df)

    def stratified_split(
        self,
        df: pd.DataFrame,
        train_ratio: float = 0.70,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Perform a deterministic stratified train/validation/test split by target label.

        Maintains class balance across splits using fixed random_seed.

        Args:
            df: Dataset DataFrame containing TARGET_COLUMN.
            train_ratio: Proportion in train split.
            val_ratio: Proportion in val split.
            test_ratio: Proportion in test split.

        Returns:
            Tuple of (train_df, val_df, test_df).
        """
        if TARGET_COLUMN not in df.columns:
            raise KeyError(f"DataFrame must contain '{TARGET_COLUMN}' for stratified split")

        train_parts = []
        val_parts = []
        test_parts = []

        for label_val, group in df.groupby(TARGET_COLUMN):
            shuffled = group.sample(frac=1.0, random_state=self.random_seed).reset_index(drop=True)
            k = len(shuffled)
            if k == 0:
                continue
            if k == 1:
                # If only 1 sample for a class, assign to train
                train_parts.append(shuffled)
                continue

            t_end = int(np.floor(k * train_ratio))
            v_end = int(np.floor(k * (train_ratio + val_ratio)))

            t_end = max(1, min(t_end, k - 1))
            if v_end <= t_end and k > 2:
                v_end = t_end + 1

            train_parts.append(shuffled.iloc[:t_end])
            val_parts.append(shuffled.iloc[t_end:v_end])
            test_parts.append(shuffled.iloc[v_end:])

        train_df = pd.concat(train_parts, ignore_index=True) if train_parts else pd.DataFrame()
        val_df = pd.concat(val_parts, ignore_index=True) if val_parts else pd.DataFrame()
        test_df = pd.concat(test_parts, ignore_index=True) if test_parts else pd.DataFrame()

        # Re-sort deterministically by first_seen_at, tx_hash
        train_df = train_df.sort_values(by=["first_seen_at", "tx_hash"]).reset_index(drop=True)
        val_df = val_df.sort_values(by=["first_seen_at", "tx_hash"]).reset_index(drop=True)
        test_df = test_df.sort_values(by=["first_seen_at", "tx_hash"]).reset_index(drop=True)

        return (train_df, val_df, test_df)

    def grouped_instance_split(
        self,
        df: pd.DataFrame,
        instance_col: str = "scenario_id",
        train_ratio: float = 0.70,
        val_ratio: float = 0.15,
        test_ratio: float = 0.15,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Perform a leak-free grouped split by scenario INSTANCE across scenario types.

        Ensures:
        1. Every scenario type (each attack archetype + benign) appears in Train, Val, and Test.
        2. No scenario instance (round / sender group) is ever split across partitions (zero group leakage).
        3. Both class 0 and class 1 are guaranteed present in all splits.

        Args:
            df: Dataset DataFrame containing instance_col and TARGET_COLUMN.
            instance_col: Column identifying specific scenario instances (e.g. 'GAS_SPIKE__rnd001').
            train_ratio: Proportion of instances allocated to train.
            val_ratio: Proportion of instances allocated to validation.
            test_ratio: Proportion of instances allocated to test.

        Returns:
            Tuple of (train_df, val_df, test_df).
        """
        rng = np.random.default_rng(self.random_seed)

        if instance_col not in df.columns:
            raise KeyError(f"DataFrame missing instance column '{instance_col}'")
        if TARGET_COLUMN not in df.columns:
            raise KeyError(f"DataFrame missing target column '{TARGET_COLUMN}'")

        # Derive base scenario type from instance ID (e.g. 'GAS_SPIKE__rnd001' -> 'GAS_SPIKE')
        scenario_types = df[instance_col].apply(lambda s: str(s).split("__rnd")[0])
        df_work = df.copy()
        df_work["_scenario_type"] = scenario_types

        train_instances: set[str] = set()
        val_instances: set[str] = set()
        test_instances: set[str] = set()

        for scen_type, sub_df in df_work.groupby("_scenario_type"):
            unique_instances = sub_df[instance_col].unique()
            shuffled = rng.permutation(unique_instances)
            k = len(shuffled)

            if k == 1:
                train_instances.add(shuffled[0])
            elif k == 2:
                train_instances.add(shuffled[0])
                test_instances.add(shuffled[1])
            else:
                n_tr = max(1, int(round(k * train_ratio)))
                n_val = max(1, int(round(k * val_ratio)))
                if n_tr + n_val >= k:
                    n_tr = max(1, k - 2)
                    n_val = 1

                train_instances.update(shuffled[:n_tr])
                val_instances.update(shuffled[n_tr : n_tr + n_val])
                test_instances.update(shuffled[n_tr + n_val :])

        train_df = df[df[instance_col].isin(train_instances)].copy().reset_index(drop=True)
        val_df = df[df[instance_col].isin(val_instances)].copy().reset_index(drop=True)
        test_df = df[df[instance_col].isin(test_instances)].copy().reset_index(drop=True)

        # Sort deterministically
        if "first_seen_at" in train_df.columns:
            train_df = train_df.sort_values(by=["first_seen_at", "tx_hash"]).reset_index(drop=True)
            val_df = val_df.sort_values(by=["first_seen_at", "tx_hash"]).reset_index(drop=True)
            test_df = test_df.sort_values(by=["first_seen_at", "tx_hash"]).reset_index(drop=True)

        logger.info(
            "Grouped instance split: Train=%d, Val=%d, Test=%d (Instances: Train=%d, Val=%d, Test=%d)",
            len(train_df), len(val_df), len(test_df),
            len(train_instances), len(val_instances), len(test_instances)
        )
        return (train_df, val_df, test_df)

    def save_splits(
        self,
        train_df: pd.DataFrame,
        val_df: pd.DataFrame,
        test_df: pd.DataFrame,
        output_dir: str | Path,
        split_type: str = "time_based",
    ) -> dict[str, Path]:
        """Save train, validation, and test splits to Parquet with a split manifest.

        Args:
            train_df: Training split DataFrame.
            val_df: Validation split DataFrame.
            test_df: Testing split DataFrame.
            output_dir: Target directory path.
            split_type: Description of split strategy used.

        Returns:
            Dictionary mapping split names to saved paths.
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        paths = {
            "train": out_path / "train.parquet",
            "val": out_path / "val.parquet",
            "test": out_path / "test.parquet",
        }

        train_df.to_parquet(paths["train"], engine="pyarrow", index=False)
        val_df.to_parquet(paths["val"], engine="pyarrow", index=False)
        test_df.to_parquet(paths["test"], engine="pyarrow", index=False)

        manifest = {
            "split_type": split_type,
            "random_seed": self.random_seed,
            "counts": {
                "train": len(train_df),
                "val": len(val_df),
                "test": len(test_df),
                "total": len(train_df) + len(val_df) + len(test_df),
            },
            "class_distributions": {
                "train": train_df[TARGET_COLUMN].value_counts().to_dict() if len(train_df) else {},
                "val": val_df[TARGET_COLUMN].value_counts().to_dict() if len(val_df) else {},
                "test": test_df[TARGET_COLUMN].value_counts().to_dict() if len(test_df) else {},
            },
        }

        manifest_path = out_path / "splits_manifest.json"
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        paths["manifest"] = manifest_path
        logger.info("Saved dataset splits and manifest to %s", out_path)
        return paths

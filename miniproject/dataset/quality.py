"""Data Quality & Anti-Leakage Diagnostics for MempoolGuard.

Enforces dataset integrity, validates feature/label alignment, verifies absence
of post-confirmation fields, and computes class and scenario distributions.
"""

from __future__ import annotations

import logging

import pandas as pd
from pydantic import BaseModel, ConfigDict

from dataset.schema import (
    FEATURE_COLUMNS,
    FORBIDDEN_FEATURE_NAMES,
    TARGET_COLUMN,
    get_feature_matrix,
)

logger = logging.getLogger("mempoolguard.dataset.quality")


class DatasetQualityReport(BaseModel):
    """Structured report detailing dataset quality metrics and anti-leakage status."""

    model_config = ConfigDict(extra="forbid")

    total_rows: int
    duplicate_count: int
    missing_feature_counts: dict[str, int]
    invalid_label_count: int
    timestamp_inconsistencies: int
    leakage_violations: list[str]
    feature_version_mismatches: int
    class_distribution: dict[int, int]
    scenario_distribution: dict[str, int]
    is_valid: bool
    errors: list[str]


class DatasetQualityChecker:
    """Performs rigorous quality diagnostics and leakage checks on datasets."""

    def __init__(self, expected_feature_version: str = "1.0.0") -> None:
        """Initialize DatasetQualityChecker.

        Args:
            expected_feature_version: Expected semantic version of features.
        """
        self.expected_feature_version = expected_feature_version

    def check_duplicates(self, df: pd.DataFrame) -> int:
        """Count duplicate transaction hashes.

        Args:
            df: Dataset DataFrame.

        Returns:
            Number of duplicate transaction observations.
        """
        if "tx_hash" not in df.columns:
            return 0
        return int(df.duplicated(subset=["tx_hash"]).sum())

    def check_missing_features(self, df: pd.DataFrame) -> dict[str, int]:
        """Count missing (NaN/null) values across all canonical feature columns.

        Args:
            df: Dataset DataFrame.

        Returns:
            Dictionary mapping feature column name to null count.
        """
        missing: dict[str, int] = {}
        for col in FEATURE_COLUMNS:
            if col in df.columns:
                null_cnt = int(df[col].isna().sum())
                if null_cnt > 0:
                    missing[col] = null_cnt
            else:
                missing[col] = len(df)
        return missing

    def check_invalid_labels(self, df: pd.DataFrame) -> int:
        """Count invalid or non-binary ground truth labels.

        Args:
            df: Dataset DataFrame.

        Returns:
            Count of rows with labels not in {0, 1}.
        """
        if TARGET_COLUMN not in df.columns:
            return len(df)
        invalid_mask = ~df[TARGET_COLUMN].isin([0, 1])
        return int(invalid_mask.sum())

    def check_timestamp_consistency(self, df: pd.DataFrame) -> int:
        """Verify temporal ordering of timestamp fields.

        Rule: first_seen_at <= feature_timestamp.

        Args:
            df: Dataset DataFrame.

        Returns:
            Count of rows violating timestamp ordering.
        """
        required_ts = ["first_seen_at", "feature_timestamp"]
        if not all(col in df.columns for col in required_ts):
            return len(df)

        try:
            first_seen = pd.to_datetime(df["first_seen_at"], utc=True)
            feature_ts = pd.to_datetime(df["feature_timestamp"], utc=True)
            violations = (first_seen > feature_ts).sum()
            return int(violations)
        except (ValueError, TypeError, pd.errors.OutOfBoundsDatetime) as e:
            logger.warning("Error parsing timestamps for consistency check: %s", e)
            return len(df)

    def check_feature_leakage(self, df: pd.DataFrame) -> list[str]:
        """Verify that forbidden post-confirmation attributes are NOT in the feature matrix.

        Args:
            df: Dataset DataFrame.

        Returns:
            List of detected leakage violation descriptions.
        """
        violations: list[str] = []

        # 1. Check if raw DataFrame contains forbidden block fields
        for col in df.columns:
            if col in FORBIDDEN_FEATURE_NAMES and col not in (
                "label",
                "scenario_id",
                "is_synthetic",
                "tx_hash",
            ):
                violations.append(f"Forbidden column '{col}' found in dataset table")

        # 2. Check if get_feature_matrix extracts any forbidden or non-canonical column
        try:
            feature_matrix = get_feature_matrix(df)
            for col in feature_matrix.columns:
                if col not in FEATURE_COLUMNS:
                    violations.append(
                        f"Non-canonical column '{col}' present in extracted feature matrix"
                    )
                if col in FORBIDDEN_FEATURE_NAMES:
                    violations.append(
                        f"Post-confirmation column '{col}' leaked into model feature matrix"
                    )
        except (KeyError, ValueError, TypeError) as e:
            violations.append(f"Failed to extract clean feature matrix: {e}")

        return violations

    def check_feature_version_consistency(self, df: pd.DataFrame) -> int:
        """Count rows with unexpected feature versions.

        Args:
            df: Dataset DataFrame.

        Returns:
            Count of rows whose feature_version != expected_feature_version.
        """
        if "feature_version" not in df.columns:
            return len(df)
        mismatches = (df["feature_version"] != self.expected_feature_version).sum()
        return int(mismatches)

    def compute_distributions(
        self, df: pd.DataFrame
    ) -> tuple[dict[int, int], dict[str, int]]:
        """Compute exact class and scenario distributions.

        Args:
            df: Dataset DataFrame.

        Returns:
            Tuple of (class_dist: dict[int, int], scenario_dist: dict[str, int]).
        """
        class_dist: dict[int, int] = {}
        if TARGET_COLUMN in df.columns:
            cd = df[TARGET_COLUMN].value_counts().to_dict()
            class_dist = {int(k): int(v) for k, v in cd.items()}

        scenario_dist: dict[str, int] = {}
        if "scenario_id" in df.columns:
            sd = df["scenario_id"].value_counts().to_dict()
            scenario_dist = {str(k): int(v) for k, v in sd.items()}

        return (class_dist, scenario_dist)

    def run_all_checks(self, df: pd.DataFrame) -> DatasetQualityReport:
        """Run all data quality, leakage, and distribution diagnostics.

        Args:
            df: Dataset DataFrame to evaluate.

        Returns:
            Comprehensive DatasetQualityReport.
        """
        errors: list[str] = []
        total_rows = len(df)

        if total_rows == 0:
            return DatasetQualityReport(
                total_rows=0,
                duplicate_count=0,
                missing_feature_counts={},
                invalid_label_count=0,
                timestamp_inconsistencies=0,
                leakage_violations=["Dataset is empty"],
                feature_version_mismatches=0,
                class_distribution={},
                scenario_distribution={},
                is_valid=False,
                errors=["Dataset is empty"],
            )

        # 1. Duplicates
        dup_count = self.check_duplicates(df)
        if dup_count > 0:
            errors.append(f"Found {dup_count} duplicate transaction hashes")

        # 2. Missing values
        missing_feats = self.check_missing_features(df)
        if missing_feats:
            errors.append(f"Missing feature values detected: {missing_feats}")

        # 3. Invalid labels
        invalid_labels = self.check_invalid_labels(df)
        if invalid_labels > 0:
            errors.append(f"Found {invalid_labels} invalid target labels (must be 0 or 1)")

        # 4. Timestamp consistency
        ts_inconsistencies = self.check_timestamp_consistency(df)
        if ts_inconsistencies > 0:
            errors.append(
                f"Found {ts_inconsistencies} timestamp inconsistencies (first_seen > feature_timestamp)"
            )

        # 5. Leakage violations
        leakage = self.check_feature_leakage(df)
        if leakage:
            errors.extend(leakage)

        # 6. Feature version
        version_mismatches = self.check_feature_version_consistency(df)
        if version_mismatches > 0:
            errors.append(
                f"Found {version_mismatches} rows with feature_version != {self.expected_feature_version}"
            )

        # 7. Distributions
        class_dist, scenario_dist = self.compute_distributions(df)

        is_valid = len(errors) == 0

        report = DatasetQualityReport(
            total_rows=total_rows,
            duplicate_count=dup_count,
            missing_feature_counts=missing_feats,
            invalid_label_count=invalid_labels,
            timestamp_inconsistencies=ts_inconsistencies,
            leakage_violations=leakage,
            feature_version_mismatches=version_mismatches,
            class_distribution=class_dist,
            scenario_distribution=scenario_dist,
            is_valid=is_valid,
            errors=errors,
        )

        logger.info(
            "Dataset quality check finished: valid=%s, rows=%d, errors=%d",
            is_valid,
            total_rows,
            len(errors),
        )
        return report

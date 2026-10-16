"""Explicit dataset schema contract and column isolation for MempoolGuard.

Defines mutually disjoint column groups to ensure model features are strictly
separated from post-confirmation labels, timestamps, metadata, and provenance.
"""

from __future__ import annotations

import pandas as pd

from features.engine import FEATURE_NAMES

FEATURE_SCHEMA_VERSION: str = "1.1.0"
LEGACY_FEATURE_SCHEMA_VERSION: str = "1.0.0"

# Column groups
IDENTIFIER_COLUMNS: tuple[str, ...] = ("tx_hash",)

TIMESTAMP_COLUMNS: tuple[str, ...] = (
    "first_seen_at",
    "feature_timestamp",
    "label_determined_at",
)

# 30 canonical numeric features extracted by the feature pipeline
EXTRACTED_FEATURE_COLUMNS: tuple[str, ...] = FEATURE_NAMES
FEATURE_COLUMNS: tuple[str, ...] = FEATURE_NAMES  # Preserved alias for DB/extraction contract

# 29 model input features strictly excluding nonce (Schema v1.1.0)
# nonce is excluded from ML inputs because account-specific sequence numbers
# act as non-stationary identifiers that create spurious shortcut learning.
MODEL_FEATURE_COLUMNS: tuple[str, ...] = tuple(
    col for col in FEATURE_NAMES if col != "nonce"
)

VERSION_COLUMNS: tuple[str, ...] = (
    "feature_version",
    "dataset_version",
)

METADATA_COLUMNS: tuple[str, ...] = (
    "scenario_id",
    "label_provenance",
    "is_synthetic",
)

TARGET_COLUMN: str = "label"

PROVENANCE_COLUMNS: tuple[str, ...] = (
    "manifest_path",
    "random_seed",
)

ALL_COLUMNS: tuple[str, ...] = (
    IDENTIFIER_COLUMNS
    + TIMESTAMP_COLUMNS
    + FEATURE_COLUMNS
    + VERSION_COLUMNS
    + METADATA_COLUMNS
    + (TARGET_COLUMN,)
    + PROVENANCE_COLUMNS
)

# Fields that must NEVER leak into the model feature matrix
FORBIDDEN_FEATURE_NAMES: frozenset[str] = frozenset({
    "block_number",
    "block_hash",
    "transaction_index",
    "cumulative_gas_used",
    "gas_used",
    "status",
    "contract_address",
    "root",
    "logs_bloom",
    "effective_gas_price",
    "receipt",
    "execution_time",
    "confirmed_at",
    "label",
    "target",
    "scenario_id",
    "is_synthetic",
    "tx_hash",
})


def get_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Extract ONLY the 30 canonical numeric features as a pure feature matrix.

    Guarantees:
    1. Returns exactly the 30 canonical features in fixed order.
    2. Contains no targets, timestamps, identifiers, or metadata.
    3. All values are float64.

    Args:
        df: Dataset DataFrame adhering to MempoolGuard schema.

    Returns:
        Pure feature DataFrame ready for ML input.

    Raises:
        KeyError: If any canonical feature is missing.
        ValueError: If forbidden metadata or post-confirmation columns are detected.
    """
    for col in FEATURE_COLUMNS:
        if col not in df.columns:
            raise KeyError(f"Required feature column '{col}' missing from DataFrame")

    feature_df = df[list(FEATURE_COLUMNS)].copy()

    # Verify no accidental contamination
    for col in feature_df.columns:
        if col in FORBIDDEN_FEATURE_NAMES:
            raise ValueError(f"Forbidden column '{col}' detected in feature matrix!")

    return feature_df.astype("float64")


def get_model_feature_matrix(df: pd.DataFrame) -> pd.DataFrame:
    """Extract strictly the 29 model input features (excluding nonce) for ML models.

    Guarantees:
    1. Returns exactly the 29 MODEL_FEATURE_COLUMNS in fixed order.
    2. 'nonce' is strictly excluded to prevent sequence-based shortcut learning.
    3. Contains no targets, timestamps, identifiers, or metadata.
    4. All values are float64.

    Args:
        df: Dataset DataFrame adhering to MempoolGuard schema.

    Returns:
        Pure 29-feature DataFrame ready for ML input.
    """
    for col in MODEL_FEATURE_COLUMNS:
        if col not in df.columns:
            raise KeyError(f"Required model feature column '{col}' missing from DataFrame")

    model_feature_df = df[list(MODEL_FEATURE_COLUMNS)].copy()

    for col in model_feature_df.columns:
        if col in FORBIDDEN_FEATURE_NAMES:
            raise ValueError(f"Forbidden column '{col}' detected in model feature matrix!")

    return model_feature_df.astype("float64")


def get_target_labels(df: pd.DataFrame) -> pd.Series:
    """Extract the target ground-truth label series.

    Args:
        df: Dataset DataFrame.

    Returns:
        Target Series as int64.
    """
    if TARGET_COLUMN not in df.columns:
        raise KeyError(f"Target column '{TARGET_COLUMN}' missing from DataFrame")
    return df[TARGET_COLUMN].astype("int64")


def get_metadata_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Extract identifiers, timestamps, metadata, and provenance columns.

    Args:
        df: Dataset DataFrame.

    Returns:
        DataFrame containing non-feature metadata.
    """
    meta_cols = [
        c
        for c in (
            IDENTIFIER_COLUMNS
            + TIMESTAMP_COLUMNS
            + VERSION_COLUMNS
            + METADATA_COLUMNS
            + PROVENANCE_COLUMNS
        )
        if c in df.columns
    ]
    return df[meta_cols].copy()


def validate_dataset_schema(df: pd.DataFrame) -> tuple[bool, list[str]]:
    """Validate a DataFrame against the MempoolGuard dataset schema contract.

    Args:
        df: DataFrame to validate.

    Returns:
        Tuple of (is_valid: bool, errors: list[str]).
    """
    errors: list[str] = []

    # 1. Check all required columns
    missing_cols = [col for col in ALL_COLUMNS if col not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns ({len(missing_cols)}): {missing_cols}")

    # 2. Check forbidden columns
    for col in df.columns:
        if col in FORBIDDEN_FEATURE_NAMES and col not in (
            "label",
            "scenario_id",
            "is_synthetic",
            "tx_hash",
        ):
            errors.append(f"Forbidden post-confirmation column detected in dataset: {col}")

    # 3. Check label values
    if TARGET_COLUMN in df.columns:
        invalid_labels = set(df[TARGET_COLUMN].dropna().unique()) - {0, 1}
        if invalid_labels:
            errors.append(f"Invalid label values found: {invalid_labels} (must be 0 or 1)")

    # 4. Check feature count
    present_features = [col for col in FEATURE_COLUMNS if col in df.columns]
    if len(present_features) != len(FEATURE_COLUMNS):
        errors.append(
            f"Expected {len(FEATURE_COLUMNS)} features, found {len(present_features)}"
        )

    return (len(errors) == 0, errors)

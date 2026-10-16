"""Dataset creation, anti-leakage validation, and dataset splitting for MempoolGuard."""

from dataset.builder import DatasetBuilder
from dataset.quality import DatasetQualityChecker, DatasetQualityReport
from dataset.schema import (
    ALL_COLUMNS,
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    FORBIDDEN_FEATURE_NAMES,
    IDENTIFIER_COLUMNS,
    LEGACY_FEATURE_SCHEMA_VERSION,
    METADATA_COLUMNS,
    MODEL_FEATURE_COLUMNS,
    PROVENANCE_COLUMNS,
    TARGET_COLUMN,
    TIMESTAMP_COLUMNS,
    VERSION_COLUMNS,
    get_feature_matrix,
    get_model_feature_matrix,
    get_metadata_dataframe,
    get_target_labels,
    validate_dataset_schema,
)
from dataset.splitter import DatasetSplitter

__all__ = [
    "ALL_COLUMNS",
    "FEATURE_COLUMNS",
    "FEATURE_SCHEMA_VERSION",
    "FORBIDDEN_FEATURE_NAMES",
    "IDENTIFIER_COLUMNS",
    "LEGACY_FEATURE_SCHEMA_VERSION",
    "METADATA_COLUMNS",
    "MODEL_FEATURE_COLUMNS",
    "PROVENANCE_COLUMNS",
    "TARGET_COLUMN",
    "TIMESTAMP_COLUMNS",
    "VERSION_COLUMNS",
    "DatasetBuilder",
    "DatasetQualityChecker",
    "DatasetQualityReport",
    "DatasetSplitter",
    "get_feature_matrix",
    "get_model_feature_matrix",
    "get_metadata_dataframe",
    "get_target_labels",
    "validate_dataset_schema",
]

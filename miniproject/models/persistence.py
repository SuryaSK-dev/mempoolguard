"""Model serialization and schema validation subsystem for MempoolGuard.

Persists models with companion metadata and verifies feature version compatibility
upon loading, preventing silent schema drift.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import joblib

from dataset.schema import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    LEGACY_FEATURE_SCHEMA_VERSION,
    MODEL_FEATURE_COLUMNS,
)
from models.baseline import RuleBaseline

logger = logging.getLogger("mempoolguard.models.persistence")


def save_model(
    model: Any,
    output_dir: str | Path,
    file_prefix: str,
    metadata_overrides: dict[str, Any] | None = None,
) -> dict[str, Path]:
    """Save model artifacts and companion metadata JSON.

    Args:
        model: Trained model instance (RuleBaseline, LogisticRegressionModel, or XGBoostModel).
        output_dir: Directory where artifacts should be saved.
        file_prefix: Base filename prefix (e.g. 'logistic_regression', 'xgboost_model').
        metadata_overrides: Optional extra metadata fields (e.g. training metrics).

    Returns:
        Dictionary mapping artifact type to saved Path.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    metadata: dict[str, Any] = {}
    if hasattr(model, "get_metadata"):
        metadata = model.get_metadata()
    if metadata_overrides:
        metadata.update(metadata_overrides)

    paths: dict[str, Path] = {}

    if isinstance(model, RuleBaseline):
        meta_path = out_dir / f"{file_prefix}_metadata.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=2)
        paths["metadata"] = meta_path
        logger.info("Saved RuleBaseline metadata to %s", meta_path)
        return paths

    # Binary model artifact (joblib)
    model_path = out_dir / f"{file_prefix}.joblib"
    joblib.dump(model, model_path)
    paths["model"] = model_path

    # Metadata JSON
    metadata["model_artifact"] = model_path.name
    meta_path = out_dir / f"{file_prefix}_metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    paths["metadata"] = meta_path

    logger.info("Saved %s to %s and metadata to %s", type(model).__name__, model_path, meta_path)
    return paths


def load_model(
    model_path: str | Path,
    expected_feature_version: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Load model artifact and validate feature version and schema integrity.

    Args:
        model_path: Path to the .joblib artifact or metadata JSON.
        expected_feature_version: Optional required feature pipeline version. If None,
            the version is auto-detected from companion metadata and validated against
            the appropriate schema version (v1.0.0 for 30 features, v1.1.0 for 29 features).

    Returns:
        Tuple of (loaded_model, metadata_dict).

    Raises:
        FileNotFoundError: If model or metadata files do not exist.
        ValueError: If feature version or feature names do not match expected schema.
    """
    m_path = Path(model_path)
    if not m_path.exists():
        raise FileNotFoundError(f"Model path does not exist: {m_path}")

    # Determine metadata path
    if m_path.suffix == ".json":
        meta_path = m_path
        with open(meta_path, encoding="utf-8") as f:
            metadata = json.load(f)
        if metadata.get("model_name") == "RuleBasedBaseline":
            model = RuleBaseline(threshold=metadata.get("threshold", 50.0))
            return (model, metadata)
        raise ValueError(f"Direct JSON loading only supported for RuleBaseline, got {meta_path}")

    meta_path = m_path.with_name(f"{m_path.stem}_metadata.json")
    if not meta_path.exists():
        raise FileNotFoundError(f"Companion metadata not found at {meta_path}")

    with open(meta_path, encoding="utf-8") as f:
        metadata = json.load(f)

    # 1. Validate feature version
    feat_version = metadata.get("feature_version", LEGACY_FEATURE_SCHEMA_VERSION)
    if expected_feature_version is not None and feat_version != expected_feature_version:
        raise ValueError(
            f"Feature version mismatch: model requires {feat_version}, expected {expected_feature_version}"
        )

    # 2. Validate feature schema based on version
    saved_features = metadata.get("feature_names", [])
    if feat_version == FEATURE_SCHEMA_VERSION:
        expected_cols = list(MODEL_FEATURE_COLUMNS)
    else:
        expected_cols = list(FEATURE_COLUMNS)

    if saved_features != expected_cols:
        raise ValueError(
            f"Feature names mismatch: model expects {len(saved_features)} features, "
            f"schema version {feat_version} has {len(expected_cols)}"
        )

    model = joblib.load(m_path)
    logger.info("Successfully loaded and validated %s from %s", metadata.get("model_name"), m_path)
    return (model, metadata)

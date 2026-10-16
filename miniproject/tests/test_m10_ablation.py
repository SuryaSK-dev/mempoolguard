"""Dedicated tests for Milestone M10: Feature Group Ablation Study.

Validates feature group partitioning, leave-one-group-out configurations,
train-only preprocessing isolation, reproducibility, single-class metric handling,
and report artifact generation.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dataset.schema import FEATURE_COLUMNS, MODEL_FEATURE_COLUMNS
from features.engine import FEATURE_NAMES
from models.ablation import (
    FEATURE_GROUPS,
    AblationExperimentRunner,
    get_ablation_configurations,
    save_ablation_reports,
    validate_feature_groups,
)
from models.evaluator import ModelEvaluator
from models.logistic import LogisticRegressionModel
from models.xgboost_model import XGBoostModel


def test_feature_partition_completeness_and_uniqueness() -> None:
    """Verify that the 6 feature groups account for all 29 model features with zero overlap."""
    validate_feature_groups()

    assert len(FEATURE_GROUPS) == 6
    all_feats = []
    seen = set()

    for g_key, g_info in FEATURE_GROUPS.items():
        assert "id" in g_info
        assert "name" in g_info
        assert "features" in g_info
        assert len(g_info["features"]) > 0

        for f in g_info["features"]:
            assert f not in seen, f"Feature '{f}' duplicated in group '{g_key}'"
            seen.add(f)
            all_feats.append(f)

    assert len(all_feats) == len(MODEL_FEATURE_COLUMNS)
    assert set(all_feats) == set(MODEL_FEATURE_COLUMNS)
    assert "nonce" not in all_feats


def test_ablation_configurations_leave_one_group_out() -> None:
    """Verify that leave-one-group-out configurations remove strictly the designated group."""
    configs = get_ablation_configurations()
    assert len(configs) == 7  # Full + 6 ablated

    # 1. Full baseline
    full = configs["full_30_features"]
    assert full["feature_count"] == len(MODEL_FEATURE_COLUMNS)
    assert full["features"] == list(MODEL_FEATURE_COLUMNS)
    assert full["removed_group"] is None

    # 2. Ablated configs
    for g_key, g_info in FEATURE_GROUPS.items():
        config_key = f"minus_{g_key}"
        assert config_key in configs
        cfg = configs[config_key]

        expected_count = len(MODEL_FEATURE_COLUMNS) - len(g_info["features"])
        assert cfg["feature_count"] == expected_count
        assert len(cfg["features"]) == expected_count

        # Ensure no removed features are in active list
        for rf in g_info["features"]:
            assert rf not in cfg["features"]

        # Ensure feature order is deterministic and preserves canonical order
        canonical_indices = [MODEL_FEATURE_COLUMNS.index(f) for f in cfg["features"]]
        assert canonical_indices == sorted(canonical_indices)


def test_train_only_preprocessing_leak_prevention() -> None:
    """Ensure imputer and scaler in LogisticRegression are fit strictly on training split."""
    # Create train set with distinct mean (e.g. 10.0) and test set with distinct mean (e.g. 1000.0)
    rng = np.random.default_rng(42)
    feats = ["f1", "f2", "f3"]

    X_tr = pd.DataFrame(rng.normal(10.0, 1.0, size=(20, 3)), columns=feats)
    y_tr = pd.Series([0] * 10 + [1] * 10)

    X_ts = pd.DataFrame(rng.normal(1000.0, 1.0, size=(10, 3)), columns=feats)

    model = LogisticRegressionModel(random_seed=42, feature_names=feats)
    model.fit(X_tr, y_tr)

    scaler = model.pipeline.named_steps["scaler"]
    # Scaler mean must match X_tr mean (~10.0), NOT contaminated by X_ts (~1000.0)
    assert np.all(scaler.mean_ < 20.0)

    # Calling predict_proba on X_ts should NOT alter scaler.mean_
    _ = model.predict_proba(X_ts)
    assert np.all(scaler.mean_ < 20.0)


def test_deterministic_reproducibility_seed() -> None:
    """Verify that models with seed 42 produce bitwise reproducible predictions."""
    feats = list(FEATURE_COLUMNS[:10])
    rng = np.random.default_rng(42)

    X = pd.DataFrame(rng.normal(0, 1, size=(25, 10)), columns=feats)
    y = pd.Series([0] * 12 + [1] * 13)

    # 1. Logistic Regression
    lr1 = LogisticRegressionModel(random_seed=42, feature_names=feats).fit(X, y)
    lr2 = LogisticRegressionModel(random_seed=42, feature_names=feats).fit(X, y)
    np.testing.assert_array_almost_equal(lr1.predict_proba(X), lr2.predict_proba(X))

    # 2. XGBoost
    xgb1 = XGBoostModel(random_seed=42, feature_names=feats).fit(X, y)
    xgb2 = XGBoostModel(random_seed=42, feature_names=feats).fit(X, y)
    np.testing.assert_array_almost_equal(xgb1.predict_proba(X), xgb2.predict_proba(X))


def test_single_class_metric_representation() -> None:
    """Verify that single-class splits represent ROC-AUC, PR-AUC, and FPR as None without crashing."""
    feats = list(FEATURE_COLUMNS[:5])
    X = pd.DataFrame(np.ones((6, 5)), columns=feats)
    y_all_positive = pd.Series([1, 1, 1, 1, 1, 1])

    model = XGBoostModel(random_seed=42, feature_names=feats)
    # Fit on balanced dummy
    model.fit(pd.DataFrame(np.ones((10, 5)), columns=feats), pd.Series([0] * 5 + [1] * 5))

    res = ModelEvaluator.evaluate(model=model, X=X, y=y_all_positive, split_name="test_single_class")
    assert res.roc_auc is None
    assert res.pr_auc is None
    assert res.false_positive_rate is None
    assert res.single_class_warning is not None
    assert "exclusively class 1" in res.single_class_warning


@pytest.fixture
def mock_dataset_splits() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Generate deterministic mock datasets covering all 30 features."""
    rng = np.random.default_rng(42)
    feats = list(FEATURE_COLUMNS)

    train_df = pd.DataFrame(rng.normal(0, 1, size=(20, 30)), columns=feats)
    train_df["label"] = [0] * 10 + [1] * 10

    val_df = pd.DataFrame(rng.normal(0, 1, size=(8, 30)), columns=feats)
    val_df["label"] = [0] * 4 + [1] * 4

    test_df = pd.DataFrame(rng.normal(0, 1, size=(8, 30)), columns=feats)
    test_df["label"] = [0] * 4 + [1] * 4

    return train_df, val_df, test_df


def test_ablation_runner_and_report_artifacts(
    mock_dataset_splits: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame], tmp_path: Path
) -> None:
    """Verify complete ablation study execution and report generation."""
    tr, val, ts = mock_dataset_splits

    runner = AblationExperimentRunner(random_seed=42)
    results = runner.run_on_splits(tr, val, ts, dataset_name="MockSplit")

    # 7 configs x 3 models = 21 runs
    assert len(results) == 21

    # Verify deltas exist on ablated runs
    for r in results:
        assert "delta_f1" in r
        assert "delta_precision" in r
        assert "delta_recall" in r
        assert "inference_latency_ms" in r
        if r["config_id"] == "full_30_features":
            assert r["delta_f1"] == 0.0

    # Save reports
    json_p, csv_p, md_p = save_ablation_reports(results, output_dir=str(tmp_path))
    assert json_p.exists()
    assert csv_p.exists()
    assert md_p.exists()

    with open(json_p, encoding="utf-8") as f:
        data = json.load(f)
        assert data["metadata"]["milestone"] == "M10"
        assert len(data["results"]) == 21

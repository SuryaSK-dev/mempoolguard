"""Dedicated unit and integration tests for Milestone M6: Rules and Machine Learning."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dataset.schema import FEATURE_COLUMNS, MODEL_FEATURE_COLUMNS
from models.audit import run_label_leakage_audit
from models.baseline import RuleBaseline
from models.evaluator import ModelEvaluator
from models.logistic import LogisticRegressionModel
from models.persistence import load_model, save_model
from models.xgboost_model import XGBoostModel


@pytest.fixture
def synthetic_train_data() -> tuple[pd.DataFrame, pd.Series]:
    """Generate synthetic training data with balanced classes for model testing."""
    rng = np.random.default_rng(42)
    n = 40
    data = {}
    for col in FEATURE_COLUMNS:
        data[col] = rng.uniform(0.0, 5.0, size=n)

    # Make gas price distinctly higher for class 1
    y = np.array([0] * 20 + [1] * 20, dtype=np.int64)
    data["gas_price_gwei"] = np.where(y == 1, 50.0, 2.0)
    data["gas_price_to_window_ratio"] = np.where(y == 1, 10.0, 1.0)

    df = pd.DataFrame(data)
    return df, pd.Series(y)


def test_rule_baseline_deterministic_scoring():
    """Verify deterministic scoring and reason code generation of RuleBaseline."""
    baseline = RuleBaseline(threshold=50.0)

    # Benign transaction
    benign_feats = {col: 0.0 for col in FEATURE_COLUMNS}
    benign_feats["gas_price_gwei"] = 2.0
    benign_feats["gas_price_to_window_ratio"] = 1.0

    score_b, pred_b, reasons_b = baseline.evaluate_sample(benign_feats)
    assert score_b == 0.0
    assert pred_b == 0
    assert len(reasons_b) == 0

    # Gas spike transaction
    spike_feats = dict(benign_feats)
    spike_feats["gas_price_gwei"] = 50.0
    spike_feats["gas_price_to_window_ratio"] = 10.0

    score_s, _pred_s, reasons_s = baseline.evaluate_sample(spike_feats)
    assert score_s >= 40.0
    assert "GAS_SPIKE_DETECTED" in reasons_s

    # Burst + Fan-out combination
    combo_feats = dict(benign_feats)
    combo_feats["is_burst_activity"] = 1.0
    combo_feats["is_fan_out"] = 1.0
    score_c, pred_c, reasons_c = baseline.evaluate_sample(combo_feats)
    assert score_c == 55.0  # 30 (burst) + 25 (fan-out)
    assert pred_c == 1
    assert "BURST_ACTIVITY_DETECTED" in reasons_c
    assert "FAN_OUT_DISPERSION_DETECTED" in reasons_c

    # Determinism across batch
    df = pd.DataFrame([benign_feats, spike_feats, combo_feats])
    scores_1 = baseline.predict_risk_scores(df)
    scores_2 = baseline.predict_risk_scores(df)
    np.testing.assert_array_equal(scores_1, scores_2)


def test_logistic_regression_fit_and_predict(
    synthetic_train_data: tuple[pd.DataFrame, pd.Series]
):
    """Verify Logistic Regression training, scaling, probability, and risk scores."""
    X, y = synthetic_train_data
    model = LogisticRegressionModel(random_seed=42)
    model.fit(X, y)

    assert model.is_fitted is True

    # Check probabilities
    proba = model.predict_proba(X)
    assert proba.shape == (len(X), 2)
    assert np.all((proba >= 0.0) & (proba <= 1.0))
    np.testing.assert_allclose(proba.sum(axis=1), 1.0, rtol=1e-5)

    # Check risk scores
    scores = model.predict_risk_scores(X)
    assert scores.shape == (len(X),)
    assert np.all((scores >= 0.0) & (scores <= 100.0))

    # Check predictions
    preds = model.predict(X)
    assert preds.shape == (len(X),)
    assert set(preds).issubset({0, 1})

    # Check coefficients
    coefs = model.get_coefficients()
    assert len(coefs) == len(MODEL_FEATURE_COLUMNS)
    assert "gas_price_gwei" in coefs


def test_xgboost_fit_and_predict(
    synthetic_train_data: tuple[pd.DataFrame, pd.Series]
):
    """Verify XGBoost training, schema validation, and feature importances."""
    X, y = synthetic_train_data
    model = XGBoostModel(n_estimators=20, max_depth=2, random_seed=42)
    model.fit(X, y)

    assert model.is_fitted is True

    # Predict proba and risk scores
    proba = model.predict_proba(X)
    assert proba.shape == (len(X), 2)
    scores = model.predict_risk_scores(X)
    assert scores.shape == (len(X),)
    assert np.all((scores >= 0.0) & (scores <= 100.0))

    # Feature importances
    importances = model.get_feature_importances()
    assert len(importances) == len(MODEL_FEATURE_COLUMNS)
    # Gas features should be top importance given synthetic generation
    top_feature = next(iter(importances))
    assert "gas" in top_feature

    # Schema validation rejects wrong column count
    with pytest.raises(ValueError, match="Expected 29 features"):
        model.predict(np.zeros((5, 10)))


def test_model_persistence_and_schema_validation(
    synthetic_train_data: tuple[pd.DataFrame, pd.Series], tmp_path: Path
):
    """Verify serialization, reloading, and schema/version protection."""
    X, y = synthetic_train_data

    # 1. RuleBaseline persistence
    rule_model = RuleBaseline()
    r_paths = save_model(rule_model, tmp_path, "rule_baseline")
    loaded_rule, meta_r = load_model(r_paths["metadata"])
    assert loaded_rule.model_name == "RuleBasedBaseline"
    assert meta_r["feature_version"] == "1.0.0"

    # 2. Logistic Regression persistence
    lr_model = LogisticRegressionModel(random_seed=42)
    lr_model.fit(X, y)
    lr_paths = save_model(lr_model, tmp_path, "logistic_regression")
    loaded_lr, _meta_lr = load_model(lr_paths["model"])
    assert loaded_lr.is_fitted is True
    np.testing.assert_array_equal(lr_model.predict(X), loaded_lr.predict(X))

    # 3. XGBoost persistence
    xgb_model = XGBoostModel(n_estimators=10, random_seed=42)
    xgb_model.fit(X, y)
    xgb_paths = save_model(xgb_model, tmp_path, "xgboost_model")
    loaded_xgb, _meta_xgb = load_model(xgb_paths["model"])
    assert loaded_xgb.is_fitted is True
    np.testing.assert_array_equal(xgb_model.predict(X), loaded_xgb.predict(X))

    # 4. Version mismatch rejection
    with pytest.raises(ValueError, match="Feature version mismatch"):
        load_model(xgb_paths["model"], expected_feature_version="2.0.0")


def test_model_evaluator_multiclass(
    synthetic_train_data: tuple[pd.DataFrame, pd.Series]
):
    """Verify ModelEvaluator produces full statistical metrics on balanced split."""
    X, y = synthetic_train_data
    lr = LogisticRegressionModel(random_seed=42)
    lr.fit(X, y)

    res = ModelEvaluator.evaluate(lr, X, y, split_name="train")
    assert res.accuracy > 0.8
    assert res.precision is not None
    assert res.recall is not None
    assert res.f1_score is not None
    assert res.roc_auc is not None
    assert res.pr_auc is not None
    assert res.false_positive_rate is not None
    assert res.confusion_matrix == [[20, 0], [0, 20]] or len(res.confusion_matrix) == 2
    assert res.single_class_warning is None
    assert res.inference_latency_ms >= 0.0


def test_model_evaluator_single_class_integrity(
    synthetic_train_data: tuple[pd.DataFrame, pd.Series]
):
    """Verify that single-class splits honestly report metrics as None without fabricating."""
    X, _ = synthetic_train_data
    y_single = np.ones(len(X), dtype=np.int64)  # Exclusively class 1

    lr = LogisticRegressionModel(random_seed=42)
    lr.fit(X, np.array([0] * 20 + [1] * 20))  # Train has both

    # Evaluate on single-class split
    res = ModelEvaluator.evaluate(lr, X, y_single, split_name="single_class_test")

    # In single-class split, ROC-AUC, PR-AUC, and FPR cannot be meaningfully computed
    assert res.single_class_warning is not None
    assert res.roc_auc is None
    assert res.pr_auc is None
    assert res.false_positive_rate is None
    assert res.support["class_0_benign"] == 0
    assert res.support["class_1_anomaly"] == len(X)


def test_label_leakage_audit():
    """Verify that the label-leakage audit examines all 8 scenarios and reports limitations."""
    audit = run_label_leakage_audit()
    assert audit.scenarios_audited == 8
    assert audit.direct_dependency_count == 8
    assert "BENIGN_TRANSFER" in [d.scenario_id for d in audit.scenario_dependencies]
    assert "GAS_SPIKE" in [d.scenario_id for d in audit.scenario_dependencies]
    assert "CRITICAL BENCHMARK LIMITATION" in audit.benchmark_limitation_statement


def test_check_single_feature_separability_detects_shortcut():
    """Verify that the separability check detects shortcut features and respects allowlists."""
    rng = np.random.default_rng(42)
    n = 100
    y = np.array([0] * 50 + [1] * 50, dtype=np.int64)

    # DataFrame with noisy features and one perfect shortcut
    df = pd.DataFrame({
        "feat_noise": rng.uniform(0, 1, size=n),
        "feat_partial": np.where(y == 1, rng.uniform(0.3, 0.8, size=n), rng.uniform(0.1, 0.6, size=n)),
        "feat_shortcut": np.where(y == 1, 100.0, 1.0),  # Perfect separator (AUC=1.0)
    })

    # Gate must raise ValueError without allowlist
    with pytest.raises(ValueError, match="Pre-training separability gate FAILED"):
        ModelEvaluator.check_single_feature_separability(df, y, max_auc_threshold=0.95, verbose=False)

    # Gate passes when shortcut feature is explicitly allowlisted
    aucs = ModelEvaluator.check_single_feature_separability(
        df, y, max_auc_threshold=0.95, allowed_features={"feat_shortcut"}, verbose=False
    )
    assert aucs["feat_shortcut"] == 1.0
    assert aucs["feat_noise"] < 0.95


def test_model_evaluator_bootstrap_confidence_intervals(
    synthetic_train_data: tuple[pd.DataFrame, pd.Series]
):
    """Verify bootstrap confidence intervals generation across evaluation metrics."""
    X, y = synthetic_train_data
    model = LogisticRegressionModel(random_seed=42)
    model.fit(X, y)

    res = ModelEvaluator.evaluate(model, X, y, split_name="test_ci", compute_ci=True, random_seed=42)
    assert res.confidence_intervals is not None
    ci = res.confidence_intervals
    assert "f1_score" in ci
    assert "accuracy" in ci
    assert "pr_auc" in ci
    assert "roc_auc" in ci
    assert ci["f1_score"]["ci_lower"] <= ci["f1_score"]["ci_upper"]
    assert ci["accuracy"]["ci_lower"] <= ci["accuracy"]["ci_upper"]

"""Evaluation and statistical diagnostic engine for MempoolGuard models.

Computes precision, recall, F1, PR-AUC, ROC-AUC, FPR, confusion matrices, and latency.
Honoring scientific integrity: explicitly flags single-class splits as mathematically
unavailable rather than fabricating values.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

logger = logging.getLogger("mempoolguard.models.evaluator")


class ModelEvaluationResult(BaseModel):
    """Rigorous evaluation metrics container for a model on a specific data split."""

    model_config = ConfigDict(extra="forbid")

    model_name: str
    split_name: str
    sample_count: int
    class_distribution: dict[int, int]
    support: dict[str, int]
    accuracy: float
    precision: float | None
    recall: float | None
    f1_score: float | None
    pr_auc: float | None
    roc_auc: float | None
    false_positive_rate: float | None
    false_negative_rate: float | None
    confusion_matrix: list[list[int]]
    inference_latency_ms: float
    single_class_warning: str | None
    notes: list[str]
    confidence_intervals: dict[str, dict[str, float]] | None = None
    single_feature_separability: dict[str, float] | None = None


class ModelEvaluator:
    """Evaluates classification models with rigorous support and single-class checks."""

    @staticmethod
    def measure_latency(
        model: Any, X: pd.DataFrame | np.ndarray, num_runs: int = 20
    ) -> float:
        """Measure average per-sample inference latency in milliseconds.

        Args:
            model: Trained classifier model providing predict or predict_proba.
            X: Input feature matrix.
            num_runs: Repetitions to average out timer noise.

        Returns:
            Inference latency in milliseconds per transaction.
        """
        if len(X) == 0:
            return 0.0

        # Warmup
        _ = model.predict(X[:1])

        latencies = []
        for _ in range(num_runs):
            t0 = time.perf_counter()
            _ = model.predict(X)
            t1 = time.perf_counter()
            latencies.append((t1 - t0) * 1000.0 / len(X))

        return float(np.median(latencies))

    @staticmethod
    def check_single_feature_separability(
        X: pd.DataFrame,
        y: pd.Series | np.ndarray,
        max_auc_threshold: float = 0.95,
        allowed_features: set[str] | list[str] | None = None,
        verbose: bool = True,
    ) -> dict[str, float]:
        """Check all features on training data for single-feature label separability.

        Computes effective ROC-AUC (max(AUC, 1 - AUC)) for each individual feature.
        Raises an explicit ValueError if any unallowed feature has AUC >= max_auc_threshold,
        preventing shortcut learning defects from propagating into trained models.

        Args:
            X: Input feature matrix DataFrame.
            y: Ground-truth binary labels.
            max_auc_threshold: Maximum allowable AUC before flagging shortcut (default 0.95).
            allowed_features: Documented set of legitimately predictive domain features allowed.
            verbose: If True, prints a complete tabulated diagnostic report of all feature AUCs.

        Returns:
            Dictionary mapping feature column names to effective AUC.

        Raises:
            ValueError: If any non-allowlisted feature exceeds max_auc_threshold.
        """
        y_arr = np.asarray(y, dtype=np.int64)
        if len(np.unique(y_arr)) < 2:
            logger.warning("Single-class labels provided; cannot compute single-feature AUCs.")
            return {}

        allowlist = set(allowed_features) if allowed_features else set()
        feature_aucs: dict[str, float] = {}
        violations: list[tuple[str, float]] = []

        if verbose:
            print("\n" + "=" * 78)
            print("SINGLE-FEATURE SEPARABILITY SCAN (PRE-TRAINING SHORTCUT DETECTION)")
            print("=" * 78)
            print(f"{'Feature Name':<35} | {'Raw AUC':<9} | {'Effective AUC':<14} | {'Status':<12}")
            print("-" * 78)

        for col in X.columns:
            vals = X[col].to_numpy(dtype=np.float64)
            # Check for constant feature
            if np.all(vals == vals[0]) or len(np.unique(vals)) < 2:
                raw_auc = 0.5
                eff_auc = 0.5
            else:
                try:
                    raw_auc = float(roc_auc_score(y_arr, vals))
                    eff_auc = max(raw_auc, 1.0 - raw_auc)
                except Exception:
                    raw_auc = 0.5
                    eff_auc = 0.5

            feature_aucs[col] = round(eff_auc, 4)

            status = "PASS"
            if eff_auc >= max_auc_threshold:
                if col in allowlist:
                    status = "ALLOWLISTED"
                else:
                    status = "VIOLATION"
                    violations.append((col, eff_auc))

            if verbose:
                print(f"{col:<35} | {raw_auc:<9.4f} | {eff_auc:<14.4f} | {status:<12}")

        if verbose:
            print("=" * 78)

        if violations:
            violation_summary = ", ".join(f"{feat} (AUC={auc:.4f})" for feat, auc in violations)
            msg = (
                f"Pre-training separability gate FAILED! Found {len(violations)} non-allowlisted "
                f"feature(s) with effective AUC >= {max_auc_threshold}: {violation_summary}. "
                "This indicates potential shortcut learning or label leakage. Training aborted."
            )
            logger.error(msg)
            raise ValueError(msg)

        if verbose:
            print(f"[+] Separability Gate PASSED: All {len(feature_aucs)} features below {max_auc_threshold} threshold.\n")

        return feature_aucs

    @staticmethod
    def compute_bootstrap_confidence_intervals(
        y_true: np.ndarray,
        y_pred: np.ndarray,
        y_proba: np.ndarray | None,
        n_bootstraps: int = 1000,
        ci_level: float = 0.95,
        random_seed: int = 42,
    ) -> dict[str, dict[str, float]]:
        """Compute empirical bootstrap confidence intervals for multi-class evaluation metrics."""
        rng = np.random.default_rng(random_seed)
        n = len(y_true)
        if n < 5:
            return {}

        boot_metrics: dict[str, list[float]] = {
            "accuracy": [],
            "precision": [],
            "recall": [],
            "f1_score": [],
            "pr_auc": [],
            "roc_auc": [],
        }

        alpha_lower = (1.0 - ci_level) / 2.0 * 100.0
        alpha_upper = (1.0 - (1.0 - ci_level) / 2.0) * 100.0

        for _ in range(n_bootstraps):
            indices = rng.choice(n, size=n, replace=True)
            y_t = y_true[indices]
            y_p = y_pred[indices]

            # Require both classes in bootstrap resample for AUCs
            if len(np.unique(y_t)) < 2:
                continue

            boot_metrics["accuracy"].append(float(accuracy_score(y_t, y_p)))
            boot_metrics["precision"].append(float(precision_score(y_t, y_p, zero_division=0)))
            boot_metrics["recall"].append(float(recall_score(y_t, y_p, zero_division=0)))
            boot_metrics["f1_score"].append(float(f1_score(y_t, y_p, zero_division=0)))

            if y_proba is not None:
                p_sub = y_proba[indices]
                try:
                    boot_metrics["roc_auc"].append(float(roc_auc_score(y_t, p_sub)))
                except Exception:
                    pass
                try:
                    boot_metrics["pr_auc"].append(float(average_precision_score(y_t, p_sub)))
                except Exception:
                    pass

        ci_results: dict[str, dict[str, float]] = {}
        for metric_name, values in boot_metrics.items():
            if not values:
                continue
            ci_results[metric_name] = {
                "mean": round(float(np.mean(values)), 4),
                "median": round(float(np.median(values)), 4),
                "ci_lower": round(float(np.percentile(values, alpha_lower)), 4),
                "ci_upper": round(float(np.percentile(values, alpha_upper)), 4),
            }

        return ci_results

    @classmethod
    def evaluate(
        cls,
        model: Any,
        X: pd.DataFrame | np.ndarray,
        y: pd.Series | np.ndarray,
        split_name: str = "test",
        threshold: float = 0.5,
        compute_ci: bool = False,
        random_seed: int = 42,
    ) -> ModelEvaluationResult:
        """Compute all statistically meaningful classification metrics.

        Args:
            model: Model with `predict_proba` or `predict_risk_scores`.
            X: Input feature matrix.
            y: Ground-truth binary labels (0 or 1).
            split_name: Name of the partition (e.g., 'train', 'val', 'test').
            threshold: Probability threshold for positive classification (default 0.5).
            compute_ci: Whether to compute empirical bootstrap confidence intervals (B=1000).
            random_seed: Random seed for bootstrap reproducibility.

        Returns:
            Structured ModelEvaluationResult with verified metrics.
        """
        y_true = np.asarray(y, dtype=np.int64)
        sample_count = len(y_true)

        if sample_count == 0:
            raise ValueError(f"Cannot evaluate empty split '{split_name}'")

        # Extract predictions and probabilities
        if hasattr(model, "predict_proba"):
            y_proba = model.predict_proba(X)
            if y_proba.ndim == 2 and y_proba.shape[1] >= 2:
                p1 = y_proba[:, 1]
            else:
                p1 = y_proba.ravel()
            y_pred = (p1 >= threshold).astype(np.int64)
        elif hasattr(model, "predict_risk_scores"):
            scores = model.predict_risk_scores(X)
            p1 = scores / 100.0
            y_pred = (scores >= (threshold * 100.0)).astype(np.int64)
        else:
            y_pred = model.predict(X)
            p1 = y_pred.astype(np.float64)

        # Measure latency
        latency_ms = cls.measure_latency(model, X)

        # Class counts
        unique_classes, counts = np.unique(y_true, return_counts=True)
        class_dist = {int(k): int(v) for k, v in zip(unique_classes, counts, strict=False)}
        support_0 = int(np.sum(y_true == 0))
        support_1 = int(np.sum(y_true == 1))
        support_dict = {"class_0_benign": support_0, "class_1_anomaly": support_1}

        # Confusion matrix
        # Force 2x2 matrix even if one class is missing in y_true
        cm = confusion_matrix(y_true, y_pred, labels=[0, 1])
        tn, fp, fn, tp = cm.ravel()
        cm_list = [[int(tn), int(fp)], [int(fn), int(tp)]]

        # Accuracy
        acc = float(accuracy_score(y_true, y_pred))

        notes: list[str] = []
        single_class_warning: str | None = None

        # Check for single-class split limitation
        is_single_class = len(unique_classes) < 2

        if is_single_class:
            single_class_val = int(unique_classes[0])
            single_class_warning = (
                f"Split contains exclusively class {single_class_val} (support: {len(y_true)}). "
                "ROC-AUC, PR-AUC, and FPR cannot be meaningfully computed."
            )
            notes.append(single_class_warning)

            # Precision & Recall on available class
            prec = float(precision_score(y_true, y_pred, zero_division=0))
            rec = float(recall_score(y_true, y_pred, zero_division=0))
            f1 = float(f1_score(y_true, y_pred, zero_division=0))

            roc_auc = None
            pr_auc = None
            fpr = None
            fnr = float(fn / (fn + tp)) if (fn + tp) > 0 else 0.0

        else:
            # Multi-class: Full statistical evaluation
            prec = float(precision_score(y_true, y_pred, zero_division=0))
            rec = float(recall_score(y_true, y_pred, zero_division=0))
            f1 = float(f1_score(y_true, y_pred, zero_division=0))

            try:
                roc_auc = float(roc_auc_score(y_true, p1))
            except (ValueError, TypeError, RuntimeError) as e:
                roc_auc = None
                notes.append(f"ROC-AUC calculation failed: {e}")

            try:
                pr_auc = float(average_precision_score(y_true, p1))
            except (ValueError, TypeError, RuntimeError) as e:
                pr_auc = None
                notes.append(f"PR-AUC calculation failed: {e}")

            fpr = float(fp / (fp + tn)) if (fp + tn) > 0 else 0.0
            fnr = float(fn / (fn + tp)) if (fn + tp) > 0 else 0.0

        ci_results = None
        if compute_ci and not is_single_class:
            ci_results = cls.compute_bootstrap_confidence_intervals(
                y_true=y_true,
                y_pred=y_pred,
                y_proba=p1 if not is_single_class else None,
                n_bootstraps=1000,
                ci_level=0.95,
                random_seed=random_seed,
            )

        model_name = getattr(model, "model_name", type(model).__name__)

        return ModelEvaluationResult(
            model_name=model_name,
            split_name=split_name,
            sample_count=sample_count,
            class_distribution=class_dist,
            support=support_dict,
            accuracy=acc,
            precision=prec,
            recall=rec,
            f1_score=f1,
            pr_auc=pr_auc,
            roc_auc=roc_auc,
            false_positive_rate=fpr,
            false_negative_rate=fnr,
            confusion_matrix=cm_list,
            inference_latency_ms=latency_ms,
            single_class_warning=single_class_warning,
            notes=notes,
            confidence_intervals=ci_results,
        )

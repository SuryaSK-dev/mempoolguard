"""XGBoost Gradient Boosting Classifier for MempoolGuard.

Implements tree-based pre-confirmation risk detection with strict feature schema
validation, probabilistic risk scoring (0–100), and feature importance extraction.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from xgboost import XGBClassifier

from dataset.schema import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    MODEL_FEATURE_COLUMNS,
)

logger = logging.getLogger("mempoolguard.models.xgboost_model")


class XGBoostModel:
    """XGBoost classifier with schema validation and 0–100 calibrated risk scoring."""

    def __init__(
        self,
        n_estimators: int = 50,
        max_depth: int = 3,
        learning_rate: float = 0.05,
        random_seed: int = 42,
        model_version: str = "2.0.0",
        feature_names: list[str] | None = None,
    ) -> None:
        """Initialize XGBoostModel.

        Args:
            n_estimators: Number of gradient boosted trees.
            max_depth: Maximum tree depth.
            learning_rate: Boosting learning rate.
            random_seed: Fixed random seed for deterministic boosting.
            model_version: Semantic version of this model artifact.
            feature_names: Optional list of feature names to validate (defaults to canonical 29 model features).
        """
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.learning_rate = learning_rate
        self.random_seed = random_seed
        self.model_version = model_version
        self.model_name = "XGBoostClassifier"
        self.feature_names = (
            list(feature_names) if feature_names is not None else list(MODEL_FEATURE_COLUMNS)
        )
        self.feature_version = (
            FEATURE_SCHEMA_VERSION
            if (feature_names is None or set(self.feature_names) == set(MODEL_FEATURE_COLUMNS))
            else "1.0.0"
        )
        self.threshold = 0.5

        self.classifier = XGBClassifier(
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            random_state=self.random_seed,
            eval_metric="logloss",
            tree_method="hist",
        )
        self.is_fitted = False

    def _validate_input_schema(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Ensure input matches canonical 30-feature schema."""
        if isinstance(X, pd.DataFrame):
            for col in self.feature_names:
                if col not in X.columns:
                    raise KeyError(f"Feature column '{col}' missing from input DataFrame")
            X_clean = X[self.feature_names].values
        else:
            X_arr = np.asarray(X, dtype=np.float64)
            if X_arr.ndim == 1:
                X_arr = X_arr.reshape(1, -1)
            if X_arr.shape[1] != len(self.feature_names):
                raise ValueError(
                    f"Expected {len(self.feature_names)} features, got {X_arr.shape[1]}"
                )
            X_clean = X_arr
        return np.asarray(X_clean, dtype=np.float64)

    def fit(
        self,
        X_train: pd.DataFrame | np.ndarray,
        y_train: pd.Series | np.ndarray,
        X_val: pd.DataFrame | np.ndarray | None = None,
        y_val: pd.Series | np.ndarray | None = None,
    ) -> XGBoostModel:
        """Fit the XGBoost classifier strictly on training data.

        Optionally uses validation data only for early stopping if both classes exist in val.

        Args:
            X_train: Training feature matrix.
            y_train: Training binary labels.
            X_val: Optional validation feature matrix.
            y_val: Optional validation labels.

        Returns:
            Fitted XGBoostModel instance.
        """
        X_tr = self._validate_input_schema(X_train)
        y_tr = np.asarray(y_train, dtype=np.int64)

        if len(np.unique(y_tr)) < 2:
            raise ValueError("Training data must contain both classes (0 and 1) to fit XGBoost.")

        # Compute scale_pos_weight for imbalance
        n_pos = np.sum(y_tr == 1)
        n_neg = np.sum(y_tr == 0)
        scale_pos = (n_neg / n_pos) if n_pos > 0 else 1.0
        self.classifier.set_params(scale_pos_weight=scale_pos)

        eval_set = None
        if X_val is not None and y_val is not None:
            y_v = np.asarray(y_val, dtype=np.int64)
            if len(np.unique(y_v)) >= 2:
                X_v = self._validate_input_schema(X_val)
                eval_set = [(X_tr, y_tr), (X_v, y_v)]

        if eval_set:
            self.classifier.fit(X_tr, y_tr, eval_set=eval_set, verbose=False)
        else:
            self.classifier.fit(X_tr, y_tr, verbose=False)

        self.is_fitted = True
        logger.info(
            "XGBoost successfully fitted on %d samples (%d features)",
            len(X_tr),
            X_tr.shape[1],
        )
        return self

    def predict_proba(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Predict class probabilities [P(y=0), P(y=1)].

        Args:
            X: Input feature matrix.

        Returns:
            Array of shape (N, 2) with predicted probabilities.
        """
        if not self.is_fitted:
            raise RuntimeError("Model must be fitted before predict_proba can be called.")
        X_arr = self._validate_input_schema(X)
        return self.classifier.predict_proba(X_arr)

    def predict_risk_scores(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Predict continuous risk scores on a 0–100 scale.

        Formula: risk_score = P(y=1) * 100.0.

        Args:
            X: Input feature matrix.

        Returns:
            1D array of risk scores in [0.0, 100.0].
        """
        proba = self.predict_proba(X)
        return proba[:, 1] * 100.0

    def predict(
        self, X: pd.DataFrame | np.ndarray, threshold: float | None = None
    ) -> np.ndarray:
        """Predict binary class labels based on decision threshold.

        Args:
            X: Input feature matrix.
            threshold: Optional probability cutoff (defaults to self.threshold).

        Returns:
            1D array of predicted class integers (0 or 1).
        """
        thresh = threshold if threshold is not None else self.threshold
        proba = self.predict_proba(X)
        return (proba[:, 1] >= thresh).astype(np.int64)

    def tune_threshold_on_validation(
        self, X_val: pd.DataFrame | np.ndarray, y_val: pd.Series | np.ndarray
    ) -> float:
        """Select optimal decision threshold on validation set.

        Args:
            X_val: Validation feature matrix.
            y_val: Validation labels.

        Returns:
            Selected probability threshold.
        """
        y_arr = np.asarray(y_val, dtype=np.int64)
        if len(np.unique(y_arr)) < 2:
            logger.warning(
                "Validation split has single class; maintaining default threshold 0.5"
            )
            self.threshold = 0.5
            return 0.5

        proba = self.predict_proba(X_val)[:, 1]
        best_f1 = -1.0
        best_th = 0.5

        for th in np.linspace(0.1, 0.9, 17):
            pred = (proba >= th).astype(np.int64)
            tp = np.sum((pred == 1) & (y_arr == 1))
            fp = np.sum((pred == 1) & (y_arr == 0))
            fn = np.sum((pred == 0) & (y_arr == 1))
            prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) > 0 else 0.0
            if f1 > best_f1:
                best_f1 = f1
                best_th = float(th)

        self.threshold = best_th
        logger.info(
            "Tuned XGBoost threshold on validation: %.2f (Validation F1: %.3f)",
            best_th,
            best_f1,
        )
        return best_th

    def get_feature_importances(self) -> dict[str, float]:
        """Return feature importances as a dictionary sorted descending by importance."""
        if not self.is_fitted:
            raise RuntimeError("Model must be fitted before retrieving feature importances.")
        importances = self.classifier.feature_importances_
        feat_dict = {
            name: float(imp)
            for name, imp in zip(self.feature_names, importances, strict=False)
        }
        return dict(sorted(feat_dict.items(), key=lambda item: item[1], reverse=True))

    def get_metadata(self) -> dict[str, Any]:
        """Return model metadata dictionary."""
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "n_estimators": self.n_estimators,
            "max_depth": self.max_depth,
            "learning_rate": self.learning_rate,
            "random_seed": self.random_seed,
            "threshold": self.threshold,
            "is_fitted": self.is_fitted,
            "feature_version": getattr(self, "feature_version", FEATURE_SCHEMA_VERSION),
            "feature_names": self.feature_names,
        }

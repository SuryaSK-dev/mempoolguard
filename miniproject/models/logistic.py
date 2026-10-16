"""Logistic Regression Pre-Confirmation Risk Model for MempoolGuard.

Encapsulates preprocessing (median imputation and standard scaling fit strictly on train)
with class-weighted regularized logistic regression, producing calibrated risk scores (0–100).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from dataset.schema import (
    FEATURE_COLUMNS,
    FEATURE_SCHEMA_VERSION,
    MODEL_FEATURE_COLUMNS,
)

logger = logging.getLogger("mempoolguard.models.logistic")


class LogisticRegressionModel:
    """Standardized Logistic Regression classifier with strict train-only scaling."""

    def __init__(
        self,
        c_regularization: float = 1.0,
        random_seed: int = 42,
        max_iter: int = 1000,
        model_version: str = "2.0.0",
        feature_names: list[str] | None = None,
    ) -> None:
        """Initialize the LogisticRegressionModel.

        Args:
            c_regularization: Inverse of regularization strength (default 1.0).
            random_seed: Random seed for reproducible solver initialization.
            max_iter: Maximum optimization iterations.
            model_version: Semantic version of this model artifact.
            feature_names: Optional list of feature names to use (defaults to canonical 29 model features).
        """
        self.c_regularization = c_regularization
        self.random_seed = random_seed
        self.max_iter = max_iter
        self.model_version = model_version
        self.model_name = "LogisticRegression"
        self.feature_names = (
            list(feature_names) if feature_names is not None else list(MODEL_FEATURE_COLUMNS)
        )
        self.feature_version = (
            FEATURE_SCHEMA_VERSION
            if (feature_names is None or set(self.feature_names) == set(MODEL_FEATURE_COLUMNS))
            else "1.0.0"
        )
        self.threshold = 0.5  # Default probability threshold

        # Scikit-learn Pipeline: Imputer + Scaler + LogisticRegression
        self.pipeline = Pipeline([
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    C=self.c_regularization,
                    class_weight="balanced",
                    solver="lbfgs",
                    random_state=self.random_seed,
                    max_iter=self.max_iter,
                ),
            ),
        ])
        self.is_fitted = False

    def fit(self, X: pd.DataFrame | np.ndarray, y: pd.Series | np.ndarray) -> LogisticRegressionModel:
        """Fit preprocessing pipeline and logistic regression strictly on training data.

        Args:
            X: Training feature matrix.
            y: Ground-truth binary labels (0 or 1).

        Returns:
            Fitted LogisticRegressionModel instance.
        """
        if isinstance(X, pd.DataFrame):
            X_arr = np.asarray(X[self.feature_names], dtype=np.float64)
        else:
            X_arr = np.asarray(X, dtype=np.float64)
        y_arr = np.asarray(y, dtype=np.int64)

        if len(np.unique(y_arr)) < 2:
            raise ValueError(
                "Training data must contain both classes (0 and 1) to fit Logistic Regression."
            )

        self.pipeline.fit(X_arr, y_arr)
        self.is_fitted = True
        logger.info(
            "Logistic Regression successfully fitted on %d samples (%d features)",
            len(X_arr),
            X_arr.shape[1],
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
        if isinstance(X, pd.DataFrame):
            X_arr = np.asarray(X[self.feature_names], dtype=np.float64)
        else:
            X_arr = np.asarray(X, dtype=np.float64)
        return self.pipeline.predict_proba(X_arr)

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
        """Select optimal decision threshold on validation data.

        If validation data contains both classes, maximizes F1 score.
        If validation contains only one class, preserves default 0.5.

        Args:
            X_val: Validation feature matrix.
            y_val: Validation ground-truth labels.

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
        logger.info("Tuned threshold on validation: %.2f (Validation F1: %.3f)", best_th, best_f1)
        return best_th

    def get_coefficients(self) -> dict[str, float]:
        """Return feature coefficients from the fitted linear model."""
        if not self.is_fitted:
            raise RuntimeError("Model must be fitted before retrieving coefficients.")
        classifier = self.pipeline.named_steps["classifier"]
        coefs = classifier.coef_[0]
        return {
            name: float(coef) for name, coef in zip(self.feature_names, coefs, strict=False)
        }

    def get_metadata(self) -> dict[str, Any]:
        """Return model metadata dictionary."""
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "c_regularization": self.c_regularization,
            "random_seed": self.random_seed,
            "max_iter": self.max_iter,
            "threshold": self.threshold,
            "is_fitted": self.is_fitted,
            "feature_version": getattr(self, "feature_version", FEATURE_SCHEMA_VERSION),
            "feature_names": self.feature_names,
        }

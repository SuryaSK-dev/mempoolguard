"""Deterministic rule-based risk baseline for MempoolGuard.

Evaluates pre-confirmation transactions against explicit heuristic criteria to
produce explainable risk scores (0–100), binary risk classifications, and reason codes.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from dataset.schema import FEATURE_COLUMNS

logger = logging.getLogger("mempoolguard.models.baseline")

# Documented heuristic rules and their assigned severity weights
HEURISTIC_RULES: dict[str, dict[str, Any]] = {
    "RULE_GAS_SPIKE": {
        "description": "Gas price significantly exceeds local window average or absolute threshold (>20 Gwei)",
        "weight": 40.0,
        "reason_code": "GAS_SPIKE_DETECTED",
    },
    "RULE_BURST_ACTIVITY": {
        "description": "Rapid succession of transactions detected in 10-second sliding window (>=3 txs)",
        "weight": 30.0,
        "reason_code": "BURST_ACTIVITY_DETECTED",
    },
    "RULE_FAN_OUT": {
        "description": "Rapid dispersion to multiple unique destination addresses within 60s (>=3 addresses)",
        "weight": 25.0,
        "reason_code": "FAN_OUT_DISPERSION_DETECTED",
    },
    "RULE_REPEATED_TRANSFER": {
        "description": "Repeated high-frequency value transfers between identical accounts (>=3 transfers)",
        "weight": 25.0,
        "reason_code": "REPEATED_TRANSFER_DETECTED",
    },
    "RULE_CONTRACT_CALL": {
        "description": "Transaction includes non-empty calldata invoking a smart contract",
        "weight": 20.0,
        "reason_code": "CONTRACT_CALL_INVOCATION",
    },
    "RULE_GAS_DEVIATION_LOW": {
        "description": "Gas price below standard network baseline (<1.0 Gwei)",
        "weight": 20.0,
        "reason_code": "GAS_PRICE_ANOMALY_LOW",
    },
}


class RuleBaseline:
    """Deterministic rule-based risk scoring baseline."""

    def __init__(self, threshold: float = 50.0, model_version: str = "1.0.0") -> None:
        """Initialize the RuleBaseline.

        Args:
            threshold: Risk score cutoff (0–100) for binary classification.
            model_version: Version identifier.
        """
        self.threshold = threshold
        self.model_version = model_version
        self.model_name = "RuleBasedBaseline"
        self.rules = HEURISTIC_RULES

    def evaluate_sample(self, features: dict[str, float]) -> tuple[float, int, list[str]]:
        """Evaluate a single feature dictionary against deterministic rules.

        Args:
            features: Dictionary mapping canonical feature names to float values.

        Returns:
            Tuple of (risk_score: float, predicted_class: int, triggered_reasons: list[str]).
        """
        score = 0.0
        reasons: list[str] = []

        # 1. Gas spike rule
        gas_ratio = float(features.get("gas_price_to_window_ratio", 1.0))
        gas_price = float(features.get("gas_price_gwei", 2.0))
        if gas_ratio > 3.0 or gas_price > 20.0:
            score += self.rules["RULE_GAS_SPIKE"]["weight"]
            reasons.append(self.rules["RULE_GAS_SPIKE"]["reason_code"])

        # 2. Burst activity rule
        is_burst = float(features.get("is_burst_activity", 0.0))
        burst_cnt = float(features.get("window_tx_count_10s", 0.0))
        if is_burst >= 1.0 or burst_cnt >= 3.0:
            score += self.rules["RULE_BURST_ACTIVITY"]["weight"]
            reasons.append(self.rules["RULE_BURST_ACTIVITY"]["reason_code"])

        # 3. Fan-out dispersion rule
        is_fan_out = float(features.get("is_fan_out", 0.0))
        unique_recvs = float(features.get("window_unique_receivers_60s", 0.0))
        if is_fan_out >= 1.0 or unique_recvs >= 3.0:
            score += self.rules["RULE_FAN_OUT"]["weight"]
            reasons.append(self.rules["RULE_FAN_OUT"]["reason_code"])

        # 4. Repeated transfer rule
        is_repeated = float(features.get("is_repeated_transfer", 0.0))
        pair_cnt = float(features.get("sender_receiver_tx_count", 0.0))
        if is_repeated >= 1.0 or pair_cnt >= 3.0:
            score += self.rules["RULE_REPEATED_TRANSFER"]["weight"]
            reasons.append(self.rules["RULE_REPEATED_TRANSFER"]["reason_code"])

        # 5. Contract call rule
        is_call = float(features.get("is_contract_call", 0.0))
        calldata_size = float(features.get("calldata_size_bytes", 0.0))
        if is_call >= 1.0 and calldata_size > 0.0:
            score += self.rules["RULE_CONTRACT_CALL"]["weight"]
            reasons.append(self.rules["RULE_CONTRACT_CALL"]["reason_code"])

        # 6. Gas deviation low rule
        if 0.0 <= gas_price < 1.0:
            score += self.rules["RULE_GAS_DEVIATION_LOW"]["weight"]
            reasons.append(self.rules["RULE_GAS_DEVIATION_LOW"]["reason_code"])

        clamped_score = min(max(score, 0.0), 100.0)
        predicted_class = 1 if clamped_score >= self.threshold else 0
        return (clamped_score, predicted_class, reasons)

    def predict_risk_scores(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Compute continuous risk scores (0–100) for a batch of samples.

        Args:
            X: DataFrame with FEATURE_COLUMNS or 2D NumPy array.

        Returns:
            1D NumPy array of risk scores in [0.0, 100.0].
        """
        if isinstance(X, np.ndarray):
            df_x = pd.DataFrame(X, columns=list(FEATURE_COLUMNS))
        else:
            df_x = X

        scores = np.zeros(len(df_x), dtype=np.float64)
        for i in range(len(df_x)):
            row_dict = df_x.iloc[i].to_dict()
            score, _, _ = self.evaluate_sample(row_dict)
            scores[i] = score

        return scores

    def predict_proba(self, X: pd.DataFrame | np.ndarray) -> np.ndarray:
        """Compute pseudo-probabilities [P(y=0), P(y=1)] from risk scores.

        Args:
            X: Input feature matrix.

        Returns:
            2D array of shape (N, 2) where column 1 is risk_score / 100.0.
        """
        scores = self.predict_risk_scores(X)
        p1 = scores / 100.0
        p0 = 1.0 - p1
        return np.column_stack([p0, p1])

    def predict(
        self, X: pd.DataFrame | np.ndarray, threshold: float | None = None
    ) -> np.ndarray:
        """Predict binary class labels (0 or 1).

        Args:
            X: Input feature matrix.
            threshold: Optional override for risk cutoff (defaults to self.threshold).

        Returns:
            1D integer array of class predictions.
        """
        thresh = threshold if threshold is not None else self.threshold
        scores = self.predict_risk_scores(X)
        return (scores >= thresh).astype(np.int64)

    def get_metadata(self) -> dict[str, Any]:
        """Return model metadata dictionary."""
        return {
            "model_name": self.model_name,
            "model_version": self.model_version,
            "threshold": self.threshold,
            "rules": self.rules,
            "feature_version": "1.0.0",
            "feature_count": len(FEATURE_COLUMNS),
        }

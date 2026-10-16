"""Policy configuration, boundary semantics, and truthful reason-code derivation for MempoolGuard.

Defines deterministic threshold-based decision mapping (ALLOW, FLAG, HOLD) and evidence-backed
reason codes supporting application-level simulated risk interventions.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from core.config import RiskConfig
from core.contracts import DecisionType

logger = logging.getLogger("mempoolguard.decision.policy")

# Standardized Truthful Reason Codes
REASON_LOW_RISK_SCORE = "LOW_RISK_SCORE"
REASON_MEDIUM_RISK_SCORE = "MEDIUM_RISK_SCORE"
REASON_HIGH_RISK_SCORE = "HIGH_RISK_SCORE"
REASON_GAS_SPIKE = "GAS_SPIKE_DETECTED"
REASON_BURST_ACTIVITY = "BURST_ACTIVITY_DETECTED"
REASON_FAN_OUT = "FAN_OUT_DETECTED"
REASON_REPEATED_TRANSFER = "REPEATED_TRANSFER_DETECTED"
REASON_REPEATED_INTERACTION = "REPEATED_INTERACTION_DETECTED"
REASON_CONTRACT_CALL = "CONTRACT_CALL_INVOCATION"
REASON_GAS_DEVIATION_LOW = "GAS_PRICE_ANOMALY_LOW"
REASON_MODEL_FAILURE = "MODEL_FAILURE"
REASON_FALLBACK_POLICY = "FALLBACK_POLICY"


class PolicyEngine:
    """Evaluates risk scores against configurable thresholds and derives evidence-backed reason codes."""

    def __init__(
        self,
        threshold_allow: float = 30.0,
        threshold_flag: float = 70.0,
        default_fallback_decision: str | DecisionType = DecisionType.FLAG,
        policy_version: str = "1.0.0",
        hold_duration_seconds: float = 30.0,
    ) -> None:
        """Initialize the PolicyEngine with specific threshold boundaries.

        Args:
            threshold_allow: Upper inclusive bound for ALLOW decision (e.g., 30.0).
            threshold_flag: Threshold at or above which decision is HOLD (e.g., 70.0).
            default_fallback_decision: Safe default action if model or features fail.
            policy_version: Semantic policy version string.
            hold_duration_seconds: Simulated intervention duration for temporary HOLD.
        """
        if threshold_allow >= threshold_flag:
            raise ValueError(
                f"threshold_allow ({threshold_allow}) must be strictly less than "
                f"threshold_flag ({threshold_flag})"
            )
        if not (0.0 <= threshold_allow <= 100.0) or not (0.0 <= threshold_flag <= 100.0):
            raise ValueError("Thresholds must be bounded in [0.0, 100.0]")

        self.threshold_allow = float(threshold_allow)
        self.threshold_flag = float(threshold_flag)
        self.default_fallback_decision = (
            DecisionType(default_fallback_decision)
            if isinstance(default_fallback_decision, str)
            else default_fallback_decision
        )
        self.policy_version = policy_version
        self.hold_duration_seconds = float(hold_duration_seconds)

    @classmethod
    def from_config(cls, risk_config: RiskConfig) -> PolicyEngine:
        """Create a PolicyEngine directly from application RiskConfig."""
        return cls(
            threshold_allow=risk_config.threshold_allow,
            threshold_flag=risk_config.threshold_flag,
            default_fallback_decision=risk_config.default_fallback_decision,
            policy_version=risk_config.policy_version,
            hold_duration_seconds=risk_config.hold_duration_seconds,
        )

    def evaluate_score(self, risk_score: float) -> DecisionType:
        """Map a numeric risk score in [0.0, 100.0] to a deterministic simulated DecisionType.

        Exact Boundary Semantics:
        - risk_score <= threshold_allow             -> ALLOW
        - threshold_allow < risk_score < threshold_flag -> FLAG
        - risk_score >= threshold_flag            -> HOLD

        Args:
            risk_score: Numeric score in [0.0, 100.0].

        Returns:
            DecisionType.ALLOW, DecisionType.FLAG, or DecisionType.HOLD.

        Raises:
            ValueError: If risk_score is NaN, Inf, or outside [0.0, 100.0].
        """
        if risk_score is None or math.isnan(risk_score) or math.isinf(risk_score):
            raise ValueError(f"Invalid risk score: {risk_score}. Must be a finite number.")

        if not (0.0 <= risk_score <= 100.0):
            raise ValueError(
                f"Risk score {risk_score} out of bounds: must be between 0.0 and 100.0."
            )

        if risk_score <= self.threshold_allow:
            return DecisionType.ALLOW
        if risk_score < self.threshold_flag:
            return DecisionType.FLAG
        return DecisionType.HOLD

    def derive_reason_codes(
        self,
        risk_score: float | None,
        decision: DecisionType,
        features: dict[str, Any] | None = None,
        model_reasons: list[str] | None = None,
        is_fallback: bool = False,
    ) -> list[str]:
        """Derive truthful, evidence-backed reason codes for a decision.

        Guarantees:
        1. No reason codes are generated unless supported by concrete input evidence.
        2. Primary score category (LOW_RISK_SCORE, MEDIUM_RISK_SCORE, HIGH_RISK_SCORE)
           is explicitly aligned with the rendered decision.
        3. Model failure and fallback invocations are prominently tagged.
        4. Output ordering is deterministic.

        Args:
            risk_score: Estimated risk score (None if model failure).
            decision: Rendered decision type.
            features: Optional dictionary of pre-confirmation transaction features.
            model_reasons: Optional reason codes already emitted by model (e.g. RuleBaseline).
            is_fallback: True if decision was triggered by error/fallback policy.

        Returns:
            List of unique, verified reason code strings.
        """
        reasons: list[str] = []

        if is_fallback:
            reasons.extend([REASON_MODEL_FAILURE, REASON_FALLBACK_POLICY])

        # Primary score category
        if decision == DecisionType.ALLOW:
            reasons.append(REASON_LOW_RISK_SCORE)
        elif decision == DecisionType.FLAG:
            reasons.append(REASON_MEDIUM_RISK_SCORE)
        elif decision == DecisionType.HOLD:
            reasons.append(REASON_HIGH_RISK_SCORE)

        # Behavioral evidence checks from extracted feature vector
        if features:
            # Gas spike: gas price ratio >= 3.0 or absolute >= 20.0 Gwei
            gas_ratio = float(features.get("gas_price_to_window_ratio", 1.0))
            gas_gwei = float(features.get("gas_price_gwei", 1.0))
            if gas_ratio >= 3.0 or gas_gwei >= 20.0:
                reasons.append(REASON_GAS_SPIKE)

            # Burst activity: burst flag or >= 3 txs in 10s
            is_burst = float(features.get("is_burst_activity", 0.0))
            w10_cnt = float(features.get("window_tx_count_10s", 0.0))
            if is_burst >= 1.0 or w10_cnt >= 3.0:
                reasons.append(REASON_BURST_ACTIVITY)

            # Fan-out: dispersion flag or >= 3 unique receivers in 60s
            is_fan = float(features.get("is_fan_out", 0.0))
            u_recv = float(features.get("window_unique_receivers_60s", 0.0))
            if is_fan >= 1.0 or u_recv >= 3.0:
                reasons.append(REASON_FAN_OUT)

            # Repeated transfer: flag or >= 3 transfers to same receiver
            is_rep = float(features.get("is_repeated_transfer", 0.0))
            pair_cnt = float(features.get("sender_receiver_tx_count", 0.0))
            if is_rep >= 1.0 or pair_cnt >= 3.0:
                reasons.append(REASON_REPEATED_TRANSFER)

            # Repeated interaction: established relationship with multiple interactions
            existing_rel = float(features.get("sender_receiver_existing_relationship", 0.0))
            if existing_rel >= 1.0 and pair_cnt >= 2.0 and REASON_REPEATED_TRANSFER not in reasons:
                reasons.append(REASON_REPEATED_INTERACTION)

            # Contract call invocation: non-empty calldata
            is_contract = float(features.get("is_contract_call", 0.0))
            calldata_bytes = float(features.get("calldata_size_bytes", 0.0))
            if is_contract >= 1.0 and calldata_bytes > 0.0:
                reasons.append(REASON_CONTRACT_CALL)

            # Sub-base or zero priority fee
            if 0.0 <= gas_gwei < 1.0:
                reasons.append(REASON_GAS_DEVIATION_LOW)

        # Incorporate verified upstream model reasons
        if model_reasons:
            for mr in model_reasons:
                if mr not in reasons:
                    reasons.append(mr)

        # Deduplicate while preserving order of primary categories
        seen = set()
        deduped: list[str] = []
        for r in reasons:
            if r not in seen:
                seen.add(r)
                deduped.append(r)

        return deduped

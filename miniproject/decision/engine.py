"""Decision Engine for MempoolGuard.

Converts pre-confirmation model risk predictions into application-level simulated
decisions (ALLOW, FLAG, HOLD), supports safe model failure fallbacks, enforces
idempotency, and persists full audit records.
"""

from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from core.config import RiskConfig, load_config
from core.contracts import Decision, DecisionType, FeatureVector, RiskResult, utc_now
from core.database import Database
from decision.audit import AuditLogger
from decision.policy import (
    PolicyEngine,
)

logger = logging.getLogger("mempoolguard.decision.engine")


class DecisionEngine:
    """Core decision engine evaluating risk scores and dispatching simulated intervention policies."""

    def __init__(
        self,
        config: RiskConfig | None = None,
        db: Database | None = None,
        policy: PolicyEngine | None = None,
    ) -> None:
        """Initialize DecisionEngine.

        Args:
            config: Optional RiskConfig. Loaded from global config if omitted.
            db: Optional Database instance for persistence and idempotency tracking.
            policy: Optional pre-configured PolicyEngine.
        """
        if config is None:
            full_cfg = load_config()
            config = full_cfg.risk

        self.config = config
        self.policy = policy or PolicyEngine.from_config(self.config)
        self.db = db
        self.audit_logger = AuditLogger(db) if db is not None else None

    def _ensure_raw_tx(self, tx_hash: str, timestamp: Any) -> None:
        """Ensure parent raw_transactions record exists for foreign key constraint."""
        if self.db is not None and not self.db.get_raw_transaction(tx_hash):
            from datetime import datetime
            dt = timestamp if isinstance(timestamp, datetime) else utc_now()
            self.db.insert_raw_transaction(tx_hash, "{}", first_seen_at=dt)

    def decide_score(
        self,
        tx_hash: str,
        risk_score: float,
        features: dict[str, Any] | None = None,
        model_name: str | None = None,
        model_version: str | None = None,
        model_reasons: list[str] | None = None,
        idempotency: bool = True,
    ) -> tuple[Decision, Any]:
        """Render a decision from a numeric risk score and persist audit record.

        Args:
            tx_hash: 66-character lowercase transaction hash.
            risk_score: Estimated risk score in [0.0, 100.0].
            features: Optional pre-confirmation feature dictionary.
            model_name: Identifier of the predictive model.
            model_version: Semantic version of the model.
            model_reasons: Reason codes emitted directly by model/baseline.
            idempotency: If True, returns existing decision if already computed with same policy.

        Returns:
            Tuple of (Decision, AuditRecord | None).
        """
        clean_tx = tx_hash.strip().lower()

        # 1. Idempotency Check
        if idempotency and self.db is not None:
            existing = self.db.get_latest_decision(clean_tx)
            if existing and existing.policy_version == self.policy.policy_version:
                # Check if decision type matches what this score would produce
                try:
                    expected_dec = self.policy.evaluate_score(risk_score)
                    if existing.decision == expected_dec:
                        logger.info(
                            "Idempotent resolution for tx %s: returning existing %s decision",
                            clean_tx[:10],
                            existing.decision.value,
                        )
                        audits = self.db.get_audit_records_by_tx(clean_tx)
                        latest_audit = audits[-1] if audits else None
                        return existing, latest_audit
                except ValueError:
                    pass

        # 2. Score Validation & Safe Failure Handling
        is_invalid_score = (
            risk_score is None
            or not isinstance(risk_score, (int, float))
            or math.isnan(risk_score)
            or math.isinf(risk_score)
            or not (0.0 <= risk_score <= 100.0)
        )

        now = utc_now()

        if is_invalid_score:
            decision_type = self.policy.default_fallback_decision
            reasons = self.policy.derive_reason_codes(
                risk_score=None,
                decision=decision_type,
                features=features,
                is_fallback=True,
            )
            error_msg = f"Invalid risk score: {risk_score}; applied safe fallback policy {decision_type.value}"
            logger.warning(
                "Tx %s: %s",
                clean_tx[:10],
                error_msg,
            )

            expires_at = (
                now + timedelta(seconds=self.policy.hold_duration_seconds)
                if decision_type == DecisionType.HOLD
                else None
            )

            decision = Decision(
                tx_hash=clean_tx,
                decision=decision_type,
                reason_codes=reasons,
                policy_version=self.policy.policy_version,
                created_at=now,
                expires_at=expires_at,
                is_simulated=True,
            )

            audit_rec = None
            if self.db is not None and self.audit_logger is not None:
                self._ensure_raw_tx(clean_tx, now)
                self.db.insert_decision(decision)
                audit_rec = self.audit_logger.log_decision_event(
                    tx_hash=clean_tx,
                    decision=decision_type,
                    risk_score=None,
                    reason_codes=reasons,
                    policy_version=self.policy.policy_version,
                    model_name=model_name,
                    model_version=model_version,
                    error_info=error_msg,
                    extra_details={"is_fallback": True, "raw_score": str(risk_score)},
                )
            return decision, audit_rec

        # 3. Normal Deterministic Decision Evaluation
        decision_type = self.policy.evaluate_score(risk_score)
        reasons = self.policy.derive_reason_codes(
            risk_score=risk_score,
            decision=decision_type,
            features=features,
            model_reasons=model_reasons,
            is_fallback=False,
        )

        expires_at = (
            now + timedelta(seconds=self.policy.hold_duration_seconds)
            if decision_type == DecisionType.HOLD
            else None
        )

        decision = Decision(
            tx_hash=clean_tx,
            decision=decision_type,
            reason_codes=reasons,
            policy_version=self.policy.policy_version,
            created_at=now,
            expires_at=expires_at,
            is_simulated=True,
        )

        audit_rec = None
        if self.db is not None and self.audit_logger is not None:
            self._ensure_raw_tx(clean_tx, now)
            self.db.insert_decision(decision)
            audit_rec = self.audit_logger.log_decision_event(
                tx_hash=clean_tx,
                decision=decision_type,
                risk_score=risk_score,
                reason_codes=reasons,
                policy_version=self.policy.policy_version,
                model_name=model_name,
                model_version=model_version,
                extra_details={
                    "threshold_allow": self.policy.threshold_allow,
                    "threshold_flag": self.policy.threshold_flag,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
            )

        return decision, audit_rec

    def decide_risk_result(
        self,
        risk_result: RiskResult,
        features: dict[str, Any] | None = None,
        idempotency: bool = True,
    ) -> tuple[Decision, Any]:
        """Convenience method to evaluate a standardized RiskResult from M6."""
        return self.decide_score(
            tx_hash=risk_result.tx_hash,
            risk_score=risk_result.risk_score,
            features=features,
            model_name=risk_result.model_name,
            model_version=risk_result.model_version,
            model_reasons=risk_result.reason_codes,
            idempotency=idempotency,
        )

    def decide_with_model(
        self,
        model: Any,
        tx_hash: str,
        features: FeatureVector | dict[str, Any] | pd.DataFrame | np.ndarray,
        idempotency: bool = True,
    ) -> tuple[Decision, Any]:
        """Execute model inference and evaluate decision with comprehensive error containment.

        Guarantees:
        1. If model raises any exception (OOM, missing column, bad format), pipeline does NOT crash.
        2. Configured safe fallback decision is rendered.
        3. Audit record captures the exact exception message and traceback context.
        4. MODEL_FAILURE and FALLBACK_POLICY reason codes are attached.

        Args:
            model: Trained predictive model (RuleBaseline, LogisticRegressionModel, or XGBoostModel).
            tx_hash: Associated transaction hash.
            features: FeatureVector contract, feature dict, or DataFrame/array.
            idempotency: Flag controlling duplicate suppression.

        Returns:
            Tuple of (Decision, AuditRecord | None).
        """
        clean_tx = tx_hash.strip().lower()
        model_name = getattr(model, "model_name", type(model).__name__)
        model_version = getattr(model, "model_version", "1.0.0")

        # Extract dictionary context for reason code generation
        feat_dict: dict[str, Any] = {}
        if isinstance(features, FeatureVector):
            feat_dict = features.features
            X = pd.DataFrame([feat_dict])
        elif isinstance(features, dict):
            feat_dict = features
            X = pd.DataFrame([feat_dict])
        elif isinstance(features, pd.DataFrame):
            X = features
            if len(X) > 0:
                feat_dict = X.iloc[0].to_dict()
        else:
            X = np.asarray(features)

        # Attempt inference with safe failure handling
        try:
            model_reasons: list[str] = []
            if hasattr(model, "evaluate_sample") and isinstance(feat_dict, dict) and feat_dict:
                score, _, model_reasons = model.evaluate_sample(feat_dict)
                risk_score = float(score)
            elif hasattr(model, "predict_risk_scores"):
                scores = model.predict_risk_scores(X)
                risk_score = float(scores[0])
            elif hasattr(model, "predict_proba"):
                proba = model.predict_proba(X)
                risk_score = float(proba[0, 1] * 100.0)
            elif hasattr(model, "predict"):
                preds = model.predict(X)
                risk_score = float(preds[0] * 100.0)
            else:
                raise TypeError(f"Provided model {type(model)} does not provide prediction methods")

            return self.decide_score(
                tx_hash=clean_tx,
                risk_score=risk_score,
                features=feat_dict,
                model_name=model_name,
                model_version=model_version,
                model_reasons=model_reasons,
                idempotency=idempotency,
            )

        except Exception as exc:
            logger.exception(
                "Model inference failure for tx %s using %s",
                clean_tx[:10],
                model_name,
            )
            # Safe Fallback
            now = utc_now()
            fallback_type = self.policy.default_fallback_decision
            reasons = self.policy.derive_reason_codes(
                risk_score=None,
                decision=fallback_type,
                features=feat_dict,
                is_fallback=True,
            )
            error_details = f"Model failure in {model_name}: {type(exc).__name__}: {exc}"

            expires_at = (
                now + timedelta(seconds=self.policy.hold_duration_seconds)
                if fallback_type == DecisionType.HOLD
                else None
            )

            decision = Decision(
                tx_hash=clean_tx,
                decision=fallback_type,
                reason_codes=reasons,
                policy_version=self.policy.policy_version,
                created_at=now,
                expires_at=expires_at,
                is_simulated=True,
            )

            audit_rec = None
            if self.db is not None and self.audit_logger is not None:
                self._ensure_raw_tx(clean_tx, now)
                self.db.insert_decision(decision)
                audit_rec = self.audit_logger.log_decision_event(
                    tx_hash=clean_tx,
                    decision=fallback_type,
                    risk_score=None,
                    reason_codes=reasons,
                    policy_version=self.policy.policy_version,
                    model_name=model_name,
                    model_version=model_version,
                    error_info=error_details,
                    extra_details={
                        "is_fallback": True,
                        "exception_class": type(exc).__name__,
                        "exception_message": str(exc),
                    },
                )

            return decision, audit_rec

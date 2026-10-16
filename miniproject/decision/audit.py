"""Audit logging subsystem for MempoolGuard.

Persists immutable, structured audit trail records to the relational database, recording
risk evaluations, simulated intervention decisions, policy parameters, and safe fallback events.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from core.contracts import AuditEventType, AuditRecord, DecisionType, utc_now
from core.database import Database

logger = logging.getLogger("mempoolguard.decision.audit")


class AuditLogger:
    """Manages immutable audit logging for risk predictions and intervention decisions."""

    def __init__(self, db: Database) -> None:
        """Initialize AuditLogger with connected SQLite database.

        Args:
            db: Connected MempoolGuard SQLite database instance.
        """
        self.db = db

    def log_decision_event(
        self,
        tx_hash: str,
        decision: DecisionType,
        risk_score: float | None,
        reason_codes: list[str],
        policy_version: str,
        model_name: str | None = None,
        model_version: str | None = None,
        error_info: str | None = None,
        extra_details: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Construct and persist an audit record for an application intervention decision.

        Args:
            tx_hash: Canonical 66-character lowercase transaction hash.
            decision: Rendered DecisionType (ALLOW, FLAG, or HOLD).
            risk_score: Estimated numeric risk score, or None if fallback.
            reason_codes: Standardized list of evidence-backed reason codes.
            policy_version: Version identifier of the evaluated policy.
            model_name: Optional name of the predictive model used.
            model_version: Optional version of the predictive model artifact.
            error_info: Optional error or exception message if fallback occurred.
            extra_details: Optional extra structured context for compliance tracing.

        Returns:
            Saved immutable AuditRecord instance.
        """
        details: dict[str, Any] = {
            "policy_version": policy_version,
            "reason_codes": reason_codes,
            "is_simulated": True,
        }
        if model_name:
            details["model_name"] = model_name
        if model_version:
            details["model_version"] = model_version
        if extra_details:
            details.update(extra_details)

        event_type = (
            AuditEventType.PIPELINE_ERROR if error_info else AuditEventType.DECISION_ISSUED
        )

        record = AuditRecord(
            audit_id=str(uuid.uuid4()),
            tx_hash=tx_hash,
            event_type=event_type,
            timestamp=utc_now(),
            risk_score=risk_score,
            decision=decision,
            details=details,
            error_info=error_info,
        )

        self.db.insert_audit_record(record)
        logger.info(
            "Logged audit record %s for tx %s: %s (risk: %s)",
            record.audit_id[:8],
            tx_hash[:10],
            decision.value,
            f"{risk_score:.2f}" if risk_score is not None else "N/A",
        )
        return record

    def log_error_event(
        self,
        tx_hash: str,
        error_message: str,
        details: dict[str, Any] | None = None,
    ) -> AuditRecord:
        """Log a pipeline execution error or model failure event.

        Args:
            tx_hash: Associated transaction hash.
            error_message: Detailed error string or traceback snippet.
            details: Optional contextual metadata.

        Returns:
            Saved AuditRecord instance.
        """
        record = AuditRecord(
            audit_id=str(uuid.uuid4()),
            tx_hash=tx_hash,
            event_type=AuditEventType.PIPELINE_ERROR,
            timestamp=utc_now(),
            risk_score=None,
            decision=None,
            details=details or {},
            error_info=error_message,
        )
        self.db.insert_audit_record(record)
        logger.error(
            "Logged pipeline error audit %s for tx %s: %s",
            record.audit_id[:8],
            tx_hash[:10],
            error_message,
        )
        return record

    def get_audit_trail(self, tx_hash: str) -> list[AuditRecord]:
        """Retrieve complete chronological audit trail for a specific transaction."""
        return self.db.get_audit_records_by_tx(tx_hash)

    def get_audit_record(self, audit_id: str) -> AuditRecord | None:
        """Retrieve an individual audit record by its unique identifier."""
        return self.db.get_audit_record_by_id(audit_id)

    def get_recent_audits(self, limit: int = 50, offset: int = 0) -> list[AuditRecord]:
        """Fetch paginated audit log entries ordered descending by timestamp."""
        return self.db.get_audit_records(limit=limit, offset=offset)

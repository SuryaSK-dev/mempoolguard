"""Read-only data loader for historical data and aggregate analytics."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

import pandas as pd

from core.database import Database

logger = logging.getLogger("mempoolguard.dashboard.data_loader")

_DB_EXCEPTIONS = (sqlite3.Error, OSError, ValueError, KeyError)


class DataLoader:
    """Read-only interface to existing SQLite database for dashboard visualization."""

    def __init__(self, db: Database | None = None, db_path: str = "data/mempoolguard.db") -> None:
        self.db = db or Database(db_path=db_path)

    def is_available(self) -> bool:
        """Check if SQLite database is accessible."""
        try:
            conn = self.db.get_connection()
            conn.execute("SELECT 1;")
            conn.close()
            return True
        except _DB_EXCEPTIONS:
            return False

    def get_summary_counts(self) -> dict[str, int]:
        """Fetch total record counts across all primary tables."""
        try:
            return self.db.get_summary_counts()
        except _DB_EXCEPTIONS as exc:
            logger.warning("Could not fetch summary counts: %s", exc)
            return {
                "raw_transactions": 0,
                "decoded_transactions": 0,
                "feature_vectors": 0,
                "model_predictions": 0,
                "decisions": 0,
                "audit_records": 0,
            }

    def get_recent_transactions(self, limit: int = 100) -> pd.DataFrame:
        """Fetch recently decoded transactions as a formatted DataFrame."""
        try:
            conn = self.db.get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT tx_hash, first_seen_at, sender, receiver, value_wei,
                       nonce, gas_limit, gas_price_wei, transaction_status, block_number
                FROM decoded_transactions
                ORDER BY first_seen_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            if not rows:
                return pd.DataFrame(
                    columns=[
                        "tx_hash",
                        "first_seen_at",
                        "sender",
                        "receiver",
                        "value_eth",
                        "value_wei",
                        "nonce",
                        "gas_limit",
                        "status",
                        "block_number",
                    ]
                )
            df = pd.DataFrame(rows)
            df["value_eth"] = df["value_wei"].apply(lambda v: float(v) / 1e18 if v else 0.0)
            df.rename(columns={"transaction_status": "status"}, inplace=True)
            return df
        except _DB_EXCEPTIONS as exc:
            logger.warning("Error fetching recent transactions: %s", exc)
            return pd.DataFrame()

    def get_recent_decisions(self, limit: int = 100) -> pd.DataFrame:
        """Fetch recent decision records with audit risk scores."""
        try:
            conn = self.db.get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT d.id, d.tx_hash, d.decision, d.reason_codes, d.policy_version,
                       d.created_at, d.is_simulated,
                       COALESCE(a.risk_score, p.risk_score, 0.0) as risk_score
                FROM decisions d
                LEFT JOIN audit_records a ON d.tx_hash = a.tx_hash
                LEFT JOIN model_predictions p ON d.tx_hash = p.tx_hash
                GROUP BY d.id
                ORDER BY d.id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            if not rows:
                return pd.DataFrame(
                    columns=[
                        "id",
                        "tx_hash",
                        "decision",
                        "risk_score",
                        "reason_codes",
                        "policy_version",
                        "created_at",
                        "is_simulated",
                    ]
                )
            df = pd.DataFrame(rows)
            df["reason_codes"] = df["reason_codes"].apply(
                lambda r: ", ".join(json.loads(r)) if isinstance(r, str) and r.startswith("[") else str(r)
            )
            return df
        except _DB_EXCEPTIONS as exc:
            logger.warning("Error fetching recent decisions: %s", exc)
            return pd.DataFrame()

    def get_recent_risk_results(self, limit: int = 100) -> pd.DataFrame:
        """Fetch recent model prediction records."""
        try:
            conn = self.db.get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT tx_hash, risk_score, predicted_class, model_name, model_version,
                       prediction_timestamp, inference_latency_ms, reason_codes
                FROM model_predictions
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            if not rows:
                return pd.DataFrame(
                    columns=[
                        "tx_hash",
                        "risk_score",
                        "predicted_class",
                        "model_name",
                        "model_version",
                        "prediction_timestamp",
                        "inference_latency_ms",
                        "reason_codes",
                    ]
                )
            df = pd.DataFrame(rows)
            df["reason_codes"] = df["reason_codes"].apply(
                lambda r: ", ".join(json.loads(r)) if isinstance(r, str) and r.startswith("[") else str(r)
            )
            return df
        except _DB_EXCEPTIONS as exc:
            logger.warning("Error fetching recent risk results: %s", exc)
            return pd.DataFrame()

    def get_recent_audit_records(self, limit: int = 100) -> pd.DataFrame:
        """Fetch recent immutable audit logs."""
        try:
            conn = self.db.get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT audit_id, tx_hash, event_type, timestamp, risk_score,
                       decision, details, error_info
                FROM audit_records
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            if not rows:
                return pd.DataFrame(
                    columns=[
                        "audit_id",
                        "tx_hash",
                        "event_type",
                        "timestamp",
                        "risk_score",
                        "decision",
                        "details",
                        "error_info",
                    ]
                )
            return pd.DataFrame(rows)
        except _DB_EXCEPTIONS as exc:
            logger.warning("Error fetching recent audit records: %s", exc)
            return pd.DataFrame()

    def get_decision_distribution(self) -> dict[str, int]:
        """Compute decision distribution counts (ALLOW, FLAG, HOLD)."""
        counts = {"ALLOW": 0, "FLAG": 0, "HOLD": 0}
        try:
            conn = self.db.get_connection()
            cur = conn.cursor()
            cur.execute(
                """
                SELECT decision, COUNT(*) as cnt
                FROM decisions
                GROUP BY decision
                """
            )
            for row in cur.fetchall():
                d = row["decision"]
                if d in counts:
                    counts[d] = row["cnt"]
                else:
                    counts[d] = row["cnt"]
            conn.close()
        except _DB_EXCEPTIONS as exc:
            logger.warning("Error computing decision distribution: %s", exc)
        return counts

    def get_risk_score_distribution(self) -> list[float]:
        """Fetch list of all recorded risk scores for histogram plotting."""
        try:
            conn = self.db.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT risk_score FROM audit_records WHERE risk_score IS NOT NULL")
            scores = [float(r["risk_score"]) for r in cur.fetchall()]
            conn.close()
            return scores
        except _DB_EXCEPTIONS as exc:
            logger.warning("Error fetching risk scores: %s", exc)
            return []

    def get_audit_event_distribution(self) -> dict[str, int]:
        """Compute distribution of audit event types."""
        counts: dict[str, int] = {}
        try:
            conn = self.db.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT event_type, COUNT(*) as cnt FROM audit_records GROUP BY event_type")
            for row in cur.fetchall():
                counts[row["event_type"]] = row["cnt"]
            conn.close()
        except _DB_EXCEPTIONS as exc:
            logger.warning("Error computing audit event distribution: %s", exc)
        return counts

    def get_all_transaction_hashes(self, limit: int = 200) -> list[str]:
        """Fetch list of known transaction hashes for selection dropdown."""
        try:
            conn = self.db.get_connection()
            cur = conn.cursor()
            cur.execute("SELECT tx_hash FROM decoded_transactions ORDER BY first_seen_at DESC LIMIT ?", (limit,))
            hashes = [r["tx_hash"] for r in cur.fetchall()]
            conn.close()
            return hashes
        except _DB_EXCEPTIONS:
            return []

    def get_transaction_detail(self, tx_hash: str) -> dict[str, Any] | None:
        """Fetch complete consolidated profile for a single transaction.

        Returns:
            Dictionary containing:
            - 'transaction': Decoded transaction model
            - 'features': FeatureVector model
            - 'prediction': Latest RiskResult model
            - 'decision': Latest Decision model
            - 'audit_records': List of AuditRecord models
        """
        clean_hash = tx_hash.strip().lower()
        tx = self.db.get_transaction(clean_hash)
        if not tx:
            return None

        fv = self.db.get_feature_vector(clean_hash)
        pred = self.db.get_latest_risk_result(clean_hash)
        dec = self.db.get_latest_decision(clean_hash)
        audits = self.db.get_audit_records_by_tx(clean_hash)

        return {
            "transaction": tx,
            "features": fv,
            "prediction": pred,
            "decision": dec,
            "audit_records": audits,
        }

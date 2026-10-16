"""Transactional SQLite persistence layer for MempoolGuard.

Manages connection lifecycle, schema initialization, indexes, transactional
integrity, and strongly typed CRUD operations for all pipeline contracts.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.contracts import (
    AuditEventType,
    AuditRecord,
    Decision,
    DecisionType,
    FeatureVector,
    RiskResult,
    Transaction,
    TransactionStatus,
)


class Database:
    """SQLite Database manager with WAL support, connection pooling, and schema management."""

    def __init__(
        self,
        db_path: str | Path = "data/mempoolguard.db",
        timeout_seconds: float = 10.0,
        enable_wal: bool = True,
    ) -> None:
        self.db_path = str(db_path)
        self.timeout_seconds = timeout_seconds
        self.enable_wal = enable_wal
        self._is_memory = self.db_path == ":memory:"
        self._keepalive_conn: sqlite3.Connection | None = None

        if self._is_memory:
            self._conn_target = f"file:memdb_{id(self)}?mode=memory&cache=shared"
            self._keepalive_conn = sqlite3.connect(self._conn_target, uri=True)
            self._keepalive_conn.execute("PRAGMA foreign_keys = ON;")
        else:
            self._conn_target = self.db_path
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        """Close keepalive connection if using in-memory mode."""
        if self._keepalive_conn:
            with suppress(sqlite3.Error):
                self._keepalive_conn.close()
            self._keepalive_conn = None

    def __del__(self) -> None:
        self.close()

    def get_connection(self) -> sqlite3.Connection:
        """Create and configure a new SQLite connection."""
        if self._is_memory:
            conn = sqlite3.connect(
                self._conn_target,
                uri=True,
                timeout=self.timeout_seconds,
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
        else:
            conn = sqlite3.connect(
                self._conn_target,
                timeout=self.timeout_seconds,
                detect_types=sqlite3.PARSE_DECLTYPES,
            )
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("PRAGMA foreign_keys = ON;")
            conn.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)};")
            if self.enable_wal and not self._is_memory:
                conn.execute("PRAGMA journal_mode = WAL;")
        return conn

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Cursor, None, None]:
        """Context manager providing an atomic database transaction."""
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            yield cursor
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        """Create all relational tables and performance indexes idempotently."""
        schema_sql = """
        -- 1. Raw Transactions
        CREATE TABLE IF NOT EXISTS raw_transactions (
            tx_hash TEXT PRIMARY KEY,
            raw_payload TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            captured_at TEXT NOT NULL
        );

        -- 2. Decoded Transactions
        CREATE TABLE IF NOT EXISTS decoded_transactions (
            tx_hash TEXT PRIMARY KEY REFERENCES raw_transactions(tx_hash) ON DELETE CASCADE,
            chain_id INTEGER NOT NULL,
            sender TEXT NOT NULL,
            receiver TEXT,
            value_wei TEXT NOT NULL,
            nonce INTEGER NOT NULL,
            gas_limit INTEGER NOT NULL,
            gas_price_wei TEXT,
            max_fee_per_gas_wei TEXT,
            max_priority_fee_per_gas_wei TEXT,
            input_data TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            confirmed_at TEXT,
            block_number INTEGER,
            transaction_status TEXT NOT NULL,
            feature_availability TEXT NOT NULL
        );

        -- 3. Feature Vectors
        CREATE TABLE IF NOT EXISTS feature_vectors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT NOT NULL REFERENCES raw_transactions(tx_hash) ON DELETE CASCADE,
            feature_timestamp TEXT NOT NULL,
            features TEXT NOT NULL,
            feature_names TEXT NOT NULL,
            feature_version TEXT NOT NULL,
            availability_mask TEXT NOT NULL,
            missing_features TEXT NOT NULL
        );

        -- 4. Model Predictions
        CREATE TABLE IF NOT EXISTS model_predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT NOT NULL REFERENCES raw_transactions(tx_hash) ON DELETE CASCADE,
            risk_score REAL NOT NULL,
            predicted_class INTEGER NOT NULL,
            model_name TEXT NOT NULL,
            model_version TEXT NOT NULL,
            prediction_timestamp TEXT NOT NULL,
            feature_timestamp TEXT NOT NULL,
            inference_latency_ms REAL NOT NULL,
            reason_codes TEXT NOT NULL,
            explanation TEXT NOT NULL
        );

        -- 5. Application Intervention Decisions
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT NOT NULL REFERENCES raw_transactions(tx_hash) ON DELETE CASCADE,
            decision TEXT NOT NULL,
            reason_codes TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            created_at TEXT NOT NULL,
            expires_at TEXT,
            is_simulated INTEGER NOT NULL DEFAULT 1
        );

        -- 6. Audit Trail Records
        CREATE TABLE IF NOT EXISTS audit_records (
            audit_id TEXT PRIMARY KEY,
            tx_hash TEXT NOT NULL,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            risk_score REAL,
            decision TEXT,
            details TEXT NOT NULL,
            error_info TEXT
        );

        -- Indexes for high-throughput querying
        CREATE INDEX IF NOT EXISTS idx_decoded_sender ON decoded_transactions(sender);
        CREATE INDEX IF NOT EXISTS idx_decoded_receiver ON decoded_transactions(receiver);
        CREATE INDEX IF NOT EXISTS idx_decoded_status ON decoded_transactions(transaction_status);
        CREATE INDEX IF NOT EXISTS idx_decoded_first_seen ON decoded_transactions(first_seen_at);

        CREATE INDEX IF NOT EXISTS idx_features_tx ON feature_vectors(tx_hash);
        CREATE INDEX IF NOT EXISTS idx_predictions_tx ON model_predictions(tx_hash);
        CREATE INDEX IF NOT EXISTS idx_decisions_tx ON decisions(tx_hash);

        CREATE INDEX IF NOT EXISTS idx_audit_tx ON audit_records(tx_hash);
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_records(timestamp);
        """
        with self.transaction() as cur:
            cur.executescript(schema_sql)

    # -------------------------------------------------------------------------
    # Raw & Decoded Transactions
    # -------------------------------------------------------------------------

    def insert_raw_transaction(
        self,
        tx_hash: str,
        raw_payload: str,
        first_seen_at: datetime,
    ) -> bool:
        """Insert a raw transaction payload idempotently. Returns True if inserted, False if duplicate."""
        now_utc = datetime.now(timezone.utc).isoformat()
        first_seen_str = first_seen_at.astimezone(timezone.utc).isoformat()
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO raw_transactions (tx_hash, raw_payload, first_seen_at, captured_at)
                VALUES (?, ?, ?, ?)
                """,
                (tx_hash.lower(), raw_payload, first_seen_str, now_utc),
            )
            return cur.rowcount > 0

    def get_raw_transaction(self, tx_hash: str) -> dict[str, Any] | None:
        """Fetch a raw transaction payload by transaction hash."""
        with self.get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT tx_hash, raw_payload, first_seen_at, captured_at FROM raw_transactions WHERE tx_hash = ?",
                (tx_hash.lower(),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return {
                "tx_hash": row["tx_hash"],
                "raw_payload": row["raw_payload"],
                "first_seen_at": datetime.fromisoformat(row["first_seen_at"]),
                "captured_at": datetime.fromisoformat(row["captured_at"]),
            }

    def insert_decoded_transaction(self, tx: Transaction) -> bool:
        """Insert or update a decoded transaction model. Returns True if created/updated."""
        # Ensure parent raw transaction entry exists
        self.insert_raw_transaction(
            tx_hash=tx.tx_hash,
            raw_payload="{}",
            first_seen_at=tx.first_seen_at,
        )

        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO decoded_transactions (
                    tx_hash, chain_id, sender, receiver, value_wei, nonce,
                    gas_limit, gas_price_wei, max_fee_per_gas_wei,
                    max_priority_fee_per_gas_wei, input_data, first_seen_at,
                    confirmed_at, block_number, transaction_status, feature_availability
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tx_hash) DO UPDATE SET
                    confirmed_at = excluded.confirmed_at,
                    block_number = excluded.block_number,
                    transaction_status = excluded.transaction_status,
                    feature_availability = excluded.feature_availability
                """,
                (
                    tx.tx_hash.lower(),
                    tx.chain_id,
                    tx.sender.lower(),
                    tx.receiver.lower() if tx.receiver else None,
                    str(tx.value_wei),
                    tx.nonce,
                    tx.gas_limit,
                    str(tx.gas_price_wei) if tx.gas_price_wei is not None else None,
                    str(tx.max_fee_per_gas_wei) if tx.max_fee_per_gas_wei is not None else None,
                    str(tx.max_priority_fee_per_gas_wei)
                    if tx.max_priority_fee_per_gas_wei is not None
                    else None,
                    tx.input_data,
                    tx.first_seen_at.isoformat(),
                    tx.confirmed_at.isoformat() if tx.confirmed_at else None,
                    tx.block_number,
                    tx.transaction_status.value,
                    json.dumps(tx.feature_availability),
                ),
            )
            return True

    def get_transaction(self, tx_hash: str) -> Transaction | None:
        """Retrieve a decoded transaction by hash."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT * FROM decoded_transactions WHERE tx_hash = ?",
                (tx_hash.lower(),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_transaction(row)
        finally:
            conn.close()

    @staticmethod
    def _row_to_transaction(row: sqlite3.Row) -> Transaction:
        """Convert a SQLite row into a validated Transaction model."""
        return Transaction(
            tx_hash=row["tx_hash"],
            chain_id=row["chain_id"],
            sender=row["sender"],
            receiver=row["receiver"],
            value_wei=int(row["value_wei"]),
            nonce=row["nonce"],
            gas_limit=row["gas_limit"],
            gas_price_wei=int(row["gas_price_wei"]) if row["gas_price_wei"] else None,
            max_fee_per_gas_wei=int(row["max_fee_per_gas_wei"])
            if row["max_fee_per_gas_wei"]
            else None,
            max_priority_fee_per_gas_wei=int(row["max_priority_fee_per_gas_wei"])
            if row["max_priority_fee_per_gas_wei"]
            else None,
            input_data=row["input_data"],
            first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
            confirmed_at=datetime.fromisoformat(row["confirmed_at"])
            if row["confirmed_at"]
            else None,
            block_number=row["block_number"],
            transaction_status=TransactionStatus(row["transaction_status"]),
            feature_availability=json.loads(row["feature_availability"]),
        )

    def get_historical_transactions(
        self,
        cutoff_time: datetime,
        sender: str | None = None,
        receiver: str | None = None,
        exclude_tx_hash: str | None = None,
        limit: int = 1000,
    ) -> list[Transaction]:
        """Fetch transactions strictly observed at or before cutoff_time."""
        cutoff_str = cutoff_time.astimezone(timezone.utc).isoformat()
        conditions = ["first_seen_at <= ?"]
        params: list[Any] = [cutoff_str]

        if sender:
            conditions.append("sender = ?")
            params.append(sender.lower())
        if receiver:
            conditions.append("receiver = ?")
            params.append(receiver.lower())
        if exclude_tx_hash:
            conditions.append("tx_hash != ?")
            params.append(exclude_tx_hash.lower())

        query = (
            f"SELECT * FROM decoded_transactions "
            f"WHERE {' AND '.join(conditions)} "
            f"ORDER BY first_seen_at ASC LIMIT {limit}"
        )
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(query, params)
            return [self._row_to_transaction(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def get_transactions_in_window(
        self,
        start_time: datetime,
        end_time: datetime,
        sender: str | None = None,
        exclude_tx_hash: str | None = None,
        limit: int = 1000,
    ) -> list[Transaction]:
        """Fetch transactions strictly within [start_time, end_time]."""
        start_str = start_time.astimezone(timezone.utc).isoformat()
        end_str = end_time.astimezone(timezone.utc).isoformat()
        conditions = ["first_seen_at >= ?", "first_seen_at <= ?"]
        params: list[Any] = [start_str, end_str]

        if sender:
            conditions.append("sender = ?")
            params.append(sender.lower())
        if exclude_tx_hash:
            conditions.append("tx_hash != ?")
            params.append(exclude_tx_hash.lower())

        query = (
            f"SELECT * FROM decoded_transactions "
            f"WHERE {' AND '.join(conditions)} "
            f"ORDER BY first_seen_at ASC LIMIT {limit}"
        )
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(query, params)
            return [self._row_to_transaction(r) for r in cur.fetchall()]
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # Feature Vectors
    # -------------------------------------------------------------------------

    def insert_feature_vector(self, fv: FeatureVector) -> int:
        """Insert a calculated feature vector. Returns the inserted row ID."""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO feature_vectors (
                    tx_hash, feature_timestamp, features, feature_names,
                    feature_version, availability_mask, missing_features
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    fv.tx_hash.lower(),
                    fv.feature_timestamp.isoformat(),
                    json.dumps(fv.features),
                    json.dumps(fv.feature_names),
                    fv.feature_version,
                    json.dumps(fv.availability_mask),
                    json.dumps(fv.missing_features),
                ),
            )
            return cur.lastrowid or 0

    def get_latest_feature_vector(self, tx_hash: str) -> FeatureVector | None:
        """Fetch the most recent feature vector for a transaction."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM feature_vectors
                WHERE tx_hash = ?
                ORDER BY id DESC LIMIT 1
                """,
                (tx_hash.lower(),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return FeatureVector(
                tx_hash=row["tx_hash"],
                feature_timestamp=datetime.fromisoformat(row["feature_timestamp"]),
                features=json.loads(row["features"]),
                feature_names=json.loads(row["feature_names"]),
                feature_version=row["feature_version"],
                availability_mask=json.loads(row["availability_mask"]),
                missing_features=json.loads(row["missing_features"]),
            )
        finally:
            conn.close()

    get_feature_vector = get_latest_feature_vector

    # -------------------------------------------------------------------------
    # Model Predictions & Decisions
    # -------------------------------------------------------------------------

    def insert_risk_result(self, result: RiskResult) -> int:
        """Record a model prediction result."""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO model_predictions (
                    tx_hash, risk_score, predicted_class, model_name,
                    model_version, prediction_timestamp, feature_timestamp,
                    inference_latency_ms, reason_codes, explanation
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.tx_hash.lower(),
                    result.risk_score,
                    result.predicted_class,
                    result.model_name,
                    result.model_version,
                    result.prediction_timestamp.isoformat(),
                    result.feature_timestamp.isoformat(),
                    result.inference_latency_ms,
                    json.dumps(result.reason_codes),
                    result.explanation,
                ),
            )
            return cur.lastrowid or 0

    def get_latest_risk_result(self, tx_hash: str) -> RiskResult | None:
        """Fetch the most recent risk prediction for a transaction."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM model_predictions
                WHERE tx_hash = ?
                ORDER BY id DESC LIMIT 1
                """,
                (tx_hash.lower(),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return RiskResult(
                tx_hash=row["tx_hash"],
                risk_score=row["risk_score"],
                predicted_class=row["predicted_class"],
                model_name=row["model_name"],
                model_version=row["model_version"],
                prediction_timestamp=datetime.fromisoformat(row["prediction_timestamp"]),
                feature_timestamp=datetime.fromisoformat(row["feature_timestamp"]),
                inference_latency_ms=row["inference_latency_ms"],
                reason_codes=json.loads(row["reason_codes"]),
                explanation=row["explanation"],
            )
        finally:
            conn.close()

    def insert_decision(self, decision: Decision) -> int:
        """Record a simulated decision event."""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO decisions (
                    tx_hash, decision, reason_codes, policy_version,
                    created_at, expires_at, is_simulated
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision.tx_hash.lower(),
                    decision.decision.value,
                    json.dumps(decision.reason_codes),
                    decision.policy_version,
                    decision.created_at.isoformat(),
                    decision.expires_at.isoformat() if decision.expires_at else None,
                    1 if decision.is_simulated else 0,
                ),
            )
            return cur.lastrowid or 0

    def get_latest_decision(self, tx_hash: str) -> Decision | None:
        """Fetch the most recent decision for a transaction."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM decisions
                WHERE tx_hash = ?
                ORDER BY id DESC LIMIT 1
                """,
                (tx_hash.lower(),),
            )
            row = cur.fetchone()
            if not row:
                return None
            return Decision(
                tx_hash=row["tx_hash"],
                decision=DecisionType(row["decision"]),
                reason_codes=json.loads(row["reason_codes"]),
                policy_version=row["policy_version"],
                created_at=datetime.fromisoformat(row["created_at"]),
                expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
                is_simulated=bool(row["is_simulated"]),
            )
        finally:
            conn.close()

    def get_decisions_by_tx(self, tx_hash: str) -> list[Decision]:
        """Fetch all decisions recorded for a transaction ordered chronologically."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM decisions
                WHERE tx_hash = ?
                ORDER BY id ASC
                """,
                (tx_hash.lower(),),
            )
            rows = cur.fetchall()
            return [
                Decision(
                    tx_hash=row["tx_hash"],
                    decision=DecisionType(row["decision"]),
                    reason_codes=json.loads(row["reason_codes"]),
                    policy_version=row["policy_version"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
                    is_simulated=bool(row["is_simulated"]),
                )
                for row in rows
            ]
        finally:
            conn.close()

    # -------------------------------------------------------------------------
    # Audit Trail
    # -------------------------------------------------------------------------

    def insert_audit_record(self, record: AuditRecord) -> None:
        """Insert an immutable audit log record."""
        with self.transaction() as cur:
            cur.execute(
                """
                INSERT INTO audit_records (
                    audit_id, tx_hash, event_type, timestamp,
                    risk_score, decision, details, error_info
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.audit_id,
                    record.tx_hash.lower(),
                    record.event_type.value,
                    record.timestamp.isoformat(),
                    record.risk_score,
                    record.decision.value if record.decision else None,
                    json.dumps(record.details),
                    record.error_info,
                ),
            )

    def get_audit_records(self, limit: int = 50, offset: int = 0) -> list[AuditRecord]:
        """Fetch paginated audit log entries ordered descending by timestamp."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM audit_records
                ORDER BY timestamp DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            )
            rows = cur.fetchall()
            return [
                AuditRecord(
                    audit_id=row["audit_id"],
                    tx_hash=row["tx_hash"],
                    event_type=AuditEventType(row["event_type"]),
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    risk_score=row["risk_score"],
                    decision=DecisionType(row["decision"]) if row["decision"] else None,
                    details=json.loads(row["details"]),
                    error_info=row["error_info"],
                )
                for row in rows
            ]
        finally:
            conn.close()

    def get_audit_records_by_tx(self, tx_hash: str) -> list[AuditRecord]:
        """Fetch all audit log entries for a transaction ordered ascending by timestamp."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM audit_records
                WHERE tx_hash = ?
                ORDER BY timestamp ASC
                """,
                (tx_hash.lower(),),
            )
            rows = cur.fetchall()
            return [
                AuditRecord(
                    audit_id=row["audit_id"],
                    tx_hash=row["tx_hash"],
                    event_type=AuditEventType(row["event_type"]),
                    timestamp=datetime.fromisoformat(row["timestamp"]),
                    risk_score=row["risk_score"],
                    decision=DecisionType(row["decision"]) if row["decision"] else None,
                    details=json.loads(row["details"]),
                    error_info=row["error_info"],
                )
                for row in rows
            ]
        finally:
            conn.close()

    def get_audit_record_by_id(self, audit_id: str) -> AuditRecord | None:
        """Fetch an audit log entry by its unique audit_id."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT * FROM audit_records
                WHERE audit_id = ?
                LIMIT 1
                """,
                (audit_id,),
            )
            row = cur.fetchone()
            if not row:
                return None
            return AuditRecord(
                audit_id=row["audit_id"],
                tx_hash=row["tx_hash"],
                event_type=AuditEventType(row["event_type"]),
                timestamp=datetime.fromisoformat(row["timestamp"]),
                risk_score=row["risk_score"],
                decision=DecisionType(row["decision"]) if row["decision"] else None,
                details=json.loads(row["details"]),
                error_info=row["error_info"],
            )
        finally:
            conn.close()

    def get_summary_counts(self) -> dict[str, int]:
        """Count totals for transactions, predictions, decisions, and audit events."""
        conn = self.get_connection()
        try:
            cur = conn.cursor()
            counts = {}
            for table in (
                "raw_transactions",
                "decoded_transactions",
                "feature_vectors",
                "model_predictions",
                "decisions",
                "audit_records",
            ):
                cur.execute(f"SELECT COUNT(*) as cnt FROM {table}")
                counts[table] = cur.fetchone()["cnt"]
            return counts
        finally:
            conn.close()

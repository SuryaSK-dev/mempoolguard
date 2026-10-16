"""Audit trail and decision lookup endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from api.dependencies import get_db
from api.schemas import AuditLookupResponse, AuditRecordItem, _validate_hex_hash
from core.database import Database

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get(
    "/{tx_hash}",
    response_model=AuditLookupResponse,
    status_code=status.HTTP_200_OK,
    summary="Query Transaction Audit Records",
    description="Fetches all persisted decisions and immutable audit records for a given transaction hash.",
)
def get_transaction_audit(
    tx_hash: Annotated[str, Path(description="32-byte transaction hash (0x hex)")],
    db: Annotated[Database, Depends(get_db)],
) -> AuditLookupResponse:
    """Retrieve all decisions and audit events for a transaction."""
    try:
        clean_tx = _validate_hex_hash(tx_hash)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    decisions = db.get_decisions_by_tx(clean_tx)
    audit_records = db.get_audit_records_by_tx(clean_tx)

    dec_items = [
        {
            "tx_hash": d.tx_hash,
            "decision": d.decision.value,
            "reason_codes": d.reason_codes,
            "policy_version": d.policy_version,
            "created_at": d.created_at.isoformat(),
            "expires_at": d.expires_at.isoformat() if d.expires_at else None,
            "is_simulated": d.is_simulated,
        }
        for d in decisions
    ]

    audit_items = [
        AuditRecordItem(
            audit_id=r.audit_id,
            tx_hash=r.tx_hash,
            event_type=r.event_type.value,
            timestamp=r.timestamp,
            risk_score=r.risk_score,
            decision=r.decision.value if r.decision else None,
            details=r.details,
            error_info=r.error_info,
        )
        for r in audit_records
    ]

    return AuditLookupResponse(
        tx_hash=clean_tx,
        decisions=dec_items,
        audit_records=audit_items,
    )


@router.get(
    "/record/{audit_id}",
    response_model=AuditRecordItem,
    status_code=status.HTTP_200_OK,
    summary="Fetch Single Audit Record by ID",
    description="Retrieves an individual immutable audit record using its unique UUID identifier.",
)
def get_audit_record_by_id(
    audit_id: Annotated[str, Path(description="UUID string of the audit record")],
    db: Annotated[Database, Depends(get_db)],
) -> AuditRecordItem:
    """Retrieve an audit record by its primary key."""
    clean_id = audit_id.strip()
    record = db.get_audit_record_by_id(clean_id)
    if not record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Audit record '{clean_id}' not found.",
        )

    return AuditRecordItem(
        audit_id=record.audit_id,
        tx_hash=record.tx_hash,
        event_type=record.event_type.value,
        timestamp=record.timestamp,
        risk_score=record.risk_score,
        decision=record.decision.value if record.decision else None,
        details=record.details,
        error_info=record.error_info,
    )

"""Decision endpoints exposing existing M6 -> M7 decision and audit flow."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import (
    ModelRegistry,
    get_db,
    get_decision_engine,
    get_feature_engine,
    get_model_registry,
)
from api.schemas import (
    DecisionRequest,
    DecisionResponse,
    SimulateDecisionRequest,
)
from core.contracts import FeatureVector, Transaction
from core.database import Database
from dataset.schema import FEATURE_COLUMNS
from decision.engine import DecisionEngine
from features.engine import FeatureEngine

router = APIRouter(prefix="", tags=["Decision"])


def _prepare_decision_features(
    req: DecisionRequest,
    feature_engine: FeatureEngine,
    db: Database,
) -> tuple[str, dict[str, Any]]:
    """Extract or align deterministic features for decision processing."""
    if req.transaction is not None:
        tx_data = req.transaction.model_dump()
        tx = Transaction(**tx_data)
        db.insert_decoded_transaction(tx)
        fv = feature_engine.extract_features(tx)
        db.insert_feature_vector(fv)
        return tx.tx_hash, fv.features

    if req.features is not None:
        tx_hash = req.tx_hash or "0x" + "00" * 32
        aligned = {col: float(req.features.get(col, 0.0)) for col in FEATURE_COLUMNS}
        fv = FeatureVector(
            tx_hash=tx_hash,
            features=aligned,
            feature_names=list(FEATURE_COLUMNS),
            feature_version=feature_engine.feature_version,
        )
        return tx_hash, aligned

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Missing required transaction or features payload.",
    )


@router.post(
    "/decision",
    response_model=DecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Evaluate Pre-Confirmation Risk Decision",
    description="Full M6 -> M7 integration: Extracts features, executes M6 inference, evaluates M7 decision policy (ALLOW / FLAG / HOLD), and persists an immutable audit record.",
)
def evaluate_decision(
    req: DecisionRequest,
    registry: Annotated[ModelRegistry, Depends(get_model_registry)],
    feature_engine: Annotated[FeatureEngine, Depends(get_feature_engine)],
    decision_engine: Annotated[DecisionEngine, Depends(get_decision_engine)],
    db: Annotated[Database, Depends(get_db)],
) -> DecisionResponse:
    """Evaluate application-level simulated decision via existing DecisionEngine."""
    # 1. Resolve model
    try:
        model, meta = registry.get_model(req.model_name, version=req.model_version)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    # 2. Extract or format features
    tx_hash, feats_dict = _prepare_decision_features(req, feature_engine, db)

    # 3. Execute inference + decision through M7 DecisionEngine (with error containment)
    dec, audit_rec = decision_engine.decide_with_model(
        model=model,
        tx_hash=tx_hash,
        features=feats_dict,
        idempotency=req.idempotency,
    )

    model_name = meta.get("model_name", type(model).__name__)
    model_version = meta.get("model_version", "1.0.0")

    return DecisionResponse(
        tx_hash=dec.tx_hash,
        decision=dec.decision,
        risk_score=audit_rec.risk_score if audit_rec else None,
        reason_codes=dec.reason_codes,
        policy_version=dec.policy_version,
        model_name=model_name,
        model_version=model_version,
        created_at=dec.created_at,
        expires_at=dec.expires_at,
        is_simulated=dec.is_simulated,
        audit_id=audit_rec.audit_id if audit_rec else None,
    )


@router.post(
    "/simulate/decision",
    response_model=DecisionResponse,
    status_code=status.HTTP_200_OK,
    summary="Simulate Threshold Decision (Demo / Exploration)",
    description="Explicitly isolated testing endpoint to evaluate a manual numeric risk score [0–100] against M7 policy thresholds without claiming real ML inference.",
)
def simulate_decision(
    req: SimulateDecisionRequest,
    decision_engine: Annotated[DecisionEngine, Depends(get_decision_engine)],
) -> DecisionResponse:
    """Simulate policy evaluation on a manually supplied score."""
    dec, audit_rec = decision_engine.decide_score(
        tx_hash=req.tx_hash,
        risk_score=req.risk_score,
        features=req.features,
        model_name=req.model_name,
        model_version="simulation",
        idempotency=req.idempotency,
    )

    return DecisionResponse(
        tx_hash=dec.tx_hash,
        decision=dec.decision,
        risk_score=req.risk_score,
        reason_codes=dec.reason_codes,
        policy_version=dec.policy_version,
        model_name=req.model_name,
        model_version="simulation",
        created_at=dec.created_at,
        expires_at=dec.expires_at,
        is_simulated=True,
        audit_id=audit_rec.audit_id if audit_rec else None,
    )

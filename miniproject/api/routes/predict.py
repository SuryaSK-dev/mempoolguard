"""Risk score prediction endpoint exposing existing M6 models."""

from __future__ import annotations

import time
from typing import Annotated, Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, status

from api.dependencies import (
    ModelRegistry,
    get_db,
    get_feature_engine,
    get_model_registry,
)
from api.schemas import PredictRequest, PredictResponse
from core.contracts import FeatureVector, RiskResult, Transaction, utc_now
from core.database import Database
from dataset.schema import FEATURE_COLUMNS
from features.engine import FeatureEngine

router = APIRouter(prefix="", tags=["Prediction"])


def _prepare_features(
    req: PredictRequest,
    feature_engine: FeatureEngine,
    db: Database,
) -> tuple[str, dict[str, Any], str]:
    """Extract or format deterministic 30-feature vector from request."""
    if req.transaction is not None:
        tx_data = req.transaction.model_dump()
        tx = Transaction(**tx_data)
        # Ensure parent raw & decoded record exist in db for relational lookups
        db.insert_decoded_transaction(tx)
        fv = feature_engine.extract_features(tx)
        db.insert_feature_vector(fv)
        return tx.tx_hash, fv.features, fv.feature_version

    if req.features is not None:
        tx_hash = req.tx_hash or "0x" + "00" * 32
        # Conform input to canonical 30 features
        aligned = {col: float(req.features.get(col, 0.0)) for col in FEATURE_COLUMNS}
        fv = FeatureVector(
            tx_hash=tx_hash,
            features=aligned,
            feature_names=list(FEATURE_COLUMNS),
            feature_version=feature_engine.feature_version,
        )
        return tx_hash, aligned, fv.feature_version

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Missing required transaction or features payload.",
    )


@router.post(
    "/predict",
    response_model=PredictResponse,
    status_code=status.HTTP_200_OK,
    summary="Predict Pre-Confirmation Risk Score",
    description="Accepts transaction attributes or canonical features, performs inference using an M6 model, and returns a 0–100 risk score with explanation reason codes.",
)
def predict_risk(
    req: PredictRequest,
    registry: Annotated[ModelRegistry, Depends(get_model_registry)],
    feature_engine: Annotated[FeatureEngine, Depends(get_feature_engine)],
    db: Annotated[Database, Depends(get_db)],
) -> PredictResponse:
    """Execute model prediction using the existing M6 model pipeline."""
    # 1. Resolve model from registry
    try:
        model, meta = registry.get_model(req.model_name, version=req.model_version)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    # 2. Extract or conform 30-feature vector
    tx_hash, feats_dict, feat_version = _prepare_features(req, feature_engine, db)

    # 3. Execute inference and measure latency
    model_name = meta.get("model_name", type(model).__name__)
    model_version = meta.get("model_version", "1.0.0")

    t0 = time.perf_counter()
    reasons: list[str] = []

    try:
        if hasattr(model, "evaluate_sample"):
            score_raw, pred_cls_raw, reasons = model.evaluate_sample(feats_dict)
            risk_score = float(score_raw)
            predicted_class = int(pred_cls_raw)
        else:
            df = pd.DataFrame([feats_dict])
            scores = model.predict_risk_scores(df)
            risk_score = float(scores[0])
            preds = model.predict(df)
            predicted_class = int(preds[0])
            # Add basic score-based reason tag if model emits none directly
            if risk_score >= 70.0:
                reasons.append("HIGH_RISK_SCORE")
            elif risk_score > 30.0:
                reasons.append("MEDIUM_RISK_SCORE")
            else:
                reasons.append("LOW_RISK_SCORE")
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Inference error in model '{model_name}': {type(exc).__name__}",
        ) from exc

    latency_ms = round((time.perf_counter() - t0) * 1000.0, 3)

    # 4. Record prediction to SQLite
    now = utc_now()
    risk_result = RiskResult(
        tx_hash=tx_hash,
        risk_score=risk_score,
        predicted_class=predicted_class,
        model_name=model_name,
        model_version=model_version,
        prediction_timestamp=now,
        feature_timestamp=now,
        inference_latency_ms=latency_ms,
        reason_codes=reasons,
        explanation=f"Evaluated by {model_name} v{model_version} (score: {risk_score:.2f})",
    )
    # Ensure raw_transactions record exists before foreign key insert
    if not db.get_raw_transaction(tx_hash):
        db.insert_raw_transaction(tx_hash, "{}", first_seen_at=now)
    db.insert_risk_result(risk_result)

    return PredictResponse(
        tx_hash=tx_hash,
        risk_score=risk_score,
        predicted_class=predicted_class,
        model_name=model_name,
        model_version=model_version,
        inference_latency_ms=latency_ms,
        reason_codes=reasons,
        feature_version=feat_version,
        explanation=risk_result.explanation,
    )

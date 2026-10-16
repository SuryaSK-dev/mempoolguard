"""API route definitions for MempoolGuard service."""

from __future__ import annotations

from api.routes.audit import router as audit_router
from api.routes.decision import router as decision_router
from api.routes.health import router as health_router
from api.routes.predict import router as predict_router

__all__ = [
    "audit_router",
    "decision_router",
    "health_router",
    "predict_router",
]

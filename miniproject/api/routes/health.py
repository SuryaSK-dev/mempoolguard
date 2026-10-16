"""Health check and system diagnostics endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from api.dependencies import ModelRegistry, get_config, get_db, get_model_registry
from api.schemas import HealthResponse
from core.config import AppConfig
from core.database import Database
from core.health import check_database, check_python_environment, check_rpc_node

router = APIRouter(prefix="", tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="System Health Diagnostics",
    description="Reports connectivity and operational readiness of the API, SQLite database, loaded models, and EVM RPC node.",
)
def get_health_status(
    config: Annotated[AppConfig, Depends(get_config)],
    db: Annotated[Database, Depends(get_db)],
    registry: Annotated[ModelRegistry, Depends(get_model_registry)],
) -> HealthResponse:
    """Perform diagnostics across all required subsystems without crashing."""
    python_diag = check_python_environment()
    db_diag = check_database(db)
    rpc_diag = check_rpc_node(
        rpc_url=config.blockchain.rpc_http_url,
        timeout_seconds=config.blockchain.connection_timeout_seconds,
    )
    models_diag = registry.get_status()

    # Determine overall status
    is_db_healthy = db_diag.get("status") == "HEALTHY"
    is_rpc_healthy = rpc_diag.get("status") == "HEALTHY"
    has_models = models_diag.get("loaded_count", 0) > 0

    if is_db_healthy and is_rpc_healthy and has_models:
        overall_status = "HEALTHY"
    elif is_db_healthy and has_models:
        # Operating in degraded local mode (e.g. Anvil offline)
        overall_status = "DEGRADED"
    else:
        overall_status = "UNHEALTHY"

    api_meta = {
        "title": config.api.title,
        "version": config.api.version,
        "host": config.api.host,
        "port": config.api.port,
        "status": "RUNNING",
    }

    return HealthResponse(
        status=overall_status,
        api=api_meta,
        python=python_diag,
        database=db_diag,
        models=models_diag,
        rpc=rpc_diag,
    )

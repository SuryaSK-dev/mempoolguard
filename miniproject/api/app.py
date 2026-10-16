"""FastAPI application factory and middleware configuration for MempoolGuard."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.dependencies import get_config, get_db, get_model_registry
from api.routes import audit_router, decision_router, health_router, predict_router
from core.contracts import utc_now

logger = logging.getLogger("mempoolguard.api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle management."""
    logger.info("Initializing MempoolGuard API service...")
    # Initialize database and models at startup
    db = get_db()
    db.init_schema()
    registry = get_model_registry()
    logger.info("MempoolGuard API initialized with models: %s", registry.get_status()["available_models"])
    yield
    logger.info("Shutting down MempoolGuard API service.")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    config = get_config()

    application = FastAPI(
        title=config.api.title,
        version=config.api.version,
        description=(
            "MempoolGuard: Machine Learning-Based Pre-Confirmation "
            "Cryptocurrency Transaction Risk Detection REST Service."
        ),
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # 1. CORS Middleware
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Global Exception Handlers
    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for err in exc.errors():
            loc = " -> ".join(str(item) for item in err.get("loc", []))
            errors.append(f"{loc}: {err.get('msg', 'validation error')}")
        logger.warning("Validation error on %s %s: %s", request.method, request.url.path, errors)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": "VALIDATION_ERROR",
                "detail": "; ".join(errors),
                "timestamp": utc_now().isoformat(),
            },
        )

    @application.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "HTTP_ERROR",
                "detail": str(exc.detail),
                "timestamp": utc_now().isoformat(),
            },
        )

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled internal exception on %s %s: %s", request.method, request.url.path, exc)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": "INTERNAL_SERVER_ERROR",
                "detail": "An unexpected internal server error occurred.",
                "timestamp": utc_now().isoformat(),
            },
        )

    # 3. Mount Routers
    application.include_router(health_router)
    application.include_router(predict_router)
    application.include_router(decision_router)
    application.include_router(audit_router)

    return application


# Top-level instance for uvicorn run (uvicorn api.app:app)
app = create_app()

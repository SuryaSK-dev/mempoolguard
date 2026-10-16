"""Resilient HTTP API client for MempoolGuard dashboard communicating with FastAPI."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("mempoolguard.dashboard.api_client")


class ApiClient:
    """Client for querying MempoolGuard M8 FastAPI service with error isolation."""

    def __init__(self, base_url: str = "http://127.0.0.1:8000", timeout_seconds: float = 3.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout_seconds

    def check_health(self) -> dict[str, Any]:
        """Query GET /health diagnostic endpoint.

        Returns:
            Dictionary with health status or error details if unreachable.
        """
        url = f"{self.base_url}/health"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    data["is_connected"] = True
                    return data
                return {
                    "is_connected": True,
                    "status": "DEGRADED",
                    "error": f"HTTP {resp.status_code}: {resp.text}",
                    "subsystems": {},
                }
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
            logger.debug("FastAPI health check failed: %s", exc)
            return {
                "is_connected": False,
                "status": "OFFLINE",
                "error": str(exc),
                "subsystems": {},
            }

    def predict(self, transaction: dict[str, Any], model_name: str | None = None) -> dict[str, Any]:
        """Request risk score prediction from POST /predict.

        Args:
            transaction: Transaction dictionary matching TransactionInput schema.
            model_name: Optional model identifier ('xgboost', 'logistic_regression', 'rule_baseline').

        Returns:
            Dictionary with prediction response or error info.
        """
        url = f"{self.base_url}/predict"
        payload = {"transaction": transaction, "model_name": model_name}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    return {"success": True, **resp.json()}
                return {"success": False, "status_code": resp.status_code, "error": resp.text}
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
            return {"success": False, "error": str(exc)}

    def decide(self, transaction: dict[str, Any], model_name: str | None = None) -> dict[str, Any]:
        """Execute full M6 -> M7 decision flow via POST /decision.

        Args:
            transaction: Transaction dictionary matching TransactionInput schema.
            model_name: Optional model identifier.

        Returns:
            Dictionary with decision response or error info.
        """
        url = f"{self.base_url}/decision"
        payload = {"transaction": transaction, "model_name": model_name}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    return {"success": True, **resp.json()}
                return {"success": False, "status_code": resp.status_code, "error": resp.text}
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
            return {"success": False, "error": str(exc)}

    def simulate_decision(self, risk_score: float, tx_hash: str | None = None) -> dict[str, Any]:
        """Test policy engine boundaries via POST /simulate/decision.

        Args:
            risk_score: Numerical risk score between 0.0 and 100.0.
            tx_hash: Optional transaction hash.

        Returns:
            Simulated decision response dictionary.
        """
        url = f"{self.base_url}/simulate/decision"
        payload = {"risk_score": risk_score, "tx_hash": tx_hash}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=payload)
                if resp.status_code == 200:
                    return {"success": True, **resp.json()}
                return {"success": False, "status_code": resp.status_code, "error": resp.text}
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
            return {"success": False, "error": str(exc)}

    def get_audit_trail(self, tx_hash: str) -> dict[str, Any]:
        """Fetch audit trail for a transaction from GET /audit/{tx_hash}."""
        url = f"{self.base_url}/audit/{tx_hash}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return {"success": True, **resp.json()}
                return {"success": False, "status_code": resp.status_code, "error": resp.text}
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
            return {"success": False, "error": str(exc)}

    def get_audit_record(self, audit_id: str) -> dict[str, Any]:
        """Fetch a specific audit record by ID from GET /audit/record/{audit_id}."""
        url = f"{self.base_url}/audit/record/{audit_id}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    return {"success": True, "record": resp.json()}
                return {"success": False, "status_code": resp.status_code, "error": resp.text}
        except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
            return {"success": False, "error": str(exc)}

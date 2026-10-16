"""Dependency injection providers and state management for the MempoolGuard API."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from core.config import AppConfig, load_config
from core.database import Database
from decision.engine import DecisionEngine
from features.engine import FeatureEngine
from models.baseline import RuleBaseline
from models.persistence import load_model

logger = logging.getLogger("mempoolguard.api.dependencies")


class ModelRegistry:
    """Manages loaded machine learning models and baselines for API inference across versions."""

    def __init__(
        self,
        saved_dir: str | Path | None = None,
        default_version: str = "v1",
    ) -> None:
        raw_dir = saved_dir or os.getenv("MEMPOOLGUARD_MODEL_DIR", "models/saved")
        self.saved_dir = Path(raw_dir)
        self.v2_dir = self.saved_dir / "v2" if self.saved_dir.name != "v2" else self.saved_dir
        self.default_version = os.getenv("MEMPOOLGUARD_DEFAULT_MODEL_VERSION", default_version).lower()
        self.models: dict[str, Any] = {}
        self.metadata: dict[str, Any] = {}
        self.primary_name: str = "xgboost"
        self._load_all()

    def _load_all(self) -> None:
        """Attempt loading all known trained models from disk across v1 and v2 directories."""
        # 1. Load Primary (v1) Models
        xgb_path = self.saved_dir / "xgboost.joblib"
        if xgb_path.exists():
            try:
                model, meta = load_model(xgb_path)
                self.models["xgboost"] = model
                self.models["xgboost_v1"] = model
                self.metadata["xgboost"] = meta
                self.metadata["xgboost_v1"] = meta
                logger.info("Loaded XGBoost model from %s", xgb_path)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                logger.warning("Could not load XGBoost model from %s: %s", xgb_path, exc)

        lr_path = self.saved_dir / "logisticregression.joblib"
        if lr_path.exists():
            try:
                model, meta = load_model(lr_path)
                self.models["logistic_regression"] = model
                self.models["logistic_regression_v1"] = model
                self.metadata["logistic_regression"] = meta
                self.metadata["logistic_regression_v1"] = meta
                logger.info("Loaded LogisticRegression model from %s", lr_path)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                logger.warning("Could not load LogisticRegression model from %s: %s", lr_path, exc)

        rule_path = self.saved_dir / "rulebaseline_metadata.json"
        if rule_path.exists():
            try:
                model, meta = load_model(rule_path)
                self.models["rule_baseline"] = model
                self.models["rule_baseline_v1"] = model
                self.metadata["rule_baseline"] = meta
                self.metadata["rule_baseline_v1"] = meta
                logger.info("Loaded RuleBaseline from %s", rule_path)
            except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
                logger.warning("Could not load RuleBaseline from %s: %s", rule_path, exc)
        else:
            self.models["rule_baseline"] = RuleBaseline()
            self.models["rule_baseline_v1"] = self.models["rule_baseline"]
            self.metadata["rule_baseline"] = {
                "model_name": "RuleBasedBaseline",
                "model_version": "1.0.0",
                "threshold": 50.0,
            }
            self.metadata["rule_baseline_v1"] = self.metadata["rule_baseline"]

        # 2. Optionally Load v2 Models if present
        if self.v2_dir.exists():
            v2_xgb = self.v2_dir / "xgboost.joblib"
            if v2_xgb.exists():
                try:
                    m2, meta2 = load_model(v2_xgb)
                    self.models["xgboost_v2"] = m2
                    self.metadata["xgboost_v2"] = meta2
                    logger.info("Loaded v2 XGBoost model from %s", v2_xgb)
                except Exception as exc:
                    logger.warning("Could not load v2 XGBoost model from %s: %s", v2_xgb, exc)

            v2_lr = self.v2_dir / "logisticregression.joblib"
            if v2_lr.exists():
                try:
                    m2, meta2 = load_model(v2_lr)
                    self.models["logistic_regression_v2"] = m2
                    self.metadata["logistic_regression_v2"] = meta2
                    logger.info("Loaded v2 LogisticRegression model from %s", v2_lr)
                except Exception as exc:
                    logger.warning("Could not load v2 LogisticRegression model from %s: %s", v2_lr, exc)

        # 3. Configure primary default model based on default_version
        if self.default_version == "v2" and "xgboost_v2" in self.models:
            self.primary_name = "xgboost_v2"
        elif "xgboost" in self.models:
            self.primary_name = "xgboost"
        elif "logistic_regression" in self.models:
            self.primary_name = "logistic_regression"
        else:
            self.primary_name = "rule_baseline"

    def get_model(
        self, name: str | None = None, version: str | None = None
    ) -> tuple[Any, dict[str, Any]]:
        """Retrieve a loaded model and its companion metadata.

        Args:
            name: Model name ('xgboost', 'logistic_regression', 'rule_baseline') or None for primary.
            version: Model version ('v1', 'v2', etc.) or None for registry default.

        Returns:
            Tuple of (model_instance, metadata_dict).

        Raises:
            KeyError: If requested model is not available.
        """
        target = (name or self.primary_name).strip().lower().replace("-", "_")
        req_version = (version or "").strip().lower()

        # Handle versioned aliases
        if req_version == "v2" or target.endswith("_v2"):
            base = target.removesuffix("_v2")
            if base in ("logistic", "logisticregression", "lr"):
                target = "logistic_regression_v2"
            elif base in ("xgb", "xgboostmodel", "xgboost"):
                target = "xgboost_v2"
            elif base in ("baseline", "rule", "rulebased", "rule_baseline"):
                target = "rule_baseline_v2"
        elif req_version == "v1" or target.endswith("_v1"):
            base = target.removesuffix("_v1")
            if base in ("logistic", "logisticregression", "lr"):
                target = "logistic_regression"
            elif base in ("xgb", "xgboostmodel", "xgboost"):
                target = "xgboost"
            elif base in ("baseline", "rule", "rulebased", "rule_baseline"):
                target = "rule_baseline"
        else:
            if target in ("logistic", "logisticregression", "lr"):
                target = "logistic_regression"
            elif target in ("xgb", "xgboostmodel"):
                target = "xgboost"
            elif target in ("baseline", "rule", "rulebased"):
                target = "rule_baseline"

        if target not in self.models:
            available = list(self.models.keys())
            raise KeyError(f"Model '{name}' (version: '{version}') not found. Available models: {available}")

        return self.models[target], self.metadata.get(target, {})

    def get_status(self) -> dict[str, Any]:
        """Return summary of model registry status."""
        return {
            "primary_model": self.primary_name,
            "available_models": list(self.models.keys()),
            "loaded_count": len(self.models),
            "details": {
                name: {
                    "version": meta.get("model_version", "1.0.0"),
                    "type": meta.get("model_name", type(self.models[name]).__name__),
                }
                for name, meta in self.metadata.items()
            },
        }


# Global Singletons
_config: AppConfig | None = None
_db: Database | None = None
_model_registry: ModelRegistry | None = None
_feature_engine: FeatureEngine | None = None
_decision_engine: DecisionEngine | None = None


def get_config() -> AppConfig:
    """FastAPI dependency for application configuration."""
    global _config
    if _config is None:
        _config = load_config()
    return _config


def get_db() -> Database:
    """FastAPI dependency for database operations."""
    global _db
    if _db is None:
        cfg = get_config()
        _db = Database(
            db_path=cfg.database.sqlite_path,
            timeout_seconds=cfg.database.timeout_seconds,
            enable_wal=cfg.database.enable_wal,
        )
        _db.init_schema()
    return _db


def get_model_registry() -> ModelRegistry:
    """FastAPI dependency for model registry."""
    global _model_registry
    if _model_registry is None:
        _model_registry = ModelRegistry()
    return _model_registry


def get_feature_engine() -> FeatureEngine:
    """FastAPI dependency for deterministic feature extraction."""
    global _feature_engine
    if _feature_engine is None:
        db = get_db()
        cfg = get_config()
        _feature_engine = FeatureEngine(
            db=db,
            window_seconds=cfg.features.window_seconds,
            feature_version=cfg.features.feature_version,
        )
    return _feature_engine


def get_decision_engine() -> DecisionEngine:
    """FastAPI dependency for M7 decision engine."""
    global _decision_engine
    if _decision_engine is None:
        db = get_db()
        cfg = get_config()
        _decision_engine = DecisionEngine(config=cfg.risk, db=db)
    return _decision_engine


def override_dependencies(
    db: Database | None = None,
    registry: ModelRegistry | None = None,
    engine: DecisionEngine | None = None,
) -> None:
    """Testing helper to inject mock/temporary instances."""
    global _db, _model_registry, _decision_engine, _feature_engine
    if db is not None:
        _db = db
        cfg = get_config()
        _feature_engine = FeatureEngine(db=db)
        _decision_engine = engine if engine is not None else DecisionEngine(config=cfg.risk, db=db)
    elif engine is not None:
        _decision_engine = engine
    if registry is not None:
        _model_registry = registry


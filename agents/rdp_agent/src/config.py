"""
Configuration Loader
=====================

Handles loading and merging configuration from YAML or JSON files.
Provides sensible defaults for scoring weights, severity ordering,
and logging when no configuration file is supplied.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("rdp_agent.config")


# ---------------------------------------------------------------------------
# Default Configuration
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    "weights": {
        "complexity_weight": 0.2,
        "risk_weight": 0.4,
        "impact_weight": 0.4,
    },
    "severity_order": {
        "critical": 4,
        "high": 3,
        "medium": 2,
        "low": 1,
    },
    "ml_scoring": {
        "enabled": True,
        "model_name": "microsoft/codebert-base",
        "ml_prediction_weight": 0.25,
    },
    "log_level": "INFO",
}


# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------


def setup_logging(level: str = "INFO") -> None:
    """Configure logging for the RDP Agent.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Config Loader
# ---------------------------------------------------------------------------


def load_config(config_path: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration from a YAML or JSON file.

    Args:
        config_path: Path to the configuration file. If ``None``, returns
                     default configuration values.

    Returns:
        Configuration dictionary with keys: ``weights``, ``severity_order``,
        ``log_level``.
    """
    defaults = {**DEFAULT_CONFIG}

    if config_path is None:
        return defaults

    if not os.path.isfile(config_path):
        logger.warning(
            "Config file '%s' not found; using defaults.", config_path
        )
        return defaults

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            if config_path.endswith((".yaml", ".yml")):
                try:
                    import yaml  # type: ignore[import-untyped]

                    user_config = yaml.safe_load(f) or {}
                except ImportError:
                    logger.warning(
                        "PyYAML not installed; cannot read YAML config. "
                        "Using defaults."
                    )
                    return defaults
            else:
                user_config = json.load(f)

        # Merge with defaults (nested merge for dicts)
        merged = {**defaults, **user_config}
        if "weights" in user_config:
            merged["weights"] = {
                **defaults["weights"],
                **user_config["weights"],
            }
        if "severity_order" in user_config:
            merged["severity_order"] = {
                **defaults["severity_order"],
                **user_config["severity_order"],
            }
        if "ml_scoring" in user_config:
            merged["ml_scoring"] = {
                **defaults["ml_scoring"],
                **user_config["ml_scoring"],
            }
        return merged

    except Exception as exc:
        logger.error(
            "Error loading config '%s': %s. Using defaults.",
            config_path,
            exc,
        )
        return defaults

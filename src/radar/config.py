"""Runtime configuration, sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Optional


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass
class Settings:
    db_path: str = "radar.db"
    host: str = "0.0.0.0"
    port: int = 8080
    tolerance_pct: float = 2.0
    price_drift_pct: float = 2.0
    duplicate_window_days: int = 45
    api_token: str = ""

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.environ.get("RADAR_DB_PATH", "radar.db"),
            host=os.environ.get("RADAR_HOST", "0.0.0.0"),
            port=_int_env("RADAR_PORT", 8080),
            tolerance_pct=_float_env("RADAR_TOLERANCE_PCT", 2.0),
            price_drift_pct=_float_env("RADAR_PRICE_DRIFT_PCT", 2.0),
            duplicate_window_days=_int_env("RADAR_DUPLICATE_WINDOW_DAYS", 45),
            api_token=os.environ.get("RADAR_API_TOKEN", ""),
        )


RULE_SETTINGS_FIELDS = ("tolerance_pct", "price_drift_pct", "duplicate_window_days")


def effective_settings(base: Settings, rule: Optional[dict]) -> Settings:
    """Overlay the non-``None`` fields of a supplier rule onto ``base``.

    ``min_amount_eur`` is intentionally not part of :class:`Settings`; it is
    handled separately by the reconciliation engine.
    """
    if not rule:
        return replace(base)
    overrides = {
        name: rule[name] for name in RULE_SETTINGS_FIELDS if rule.get(name) is not None
    }
    return replace(base, **overrides)


def effective_rule(base: Settings, rule: Optional[dict]) -> dict:
    """Return the effective thresholds with the origin of each value.

    Every entry is ``{"value": ..., "source": "supplier" | "global"}`` so the
    audit view can show transparently which supplier override took effect.
    """
    rule = rule or {}
    result: dict = {}
    for name in RULE_SETTINGS_FIELDS:
        value = rule.get(name)
        if value is None:
            result[name] = {"value": getattr(base, name), "source": "global"}
        else:
            result[name] = {"value": value, "source": "supplier"}
    amount = rule.get("min_amount_eur")
    if amount is None:
        result["min_amount_eur"] = {"value": 0.0, "source": "global"}
    else:
        result["min_amount_eur"] = {"value": float(amount), "source": "supplier"}
    return result

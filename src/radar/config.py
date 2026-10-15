"""Runtime configuration, sourced from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


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

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


def _non_negative_float_env(name: str, default: float) -> float:
    """Parse a float env var, falling back to ``default``.

    Values that are not numeric or that are negative are rejected and the
    default is used instead, so a typo cannot silently distort the ROI math.
    """
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    db_path: str = "radar.db"
    host: str = "0.0.0.0"
    port: int = 8080
    tolerance_pct: float = 2.0
    price_drift_pct: float = 2.0
    duplicate_window_days: int = 45
    api_token: str = ""
    digest_webhook_url: str = ""
    digest_smtp_host: str = ""
    digest_smtp_port: int = 587
    digest_smtp_user: str = ""
    digest_smtp_password: str = ""
    digest_smtp_from: str = ""
    digest_smtp_to: str = ""
    digest_smtp_use_tls: bool = True
    digest_schedule: str = ""
    review_minutes_per_finding: float = 4.0
    review_hourly_rate_eur: float = 45.0

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
            digest_webhook_url=os.environ.get("RADAR_DIGEST_WEBHOOK_URL", ""),
            digest_smtp_host=os.environ.get("RADAR_DIGEST_SMTP_HOST", ""),
            digest_smtp_port=_int_env("RADAR_DIGEST_SMTP_PORT", 587),
            digest_smtp_user=os.environ.get("RADAR_DIGEST_SMTP_USER", ""),
            digest_smtp_password=os.environ.get("RADAR_DIGEST_SMTP_PASSWORD", ""),
            digest_smtp_from=os.environ.get("RADAR_DIGEST_SMTP_FROM", ""),
            digest_smtp_to=os.environ.get("RADAR_DIGEST_SMTP_TO", ""),
            digest_smtp_use_tls=_bool_env("RADAR_DIGEST_SMTP_USE_TLS", True),
            digest_schedule=os.environ.get("RADAR_DIGEST_SCHEDULE", ""),
            review_minutes_per_finding=_non_negative_float_env(
                "RADAR_REVIEW_MINUTES_PER_FINDING", 4.0
            ),
            review_hourly_rate_eur=_non_negative_float_env(
                "RADAR_REVIEW_HOURLY_RATE_EUR", 45.0
            ),
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

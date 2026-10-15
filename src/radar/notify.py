"""Active delivery of the weekly digest via webhook and SMTP.

Everything here relies on the standard library only (``urllib.request``,
``smtplib``, ``email``), keeping the service dependency-free and cheap to
operate. Delivery is best-effort per channel: a failure in one channel never
prevents the other from being attempted.
"""

from __future__ import annotations

import json
import smtplib
import urllib.request
from email.message import EmailMessage
from typing import Callable, Dict, List, Optional

from .config import Settings
from .db import Store
from .digest import render_html, weekly_digest

WEBHOOK_SUBJECT = "Rechnungs-Abgleich-Radar — Wochen-Digest"
WEBHOOK_TIMEOUT_SECONDS = 10
SMTP_TIMEOUT_SECONDS = 10
TOP_FINDINGS = 5


def _webhook_payload(digest: dict) -> dict:
    """Build a compact, Slack/Mattermost-compatible JSON message."""
    findings = digest.get("findings", [])[:TOP_FINDINGS]
    top = [
        {
            "type": finding.get("type_label") or finding.get("finding_type"),
            "message": finding.get("message"),
            "amount": round(float(finding.get("amount", 0)), 2),
            "reference": finding.get("reference_value"),
        }
        for finding in findings
    ]
    total = round(float(digest.get("total_amount", 0)), 2)
    count = int(digest.get("finding_count", 0))
    entities = list(digest.get("entities") or [])
    scope = ", ".join(str(entity) for entity in entities) or "alle Gesellschaften"
    cost_centers = list(digest.get("cost_centers") or [])
    if cost_centers:
        scope += " · Kostenstelle(n): " + ", ".join(
            str(cost_center) for cost_center in cost_centers
        )
    text = (
        "%s (%s): %d offene Auffälligkeiten, %.2f EUR potenzielles "
        "Abweichungsvolumen." % (WEBHOOK_SUBJECT, scope, count, total)
    )
    return {
        "text": text,
        "total_amount": total,
        "finding_count": count,
        "entities": entities,
        "cost_centers": cost_centers,
        "by_entity": list(digest.get("by_entity") or []),
        "by_cost_center": list(digest.get("by_cost_center") or []),
        "by_supplier": list(digest.get("by_supplier") or []),
        "top_findings": top,
    }


def send_webhook(url: str, digest: dict) -> dict:
    """POST the compact digest message to ``url``.

    Returns a channel result ``{"channel", "status", "detail"}``. Network and
    HTTP errors are captured as ``status: "error"`` rather than raised so the
    caller can keep delivering other channels.
    """
    data = json.dumps(_webhook_payload(digest), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=WEBHOOK_TIMEOUT_SECONDS) as response:
            status = getattr(response, "status", None) or response.getcode()
        return {"channel": "webhook", "status": "sent", "detail": "HTTP %s" % status}
    except Exception as exc:  # noqa: BLE001 - report, never block other channels
        return {"channel": "webhook", "status": "error", "detail": str(exc)}


def _build_email(settings: Settings, digest: dict) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = WEBHOOK_SUBJECT
    message["From"] = settings.digest_smtp_from or settings.digest_smtp_user
    message["To"] = settings.digest_smtp_to
    message.set_content(
        "Der Wochen-Digest ist als HTML verfügbar. "
        "Bitte eine HTML-fähige Mail-App verwenden."
    )
    message.add_alternative(render_html(digest), subtype="html")
    return message


def send_email(settings: Settings, digest: dict) -> dict:
    """Send the HTML digest to ``digest_smtp_to`` over SMTP.

    Returns a channel result ``{"channel", "status", "detail"}``. SMTP errors
    are captured as ``status: "error"`` rather than raised.
    """
    message = _build_email(settings, digest)
    try:
        with smtplib.SMTP(
            settings.digest_smtp_host,
            settings.digest_smtp_port,
            timeout=SMTP_TIMEOUT_SECONDS,
        ) as smtp:
            if settings.digest_smtp_use_tls:
                smtp.starttls()
            if settings.digest_smtp_user:
                smtp.login(settings.digest_smtp_user, settings.digest_smtp_password)
            smtp.send_message(message)
        return {
            "channel": "email",
            "status": "sent",
            "detail": "sent to %s" % settings.digest_smtp_to,
        }
    except Exception as exc:  # noqa: BLE001 - report, never block other channels
        return {"channel": "email", "status": "error", "detail": str(exc)}


def _deliver(
    channel: str, configured: bool, skipped_detail: str, sender: Callable[[], dict]
) -> dict:
    if not configured:
        return {"channel": channel, "status": "skipped", "detail": skipped_detail}
    try:
        return sender()
    except Exception as exc:  # noqa: BLE001 - defensive isolation
        return {"channel": channel, "status": "error", "detail": str(exc)}


def deliver_digest(
    store: Store,
    settings: Settings,
    entities: Optional[List[str]] = None,
    cost_centers: Optional[List[str]] = None,
) -> dict:
    """Deliver the current weekly digest over every configured channel.

    ``entities`` optionally restricts the digest (and the webhook payload) to
    the selected societies; ``cost_centers`` restricts it to the selected cost
    centers (AND-combined with ``entities``). Always returns both channel
    entries. Unconfigured channels are reported as ``skipped``; a failure in
    one channel is isolated from the other.
    """
    digest = weekly_digest(store, entities=entities, cost_centers=cost_centers)

    webhook_configured = bool(settings.digest_webhook_url)
    email_configured = bool(settings.digest_smtp_host and settings.digest_smtp_to)

    channels: List[Dict[str, object]] = [
        _deliver(
            "webhook",
            webhook_configured,
            "digest_webhook_url not configured",
            lambda: send_webhook(settings.digest_webhook_url, digest),
        ),
        _deliver(
            "email",
            email_configured,
            "digest_smtp_host/digest_smtp_to not configured",
            lambda: send_email(settings, digest),
        ),
    ]
    return {"channels": channels}

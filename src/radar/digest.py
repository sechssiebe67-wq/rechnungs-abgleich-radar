"""Weekly digest: HTML and JSON views of the highest-impact open findings."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import List

from .db import Store
from .models import FINDING_LABELS


def weekly_digest(store: Store, limit: int = 100) -> dict:
    findings = store.list_findings(status="open")
    findings = findings[:limit]
    total = round(sum(float(f["amount"]) for f in findings), 2)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": 7,
        "total_amount": total,
        "finding_count": len(findings),
        "stats": store.stats(),
        "findings": [_decorate(f) for f in findings],
    }


def _decorate(finding: dict) -> dict:
    finding = dict(finding)
    finding["type_label"] = FINDING_LABELS.get(
        finding["finding_type"], finding["finding_type"]
    )
    return finding


def render_html(digest: dict) -> str:
    rows: List[str] = []
    for finding in digest["findings"]:
        rows.append(
            "<tr>"
            "<td>{type}</td>"
            "<td>{message}</td>"
            "<td style='text-align:right'>{amount:.2f} EUR</td>"
            "<td>{reference}</td>"
            "</tr>".format(
                type=html.escape(finding["type_label"]),
                message=html.escape(finding["message"]),
                amount=float(finding["amount"]),
                reference=html.escape(str(finding.get("reference_value") or "")),
            )
        )
    body = "\n".join(rows) or "<tr><td colspan='4'>Keine offenen Auffälligkeiten.</td></tr>"
    stats = digest["stats"]
    precision = stats.get("precision")
    precision_text = "n/a" if precision is None else "%.0f%%" % (precision * 100)
    return """<!doctype html>
<html lang="de">
<head><meta charset="utf-8"><title>Rechnungs-Abgleich-Radar — Wochen-Digest</title></head>
<body style="font-family:sans-serif;max-width:900px;margin:2rem auto">
<h1>Wochen-Digest — Rechnungs-Abgleich-Radar</h1>
<p>Erstellt: {generated_at}. Potenzielles Abweichungsvolumen:
<strong>{total:.2f} EUR</strong> aus {count} Auffälligkeiten.</p>
<p>Rechnungen: {invoices} · Findings: {findings} · Precision (Feedback):
{precision}</p>
<table border="1" cellpadding="6" cellspacing="0" width="100%">
<thead><tr><th>Art</th><th>Begründung</th><th>Betrag</th><th>Referenz</th></tr></thead>
<tbody>
{body}
</tbody></table>
<p style="color:#666;font-size:0.85em">Automatisch erzeugt vom
Rechnungs-Abgleich-Radar. Zahlungsausführung ist nicht Teil dieses Systems.</p>
</body></html>
""".format(
        generated_at=html.escape(digest["generated_at"]),
        total=float(digest["total_amount"]),
        count=digest["finding_count"],
        invoices=stats.get("invoices", 0),
        findings=stats.get("findings", 0),
        precision=precision_text,
        body=body,
    )

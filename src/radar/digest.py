"""Weekly digest: HTML and JSON views of the highest-impact open findings."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import List, Optional

from .db import Store
from .models import FINDING_LABELS


def weekly_digest(
    store: Store,
    limit: int = 100,
    entities: Optional[List[str]] = None,
    cost_centers: Optional[List[str]] = None,
) -> dict:
    """Build the weekly digest, optionally restricted to selected societies.

    When ``entities`` is given only findings of those invoices are included and
    the response lists exactly those societies under ``entities``. Without a
    filter all societies occurring in the invoices are reported so the JSON,
    HTML, webhook and e-mail views share one consolidated figure.

    ``cost_centers`` mirrors that behaviour for the invoice cost center and is
    AND-combined with ``entities``: an invoice must belong to one of the chosen
    societies *and* one of the chosen cost centers. Unknown values are not an
    error; they simply yield no findings. The considered cost centers are
    reported under ``cost_centers`` (sorted, deduplicated).

    In addition to the flat ``total_amount``/``finding_count`` the response
    carries ``by_entity`` and ``by_cost_center``: one row per society resp. cost
    center with the number of findings and the summed open amount, computed over
    exactly the findings included here (same filters, same ``limit``). Both lists
    are sorted by ``open_amount`` descending, then by the group key ascending, so
    ``sum(open_amount) == total_amount`` and ``sum(findings) == finding_count``.
    """
    findings = store.list_findings(
        status="open", entities=entities, cost_centers=cost_centers
    )
    findings = findings[:limit]
    total = round(sum(float(f["amount"]) for f in findings), 2)
    if entities:
        considered = sorted({str(entity) for entity in entities})
    else:
        considered = store.list_entities()
    if cost_centers:
        considered_cost_centers = sorted({str(cc) for cc in cost_centers})
    else:
        considered_cost_centers = store.list_cost_centers()
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": 7,
        "total_amount": total,
        "finding_count": len(findings),
        "entities": considered,
        "cost_centers": considered_cost_centers,
        "by_entity": _breakdown(findings, "entity"),
        "by_cost_center": _breakdown(findings, "cost_center"),
        "stats": store.stats(),
        "findings": [_decorate(f) for f in findings],
    }


def _breakdown(findings: List[dict], key_name: str) -> List[dict]:
    """Group the exact digest findings by society or cost center.

    Returns one row per group with ``findings`` and ``open_amount``, sorted by
    ``open_amount`` descending and then by the group key ascending (deterministic
    for ties). The empty cost center is reported as ``""``; a missing entity
    falls back to ``default``, mirroring the ``/api/stats`` semantics. Because
    every finding belongs to exactly one group, the sums over the rows equal
    ``finding_count`` and ``total_amount`` of the digest.
    """
    default_key = "default" if key_name == "entity" else ""
    groups: dict = {}
    for finding in findings:
        key = finding.get(key_name)
        if key is None:
            key = default_key
        key = str(key)
        row = groups.get(key)
        if row is None:
            row = {key_name: key, "findings": 0, "open_amount": 0.0}
            groups[key] = row
        row["findings"] += 1
        row["open_amount"] += float(finding.get("amount", 0) or 0)
    rows = list(groups.values())
    for row in rows:
        row["open_amount"] = round(row["open_amount"], 2)
    rows.sort(key=lambda row: (-row["open_amount"], row[key_name]))
    return rows


def _decorate(finding: dict) -> dict:
    finding = dict(finding)
    finding["type_label"] = FINDING_LABELS.get(
        finding["finding_type"], finding["finding_type"]
    )
    return finding


def _breakdown_html(digest: dict) -> str:
    """Render the society/cost-center breakdown tables (omit empty blocks)."""
    blocks: List[str] = []
    for title, field, key_name, label in (
        ("Gesellschaften", "by_entity", "entity", "Gesellschaft"),
        ("Kostenstellen", "by_cost_center", "cost_center", "Kostenstelle"),
    ):
        rows = digest.get(field) or []
        if not rows:
            continue
        body = "\n".join(
            "<tr><td>{key}</td><td style='text-align:right'>{findings}</td>"
            "<td style='text-align:right'>{amount:.2f} EUR</td></tr>".format(
                key=html.escape(str(row.get(key_name) or "")),
                findings=int(row.get("findings", 0)),
                amount=float(row.get("open_amount", 0) or 0),
            )
            for row in rows
        )
        blocks.append(
            "<h3>{title}</h3>\n"
            "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"100%\">\n"
            "<thead><tr><th>{label}</th><th>Findings</th>"
            "<th>Offenes Volumen</th></tr></thead>\n"
            "<tbody>\n{body}\n</tbody></table>".format(
                title=html.escape(title),
                label=html.escape(label),
                body=body,
            )
        )
    return "\n".join(blocks)


def render_html(digest: dict) -> str:
    rows: List[str] = []
    for finding in digest["findings"]:
        rows.append(
            "<tr>"
            "<td>{entity}</td>"
            "<td>{type}</td>"
            "<td>{message}</td>"
            "<td style='text-align:right'>{amount:.2f} EUR</td>"
            "<td>{reference}</td>"
            "</tr>".format(
                entity=html.escape(str(finding.get("entity") or "")),
                type=html.escape(finding["type_label"]),
                message=html.escape(finding["message"]),
                amount=float(finding["amount"]),
                reference=html.escape(str(finding.get("reference_value") or "")),
            )
        )
    body = (
        "\n".join(rows)
        or "<tr><td colspan='5'>Keine offenen Auffälligkeiten.</td></tr>"
    )
    entities = digest.get("entities") or []
    entities_text = html.escape(", ".join(str(entity) for entity in entities)) or "alle"
    cost_centers = digest.get("cost_centers") or []
    cost_centers_line = ""
    if cost_centers:
        cost_centers_text = html.escape(
            ", ".join(str(cost_center) for cost_center in cost_centers)
        )
        cost_centers_line = (
            "<p>Kostenstelle(n): <strong>%s</strong></p>" % cost_centers_text
        )
    stats = digest["stats"]
    precision = stats.get("precision")
    precision_text = "n/a" if precision is None else "%.0f%%" % (precision * 100)
    breakdown = _breakdown_html(digest)
    return """<!doctype html>
<html lang="de">
<head><meta charset="utf-8"><title>Rechnungs-Abgleich-Radar — Wochen-Digest</title></head>
<body style="font-family:sans-serif;max-width:900px;margin:2rem auto">
<h1>Wochen-Digest — Rechnungs-Abgleich-Radar</h1>
<p>Gesellschaft(en): <strong>{entities}</strong></p>
{cost_centers_line}
<p>Erstellt: {generated_at}. Potenzielles Abweichungsvolumen:
<strong>{total:.2f} EUR</strong> aus {count} Auffälligkeiten.</p>
<p>Rechnungen: {invoices} · Findings: {findings} · Precision (Feedback):
{precision}</p>
{breakdown}
<table border="1" cellpadding="6" cellspacing="0" width="100%">
<thead><tr><th>Gesellschaft</th><th>Art</th><th>Begründung</th><th>Betrag</th><th>Referenz</th></tr></thead>
<tbody>
{body}
</tbody></table>
<p style="color:#666;font-size:0.85em">Automatisch erzeugt vom
Rechnungs-Abgleich-Radar. Zahlungsausführung ist nicht Teil dieses Systems.</p>
</body></html>
""".format(
        entities=entities_text,
        cost_centers_line=cost_centers_line,
        generated_at=html.escape(digest["generated_at"]),
        total=float(digest["total_amount"]),
        count=digest["finding_count"],
        invoices=stats.get("invoices", 0),
        findings=stats.get("findings", 0),
        precision=precision_text,
        breakdown=breakdown,
        body=body,
    )

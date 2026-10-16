"""Weekly digest: HTML and JSON views of the highest-impact open findings."""

from __future__ import annotations

import html
from datetime import datetime, timezone
from typing import List, Optional

from .config import Settings
from .db import Store
from .models import FINDING_LABELS


def _empty_rollup_totals(*key_names: str) -> dict:
    """Return a zeroed ``totals`` row for the given group keys."""
    totals = {key_name: None for key_name in key_names}
    totals.update(
        {
            "open_finding_count": 0,
            "open_amount": 0.0,
            "open_review_minutes": 0.0,
            "open_review_cost_eur": 0.0,
            "confirmed": 0,
            "false_alarm": 0,
            "precision": None,
            "avoided_review_cost_eur": 0.0,
            "avoided_mispayment_eur": 0.0,
        }
    )
    return totals


def empty_rollup(key_name: str = "entity") -> dict:
    """Return a well-formed, zeroed ``by_entity_rollup`` block.

    ``key_name`` selects the group key of the ``totals`` row (``entity`` for the
    per-society rollup, ``supplier`` for the per-supplier rollup), so both blocks
    share one zeroed shape.
    """
    return {"entities": [], "totals": _empty_rollup_totals(key_name)}


def empty_supplier_rollup() -> dict:
    """Return a well-formed, zeroed ``by_supplier_rollup`` block."""
    return empty_rollup("supplier")


def empty_cost_center_rollup() -> dict:
    """Return a well-formed, zeroed ``by_cost_center_rollup`` block.

    The block groups by ``(entity, cost_center)``, so its ``totals`` row carries
    both keys set to ``None`` (unlike the single-key entity/supplier rollups).
    """
    return {
        "cost_centers": [],
        "totals": _empty_rollup_totals("entity", "cost_center"),
    }


def empty_supplier_backlog_trend() -> dict:
    """Return a well-formed, zeroed ``by_supplier_backlog_trend`` block."""
    totals = {
        "supplier": None,
        "latest_month": None,
        "latest_open_amount": 0.0,
        "previous_month": None,
        "previous_open_amount": 0.0,
        "delta_open_amount": 0.0,
        "delta_open_finding_count": 0,
        "open_review_minutes": 0.0,
        "open_review_cost_eur": 0.0,
        "top_invoice": None,
    }
    return {"suppliers": [], "totals": totals}


def weekly_digest(
    store: Store,
    limit: int = 100,
    entities: Optional[List[str]] = None,
    cost_centers: Optional[List[str]] = None,
    settings: Optional[Settings] = None,
    review_minutes: Optional[float] = None,
    hourly_rate: Optional[float] = None,
    rollup: bool = True,
    trend_months: Optional[int] = None,
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
    carries ``by_entity``, ``by_cost_center`` and ``by_supplier``: one row per
    society resp. cost center resp. supplier with the number of findings and the
    summed open amount, computed over exactly the findings included here (same
    filters, same ``limit``). All lists are sorted by ``open_amount`` descending,
    then by the group key ascending, so ``sum(open_amount) == total_amount`` and
    ``sum(findings) == finding_count``. The supplier key is the invoice's
    ``supplier``; empty values stay ``""`` and the group is kept.

    When ``rollup`` is true (the default) the response additionally carries the
    consolidated management views ``by_entity_rollup``, ``by_supplier_rollup``
    and ``by_cost_center_rollup``: each is ``{"entities": [...], "totals": {...}}``
    (the cost-center block uses ``"cost_centers"`` as its list key) with, per
    society resp. supplier resp. ``(entity, cost_center)`` pair, the open review
    workload (``open_finding_count``, ``open_amount``, ``open_review_minutes``,
    ``open_review_cost_eur``) and the realized ROI (``confirmed``,
    ``false_alarm``, ``precision``, ``avoided_review_cost_eur``,
    ``avoided_mispayment_eur``), plus a ``totals`` row summing those fields over
    the reported groups. Unlike the count-only breakdown they span all invoices
    matching the filters (not just the digest ``limit``) so their ``open_amount``
    matches ``GET /api/stats``. ``by_supplier_rollup`` uses ``supplier`` as its
    group key and is sorted by ``open_amount`` descending, then
    ``open_finding_count`` descending, then ``supplier`` ascending.
    ``by_cost_center_rollup`` groups by ``(entity, cost_center)`` (empty cost
    centers stay ``""``) and is sorted by ``open_amount`` descending, then
    ``entity`` ascending, then ``cost_center`` ascending; its ``totals`` row
    carries ``entity is None`` and ``cost_center is None``. All blocks are gated
    by the same ``rollup`` switch; passing ``rollup=False`` omits them entirely
    to reproduce the pre-rollup payload.

    The same switch also gates ``by_supplier_backlog_trend``, the compact
    per-supplier time dimension derived from
    :meth:`Store.supplier_backlog_trend` (newest month vs. the previous month,
    ``delta_open_amount``, bound effort and the concentrating ``top_invoice``).
    ``trend_months`` forwards the look-back window (default ``2``, ``>= 2``).
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
    digest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "period_days": 7,
        "total_amount": total,
        "finding_count": len(findings),
        "entities": considered,
        "cost_centers": considered_cost_centers,
        "by_entity": _breakdown(findings, "entity"),
        "by_cost_center": _breakdown(findings, "cost_center"),
        "by_supplier": _breakdown(findings, "supplier"),
        "stats": store.stats(),
        "findings": [_decorate(f) for f in findings],
    }
    if rollup:
        digest["by_entity_rollup"] = store.entity_rollup(
            settings, entities, cost_centers, review_minutes, hourly_rate
        )
        digest["by_supplier_rollup"] = store.supplier_rollup(
            settings, entities, cost_centers, review_minutes, hourly_rate
        )
        digest["by_cost_center_rollup"] = store.cost_center_rollup(
            settings, entities, cost_centers, review_minutes, hourly_rate
        )
        digest["by_supplier_backlog_trend"] = store.supplier_backlog_trend(
            settings,
            entities,
            cost_centers,
            review_minutes,
            hourly_rate,
            trend_months,
        )
    return digest


def _breakdown(findings: List[dict], key_name: str) -> List[dict]:
    """Group the exact digest findings by society or cost center.

    Returns one row per group with ``findings`` and ``open_amount``, sorted by
    ``open_amount`` descending and then by the group key ascending (deterministic
    for ties). The empty cost center and supplier are reported as ``""``; a
    missing entity falls back to ``default``, mirroring the ``/api/stats``
    semantics. Because every finding belongs to exactly one group, the sums over
    the rows equal ``finding_count`` and ``total_amount`` of the digest.
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
    """Render the society/cost-center/supplier breakdown tables (omit empty blocks)."""
    blocks: List[str] = []
    for title, field, key_name, label in (
        ("Gesellschaften", "by_entity", "entity", "Gesellschaft"),
        ("Kostenstellen", "by_cost_center", "cost_center", "Kostenstelle"),
        ("Lieferanten", "by_supplier", "supplier", "Lieferant"),
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


def _rollup_html(digest: dict) -> str:
    """Render the per-society workload/ROI rollup with a totals row."""
    rollup = digest.get("by_entity_rollup") or {}
    rows = rollup.get("entities") or []
    totals = rollup.get("totals")
    if not rows:
        return ""

    def _number(value):
        if value is None:
            return "n/a"
        return "%.2f" % float(value)

    def _precision(value):
        if value is None:
            return "n/a"
        return "%.0f%%" % (float(value) * 100)

    body_rows = [(row, False) for row in rows]
    if totals is not None:
        body_rows.append((dict(totals, entity="Gesamt"), True))
    cells: List[str] = []
    for row, is_total in body_rows:
        label = html.escape(str(row.get("entity") or "Gesamt"))
        if is_total:
            label = "<strong>%s</strong>" % label
        cells.append(
            "<tr>"
            "<td>{entity}</td>"
            "<td style='text-align:right'>{open_count}</td>"
            "<td style='text-align:right'>{open_amount:.2f} EUR</td>"
            "<td style='text-align:right'>{open_cost} EUR</td>"
            "<td style='text-align:right'>{confirmed}</td>"
            "<td style='text-align:right'>{false_alarm}</td>"
            "<td style='text-align:right'>{precision}</td>"
            "<td style='text-align:right'>{avoided_cost} EUR</td>"
            "<td style='text-align:right'>{avoided_mis} EUR</td>"
            "</tr>".format(
                entity=label,
                open_count=int(row.get("open_finding_count", 0) or 0),
                open_amount=float(row.get("open_amount", 0) or 0),
                open_cost=_number(row.get("open_review_cost_eur")),
                confirmed=int(row.get("confirmed", 0) or 0),
                false_alarm=int(row.get("false_alarm", 0) or 0),
                precision=_precision(row.get("precision")),
                avoided_cost=_number(row.get("avoided_review_cost_eur")),
                avoided_mis=_number(row.get("avoided_mispayment_eur")),
            )
        )
    header = (
        "<tr><th>Gesellschaft</th><th>Offene Findings</th>"
        "<th>Offenes Volumen</th><th>Offener Aufwand (EUR)</th><th>Bestätigt</th>"
        "<th>Fehlalarm</th><th>Precision</th><th>Vermiedener Aufwand (EUR)</th>"
        "<th>Vermiedene Fehlzahlung (EUR)</th></tr>"
    )
    return (
        "<h3>Konsolidierter Gruppen-Rollup (offene Last und ROI je Gesellschaft)</h3>\n"
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"100%%\">\n"
        "<thead>%s</thead>\n<tbody>\n%s\n</tbody></table>"
        % (header, "\n".join(cells))
    )


def _supplier_rollup_html(digest: dict) -> str:
    """Render the per-supplier workload/ROI rollup with a totals row."""
    rollup = digest.get("by_supplier_rollup") or {}
    rows = rollup.get("entities") or []
    totals = rollup.get("totals")
    if not rows:
        return ""

    def _number(value):
        if value is None:
            return "n/a"
        return "%.2f" % float(value)

    def _precision(value):
        if value is None:
            return "n/a"
        return "%.0f%%" % (float(value) * 100)

    body_rows = [(row, False) for row in rows]
    if totals is not None:
        body_rows.append((dict(totals, supplier="Gesamt"), True))
    cells: List[str] = []
    for row, is_total in body_rows:
        label = html.escape(str(row.get("supplier") or "Gesamt"))
        if is_total:
            label = "<strong>%s</strong>" % label
        cells.append(
            "<tr>"
            "<td>{supplier}</td>"
            "<td style='text-align:right'>{open_count}</td>"
            "<td style='text-align:right'>{open_amount:.2f} EUR</td>"
            "<td style='text-align:right'>{open_minutes} min</td>"
            "<td style='text-align:right'>{open_cost} EUR</td>"
            "<td style='text-align:right'>{avoided_cost} EUR</td>"
            "<td style='text-align:right'>{avoided_mis} EUR</td>"
            "<td style='text-align:right'>{precision}</td>"
            "</tr>".format(
                supplier=label,
                open_count=int(row.get("open_finding_count", 0) or 0),
                open_amount=float(row.get("open_amount", 0) or 0),
                open_minutes=_number(row.get("open_review_minutes")),
                open_cost=_number(row.get("open_review_cost_eur")),
                avoided_cost=_number(row.get("avoided_review_cost_eur")),
                avoided_mis=_number(row.get("avoided_mispayment_eur")),
                precision=_precision(row.get("precision")),
            )
        )
    header = (
        "<tr><th>Lieferant</th><th>Offene Findings</th><th>Offenes Volumen</th>"
        "<th>Aufwand (min)</th><th>Aufwand (EUR)</th>"
        "<th>Vermiedener Aufwand (EUR)</th>"
        "<th>Vermiedene Fehlzahlung (EUR)</th><th>Precision</th></tr>"
    )
    return (
        "<h3>Lieferanten-Rollup (offene Last und ROI je Lieferant)</h3>\n"
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"100%%\">\n"
        "<thead>%s</thead>\n<tbody>\n%s\n</tbody></table>"
        % (header, "\n".join(cells))
    )


def _cost_center_rollup_html(digest: dict) -> str:
    """Render the per-(society, cost-center) workload/ROI rollup with totals."""
    rollup = digest.get("by_cost_center_rollup") or {}
    rows = rollup.get("cost_centers") or []
    totals = rollup.get("totals")
    if not rows:
        return ""

    def _number(value):
        if value is None:
            return "n/a"
        return "%.2f" % float(value)

    def _precision(value):
        if value is None:
            return "n/a"
        return "%.0f%%" % (float(value) * 100)

    def _cost_center(row, is_total):
        if is_total:
            return "Gesamt"
        return str(row.get("cost_center") or "")

    body_rows = [(row, False) for row in rows]
    if totals is not None:
        body_rows.append((dict(totals, entity="Gesamt"), True))
    cells: List[str] = []
    for row, is_total in body_rows:
        entity_label = html.escape(str(row.get("entity") or "Gesamt"))
        if is_total:
            entity_label = "<strong>%s</strong>" % entity_label
        cells.append(
            "<tr>"
            "<td>{entity}</td>"
            "<td>{cost_center}</td>"
            "<td style='text-align:right'>{open_count}</td>"
            "<td style='text-align:right'>{open_amount:.2f} EUR</td>"
            "<td style='text-align:right'>{open_cost} EUR</td>"
            "<td style='text-align:right'>{confirmed}</td>"
            "<td style='text-align:right'>{false_alarm}</td>"
            "<td style='text-align:right'>{precision}</td>"
            "<td style='text-align:right'>{avoided_cost} EUR</td>"
            "<td style='text-align:right'>{avoided_mis} EUR</td>"
            "</tr>".format(
                entity=entity_label,
                cost_center=html.escape(_cost_center(row, is_total)),
                open_count=int(row.get("open_finding_count", 0) or 0),
                open_amount=float(row.get("open_amount", 0) or 0),
                open_cost=_number(row.get("open_review_cost_eur")),
                confirmed=int(row.get("confirmed", 0) or 0),
                false_alarm=int(row.get("false_alarm", 0) or 0),
                precision=_precision(row.get("precision")),
                avoided_cost=_number(row.get("avoided_review_cost_eur")),
                avoided_mis=_number(row.get("avoided_mispayment_eur")),
            )
        )
    header = (
        "<tr><th>Gesellschaft</th><th>Kostenstelle</th><th>Offene Findings</th>"
        "<th>Offenes Volumen</th><th>Offener Aufwand (EUR)</th><th>Bestätigt</th>"
        "<th>Fehlalarm</th><th>Precision</th><th>Vermiedener Aufwand (EUR)</th>"
        "<th>Vermiedene Fehlzahlung (EUR)</th></tr>"
    )
    return (
        "<h3>Kostenstellen-Rollup (offene Last und ROI je Gesellschaft und "
        "Kostenstelle)</h3>\n"
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"100%%\">\n"
        "<thead>%s</thead>\n<tbody>\n%s\n</tbody></table>"
        % (header, "\n".join(cells))
    )


def _top_invoice_label(invoice: Optional[dict]) -> str:
    """Render a short, escaped label for a ``top_invoice`` (or ``—``)."""
    if not invoice:
        return "—"
    number = invoice.get("invoice_number")
    parts = [str(number)] if number else []
    if invoice.get("entity"):
        parts.append(str(invoice["entity"]))
    label = " · ".join(parts) or ("#%s" % invoice.get("id"))
    return html.escape(label)


def _supplier_backlog_trend_html(digest: dict) -> str:
    """Render the per-supplier backlog trend with a totals row."""
    block = digest.get("by_supplier_backlog_trend") or {}
    rows = block.get("suppliers") or []
    totals = block.get("totals")
    if not rows:
        return ""

    def _number(value):
        if value is None:
            return "n/a"
        return "%.2f" % float(value)

    body_rows = [(row, False) for row in rows]
    if totals is not None:
        body_rows.append((dict(totals, supplier="Gesamt"), True))
    cells: List[str] = []
    for row, is_total in body_rows:
        label = html.escape(str(row.get("supplier") or "Gesamt"))
        if is_total:
            label = "<strong>%s</strong>" % label
        cells.append(
            "<tr>"
            "<td>{supplier}</td>"
            "<td>{latest_month}</td>"
            "<td style='text-align:right'>{delta:.2f} EUR</td>"
            "<td style='text-align:right'>{cost} EUR</td>"
            "<td>{top_invoice}</td>"
            "</tr>".format(
                supplier=label,
                latest_month=html.escape(str(row.get("latest_month") or "—")),
                delta=float(row.get("delta_open_amount", 0) or 0),
                cost=_number(row.get("open_review_cost_eur")),
                top_invoice=_top_invoice_label(row.get("top_invoice")),
            )
        )
    header = (
        "<tr><th>Lieferant</th><th>Neuester Monat</th>"
        "<th>Delta offenes Volumen</th><th>Offener Aufwand (EUR)</th>"
        "<th>Top-Rechnung</th></tr>"
    )
    return (
        "<h3>Lieferanten-Backlog-Trend (neuester Monat vs. Vormonat)</h3>\n"
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"100%%\">\n"
        "<thead>%s</thead>\n<tbody>\n%s\n</tbody></table>"
        % (header, "\n".join(cells))
    )


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
    rollup = _rollup_html(digest)
    supplier_rollup = _supplier_rollup_html(digest)
    cost_center_rollup = _cost_center_rollup_html(digest)
    backlog_trend = _supplier_backlog_trend_html(digest)
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
{rollup}
{supplier_rollup}
{cost_center_rollup}
{backlog_trend}
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
        rollup=rollup,
        supplier_rollup=supplier_rollup,
        cost_center_rollup=cost_center_rollup,
        backlog_trend=backlog_trend,
        breakdown=breakdown,
        body=body,
    )

"""Reconciliation engine: compares invoice lines against orders and prices."""

from __future__ import annotations

from typing import List, Optional

from .config import Settings, effective_rule, effective_settings
from .db import NO_POSITION_LABEL, Store
from .models import (
    Finding,
    PurchaseOrderItem,
    ReferencePrice,
    normalize_document_number,
    normalize_text,
    parse_date,
)


def _over(actual: float, reference: float, tolerance_pct: float) -> bool:
    """True when ``actual`` exceeds ``reference`` beyond the tolerance."""
    if reference <= 0:
        return actual > 0
    allowed = reference * (1.0 + tolerance_pct / 100.0)
    return actual > allowed + 1e-9


def reconcile_invoice(store: Store, invoice_id: int, settings: Settings) -> List[int]:
    """Reconcile a single invoice and persist its findings.

    Existing findings for the invoice are replaced. Returns the list of new
    finding ids ordered by insertion.
    """
    invoice = store.get_invoice(invoice_id)
    if invoice is None:
        raise ValueError("unknown invoice: %s" % invoice_id)

    store.clear_findings(invoice_id)

    rule = store.get_supplier_rule(invoice.supplier)
    invoice_settings = effective_settings(settings, rule)
    min_amount = (
        float(rule["min_amount_eur"])
        if rule and rule.get("min_amount_eur") is not None
        else 0.0
    )

    findings: List[Finding] = []

    po = store.get_purchase_order(invoice.po_number) if invoice.po_number else None

    if po is None:
        findings.append(
            Finding(
                finding_type="missing_po",
                amount=invoice.total_amount,
                message=(
                    "Keine Bestellung zu Rechnung %s gefunden; Betrag vor Freigabe prüfen."
                    % invoice.invoice_number
                ),
                invoice_id=invoice_id,
                reference_kind="bestellung",
                reference_value=invoice.po_number or "",
                actual_value=invoice.invoice_number,
            )
        )

    po_items = {}
    if po is not None:
        for item in po.items:
            po_items[normalize_text(item.article)] = item

    for row in store.invoice_items(invoice_id):
        findings.extend(
            _reconcile_item(store, invoice, row, po_items, invoice_settings)
        )

    findings.extend(_detect_duplicates(store, invoice, invoice_settings))

    finding_ids: List[int] = []
    for finding in findings:
        if float(finding.amount) < min_amount:
            continue
        finding_ids.append(store.add_finding(finding))
    store.mark_reconciled(invoice_id)
    return finding_ids


def _reconcile_item(store, invoice, row, po_items, settings) -> List[Finding]:
    findings: List[Finding] = []
    article_norm = row["article_norm"]
    quantity = float(row["quantity"])
    unit_price = float(row["unit_price"])
    po_item: Optional[PurchaseOrderItem] = po_items.get(article_norm)
    ref: Optional[ReferencePrice] = store.latest_reference_price(
        invoice.supplier, row["article"], invoice.invoice_date
    )

    if po_item is not None:
        reference_label = "Bestellpreis"
        reference_unit = float(po_item.unit_price)
        reference_kind = "bestellung"
        reference_value = invoice.po_number or ""
        drift = False
    elif ref is not None:
        reference_label = "Vertragspreis" if ref.source == "contract" else "zuletzt akzeptierter Preis"
        reference_unit = float(ref.unit_price)
        reference_kind = ref.source
        reference_value = ref.valid_from.isoformat() if ref.valid_from else ""
        drift = ref.source != "contract"
    else:
        po_item = None
        reference_unit = None

    if reference_unit is not None:
        threshold = settings.price_drift_pct if drift else settings.tolerance_pct
        if _over(unit_price, reference_unit, threshold):
            amount = (unit_price - reference_unit) * quantity
            if amount > 0.005:
                finding_type = "price_drift" if drift else "price_deviation"
                findings.append(
                    Finding(
                        finding_type=finding_type,
                        amount=round(amount, 2),
                        message=(
                            "%s: berechnet %.2f EUR statt %s %.2f EUR (+%.1f%%)."
                            % (
                                row["article"],
                                unit_price,
                                reference_label,
                                reference_unit,
                                (unit_price - reference_unit) / reference_unit * 100.0,
                            )
                        ),
                        invoice_id=invoice.invoice_id,
                        invoice_item_id=row["id"],
                        reference_kind=reference_kind,
                        reference_value=str(reference_value),
                        actual_value="%.2f" % unit_price,
                    )
                )

    if po_item is not None and quantity > float(po_item.quantity) + 1e-9:
        excess = quantity - float(po_item.quantity)
        amount = excess * unit_price
        if amount > 0.005:
            findings.append(
                Finding(
                    finding_type="quantity_deviation",
                    amount=round(amount, 2),
                    message=(
                        "%s: %.2f berechnet, bestellt %.2f (+%.2f)."
                        % (row["article"], quantity, po_item.quantity, excess)
                    ),
                    invoice_id=invoice.invoice_id,
                    invoice_item_id=row["id"],
                    reference_kind="menge",
                    reference_value="%.2f" % float(po_item.quantity),
                    actual_value="%.2f" % quantity,
                )
            )

    return findings


def _detect_duplicates(store, invoice, settings) -> List[Finding]:
    """Flag the *newer* invoice of a suspected duplicate pair."""
    findings: List[Finding] = []
    if invoice.invoice_id is None:
        return findings
    my_number = normalize_document_number(invoice.invoice_number)
    my_total = round(invoice.total_amount, 2)
    for other in store.invoices_for_supplier(invoice.supplier):
        other_id = int(other["id"])
        if other_id == invoice.invoice_id or other_id > invoice.invoice_id:
            # Compare only against older invoices so each pair is flagged once.
            continue
        reason = None
        other_number = other["invoice_norm"] or normalize_document_number(other["invoice_number"])
        if my_number and other_number and my_number == other_number:
            reason = "gleiche Rechnungsnummer (normalisiert): %s / %s" % (
                invoice.invoice_number,
                other["invoice_number"],
            )
        elif abs(float(other["total_amount"]) - my_total) < 0.01 and my_total > 0:
            other_date = parse_date(other["invoice_date"])
            my_date = invoice.invoice_date
            if other_date and my_date:
                delta = abs((my_date - other_date).days)
                if delta <= settings.duplicate_window_days:
                    reason = "gleicher Betrag %.2f EUR innerhalb von %d Tagen (%s)" % (
                        my_total,
                        delta,
                        other["invoice_number"],
                    )
        if reason:
            findings.append(
                Finding(
                    finding_type="duplicate",
                    amount=my_total,
                    message="Duplikatverdacht gegen %s: %s." % (other["invoice_number"], reason),
                    invoice_id=invoice.invoice_id,
                    reference_kind="rechnung",
                    reference_value=other["invoice_number"],
                    actual_value=invoice.invoice_number,
                )
            )
            break
    return findings


def reconcile_all(store: Store, settings: Settings) -> dict:
    """Reconcile every invoice; returns counts for reporting."""
    total = 0
    flagged = 0
    for invoice in store.list_invoices():
        ids = reconcile_invoice(store, int(invoice["id"]), settings)
        total += 1
        if ids:
            flagged += 1
    return {"invoices": total, "flagged": flagged}


def _group_summary(group_findings: List[dict]) -> dict:
    """Aggregate the findings of one article/position group.

    ``amount`` is the rounded sum of the group's finding amounts,
    ``finding_types`` is sorted and de-duplicated so callers get a stable
    ordering independent of the underlying row order.
    """
    return {
        "finding_count": len(group_findings),
        "amount": round(sum(float(f["amount"]) for f in group_findings), 2),
        "finding_types": sorted({f["finding_type"] for f in group_findings}),
    }


def invoice_audit(
    store: Store, invoice_id: int, settings: Optional[Settings] = None
) -> Optional[dict]:
    """Return a traceability view: what was compared and what deviated."""
    invoice = store.get_invoice(invoice_id)
    if invoice is None:
        return None
    effective = settings or Settings()
    rule = store.get_supplier_rule(invoice.supplier)
    findings = store.list_findings(invoice_id=invoice_id)
    by_item: dict = {}
    for finding in findings:
        key = finding.get("invoice_item_id")
        by_item.setdefault(key, []).append(finding)

    po = store.get_purchase_order(invoice.po_number) if invoice.po_number else None
    po_prices = {}
    if po is not None:
        for po_item in po.items:
            po_prices[normalize_text(po_item.article)] = float(po_item.unit_price)

    def _reference(row):
        po_unit = po_prices.get(row["article_norm"])
        if po_unit is not None:
            return po_unit, "bestellung"
        reference = store.latest_reference_price(
            invoice.supplier, row["article"], invoice.invoice_date
        )
        if reference is not None:
            return reference.unit_price, reference.source
        return None, None

    # One entry per invoice line item, so the audit keeps enumerating every
    # compared position: clean positions carry an empty ``findings`` list and a
    # zeroed summary. Unmatched findings (``missing_po``, ``duplicate``) are
    # appended as a single ``(ohne Position)`` group.
    items = []
    for row in store.invoice_items(invoice_id):
        group_findings = by_item.get(row["id"], [])
        reference_price, reference_source = _reference(row)
        item = {
            "position": row["position"],
            "article": row["article"],
            "quantity": row["quantity"],
            "unit_price": row["unit_price"],
            "line_total": round(row["quantity"] * row["unit_price"], 2),
            "reference_price": reference_price,
            "reference_source": reference_source,
            "findings": group_findings,
        }
        item.update(_group_summary(group_findings))
        items.append(item)

    unmatched = by_item.get(None, [])
    if unmatched:
        item = {
            "position": None,
            "article": NO_POSITION_LABEL,
            "quantity": None,
            "unit_price": None,
            "line_total": None,
            "reference_price": None,
            "reference_source": None,
            "findings": unmatched,
        }
        item.update(_group_summary(unmatched))
        items.append(item)

    items.sort(
        key=lambda entry: (
            -entry["amount"],
            entry["article"],
            entry["position"] is None,
            entry["position"] or 0,
        )
    )
    items_total_amount = round(sum(float(f["amount"]) for f in findings), 2)

    return {
        "invoice": {
            "id": invoice.invoice_id,
            "invoice_number": invoice.invoice_number,
            "supplier": invoice.supplier,
            "invoice_date": invoice.invoice_date.isoformat() if invoice.invoice_date else None,
            "po_number": invoice.po_number,
            "entity": invoice.entity,
            "cost_center": invoice.cost_center,
            "total_amount": round(invoice.total_amount, 2),
        },
        "items": items,
        "items_total_amount": items_total_amount,
        "findings": findings,
        "unmatched_findings": unmatched,
        "applied_rule": effective_rule(effective, rule),
    }


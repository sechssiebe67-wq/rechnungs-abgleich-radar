"""Ingestion helpers: turn JSON and CSV payloads into domain objects."""

from __future__ import annotations

import csv
import io
from typing import Dict, List, Optional

from .models import (
    Invoice,
    InvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    ReferencePrice,
    parse_date,
)


def _to_float(value, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("€", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    return float(text)


def _entity(value: Optional[str], default: str) -> str:
    text = (value or "").strip()
    return text or default or "default"


def _cost_center(value: Optional[str], default: str) -> str:
    """Return the invoice cost center, falling back to ``default``.

    Unlike :func:`_entity` there is no non-empty database default: an absent or
    blank value becomes the configured default (usually ``""``).
    """
    text = (value or "").strip()
    return text or (default or "").strip()


def invoice_from_dict(
    data: dict, default_entity: str = "default", default_cost_center: str = ""
) -> Invoice:
    items = []
    for idx, raw in enumerate(data.get("items", []), start=1):
        items.append(
            InvoiceItem(
                position=int(raw.get("position", idx)),
                article=str(raw.get("article", "")).strip(),
                quantity=_to_float(raw.get("quantity"), 0.0),
                unit_price=_to_float(raw.get("unit_price"), 0.0),
            )
        )
    return Invoice(
        invoice_number=str(data.get("invoice_number", "")).strip(),
        supplier=str(data.get("supplier", "")).strip(),
        invoice_date=parse_date(data.get("invoice_date")),
        po_number=(str(data["po_number"]).strip() if data.get("po_number") else None),
        entity=_entity(data.get("entity"), default_entity),
        cost_center=_cost_center(data.get("cost_center"), default_cost_center),
        items=items,
    )


def purchase_order_from_dict(data: dict) -> PurchaseOrder:
    items = []
    for raw in data.get("items", []):
        items.append(
            PurchaseOrderItem(
                article=str(raw.get("article", "")).strip(),
                quantity=_to_float(raw.get("quantity"), 0.0),
                unit_price=_to_float(raw.get("unit_price"), 0.0),
            )
        )
    return PurchaseOrder(
        po_number=str(data.get("po_number", "")).strip(),
        supplier=str(data.get("supplier", "")).strip(),
        items=items,
    )


_RULE_PARSERS = {
    "tolerance_pct": ("float", "tolerance_pct"),
    "price_drift_pct": ("float", "price_drift_pct"),
    "duplicate_window_days": ("int", "duplicate_window_days"),
    "min_amount_eur": ("float", "min_amount_eur"),
}


def supplier_rule_from_dict(data: dict) -> dict:
    """Validate and normalize a supplier rule payload.

    Raises :class:`ValueError` with a human-readable message when a value is
    out of range or not numeric.
    """
    if not isinstance(data, dict):
        raise ValueError("supplier rule must be a JSON object")
    supplier = str(data.get("supplier", "")).strip()
    if not supplier:
        raise ValueError("supplier is required")

    rule: dict = {}
    for field, (kind, label) in _RULE_PARSERS.items():
        raw = data.get(field)
        if raw is None or raw == "":
            continue
        try:
            value = int(raw) if kind == "int" else float(raw)
        except (TypeError, ValueError):
            raise ValueError("%s must be a number" % label)
        if field in ("tolerance_pct", "price_drift_pct") and not (0 <= value <= 100):
            raise ValueError("%s must be between 0 and 100" % label)
        if field == "duplicate_window_days" and value < 0:
            raise ValueError("%s must be >= 0" % label)
        if field == "min_amount_eur" and value < 0:
            raise ValueError("%s must be >= 0" % label)
        rule[field] = value
    return {"supplier": supplier, **rule}


def reference_price_from_dict(data: dict) -> ReferencePrice:
    return ReferencePrice(
        supplier=str(data.get("supplier", "")).strip(),
        article=str(data.get("article", "")).strip(),
        unit_price=_to_float(data.get("unit_price"), 0.0),
        valid_from=parse_date(data.get("valid_from")),
        source=str(data.get("source", "contract")).strip() or "contract",
    )


def invoices_from_csv(
    text: str, default_entity: str = "default", default_cost_center: str = ""
) -> List[Invoice]:
    """Parse long-format invoice CSV (one row per line item).

    The ``entity`` (Gesellschaft) and ``cost_center`` columns are optional;
    missing values fall back to ``default_entity`` / ``default_cost_center``.
    Rows are grouped by ``(number, supplier, date, po, entity, cost_center)`` so
    the same invoice number in two societies or cost centers stays two invoices.
    """
    reader = csv.DictReader(io.StringIO(text))
    grouped: Dict[tuple, Invoice] = {}
    order: List[tuple] = []
    for row in reader:
        number = (row.get("invoice_number") or "").strip()
        supplier = (row.get("supplier") or "").strip()
        invoice_date = (row.get("invoice_date") or "").strip()
        po_number = (row.get("po_number") or "").strip() or None
        entity = _entity(row.get("entity"), default_entity)
        cost_center = _cost_center(row.get("cost_center"), default_cost_center)
        key = (number, supplier, invoice_date, po_number, entity, cost_center)
        if key not in grouped:
            grouped[key] = Invoice(
                invoice_number=number,
                supplier=supplier,
                invoice_date=parse_date(invoice_date),
                po_number=po_number,
                entity=entity,
                cost_center=cost_center,
            )
            order.append(key)
        position = row.get("position")
        grouped[key].items.append(
            InvoiceItem(
                position=int(position) if position else len(grouped[key].items) + 1,
                article=(row.get("article") or "").strip(),
                quantity=_to_float(row.get("quantity"), 0.0),
                unit_price=_to_float(row.get("unit_price"), 0.0),
            )
        )
    return [grouped[key] for key in order]


def reference_prices_from_csv(text: str) -> List[ReferencePrice]:
    reader = csv.DictReader(io.StringIO(text))
    return [reference_price_from_dict(row) for row in reader]


def purchase_orders_from_csv(text: str) -> List[PurchaseOrder]:
    """Parse long-format PO CSV (one row per line item)."""
    reader = csv.DictReader(io.StringIO(text))
    grouped: Dict[str, PurchaseOrder] = {}
    order: List[str] = []
    for row in reader:
        number = (row.get("po_number") or "").strip()
        if number not in grouped:
            grouped[number] = PurchaseOrder(
                po_number=number,
                supplier=(row.get("supplier") or "").strip(),
            )
            order.append(number)
        grouped[number].items.append(
            PurchaseOrderItem(
                article=(row.get("article") or "").strip(),
                quantity=_to_float(row.get("quantity"), 0.0),
                unit_price=_to_float(row.get("unit_price"), 0.0),
            )
        )
    return [grouped[number] for number in order]

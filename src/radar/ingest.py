"""Ingestion helpers: turn JSON and CSV payloads into domain objects."""

from __future__ import annotations

import csv
import io
from typing import Dict, List

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


def invoice_from_dict(data: dict) -> Invoice:
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


def reference_price_from_dict(data: dict) -> ReferencePrice:
    return ReferencePrice(
        supplier=str(data.get("supplier", "")).strip(),
        article=str(data.get("article", "")).strip(),
        unit_price=_to_float(data.get("unit_price"), 0.0),
        valid_from=parse_date(data.get("valid_from")),
        source=str(data.get("source", "contract")).strip() or "contract",
    )


def invoices_from_csv(text: str) -> List[Invoice]:
    """Parse long-format invoice CSV (one row per line item)."""
    reader = csv.DictReader(io.StringIO(text))
    grouped: Dict[tuple, Invoice] = {}
    order: List[tuple] = []
    for row in reader:
        number = (row.get("invoice_number") or "").strip()
        supplier = (row.get("supplier") or "").strip()
        invoice_date = (row.get("invoice_date") or "").strip()
        po_number = (row.get("po_number") or "").strip() or None
        key = (number, supplier, invoice_date, po_number)
        if key not in grouped:
            grouped[key] = Invoice(
                invoice_number=number,
                supplier=supplier,
                invoice_date=parse_date(invoice_date),
                po_number=po_number,
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

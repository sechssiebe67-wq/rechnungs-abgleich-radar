"""Domain models and small helpers shared across the radar."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional


def normalize_text(value: Optional[str]) -> str:
    """Normalize an article/supplier label for tolerant matching."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_document_number(value: Optional[str]) -> str:
    """Normalize an invoice/order number, ignoring punctuation and spacing.

    Handles lightly differing numbers such as ``RE-2026/0042`` vs ``RE20260042``.
    """
    if value is None:
        return ""
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class InvoiceItem:
    position: int
    article: str
    quantity: float
    unit_price: float

    @property
    def line_total(self) -> float:
        return self.quantity * self.unit_price


@dataclass
class Invoice:
    invoice_number: str
    supplier: str
    invoice_date: Optional[date]
    items: List[InvoiceItem] = field(default_factory=list)
    po_number: Optional[str] = None
    invoice_id: Optional[int] = None
    # Legal entity (Gesellschaft) the invoice belongs to; used for the
    # multi-entity rollup. ``"default"`` keeps single-entity data working.
    entity: str = "default"

    @property
    def total_amount(self) -> float:
        return sum(item.line_total for item in self.items)


@dataclass
class PurchaseOrderItem:
    article: str
    quantity: float
    unit_price: float


@dataclass
class PurchaseOrder:
    po_number: str
    supplier: str
    items: List[PurchaseOrderItem] = field(default_factory=list)


@dataclass
class ReferencePrice:
    supplier: str
    article: str
    unit_price: float
    valid_from: Optional[date] = None
    source: str = "contract"  # "contract" or "accepted"


@dataclass
class Finding:
    finding_type: str
    amount: float
    message: str
    invoice_id: int
    invoice_item_id: Optional[int] = None
    reference_kind: Optional[str] = None
    reference_value: Optional[str] = None
    actual_value: Optional[str] = None
    status: str = "open"
    feedback: Optional[str] = None
    finding_id: Optional[int] = None


FINDING_LABELS = {
    "price_deviation": "Preisabweichung",
    "quantity_deviation": "Mengenabweichung",
    "missing_po": "Fehlende Bestellreferenz",
    "duplicate": "Duplikatverdacht",
    "price_drift": "Preisdrift",
}

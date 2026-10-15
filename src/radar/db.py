"""SQLite persistence for the invoice reconciliation radar.

SQLite is used deliberately: it keeps the product standalone, dependency-free
and cheap to operate while still providing durable persistence for reference
data, invoices, findings and accumulated feedback.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from typing import Iterator, List, Optional

from .models import (
    Finding,
    Invoice,
    InvoiceItem,
    PurchaseOrder,
    PurchaseOrderItem,
    ReferencePrice,
    normalize_document_number,
    normalize_text,
    parse_date,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS reference_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT NOT NULL,
    article TEXT NOT NULL,
    article_norm TEXT NOT NULL,
    unit_price REAL NOT NULL,
    valid_from TEXT,
    source TEXT NOT NULL DEFAULT 'contract',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ref_supplier_article
    ON reference_prices (supplier, article_norm);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_number TEXT PRIMARY KEY,
    supplier TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS po_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_number TEXT NOT NULL REFERENCES purchase_orders(po_number) ON DELETE CASCADE,
    article TEXT NOT NULL,
    article_norm TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_po_items_number ON po_items (po_number);

CREATE TABLE IF NOT EXISTS invoices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_number TEXT NOT NULL,
    invoice_norm TEXT NOT NULL,
    supplier TEXT NOT NULL,
    invoice_date TEXT,
    po_number TEXT,
    total_amount REAL NOT NULL DEFAULT 0,
    reconciled_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_invoices_supplier ON invoices (supplier);

CREATE TABLE IF NOT EXISTS invoice_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    article TEXT NOT NULL,
    article_norm TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit_price REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_invoice_items_invoice ON invoice_items (invoice_id);

CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    invoice_item_id INTEGER REFERENCES invoice_items(id) ON DELETE CASCADE,
    finding_type TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    message TEXT NOT NULL,
    reference_kind TEXT,
    reference_value TEXT,
    actual_value TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    feedback TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_findings_invoice ON findings (invoice_id);
CREATE INDEX IF NOT EXISTS idx_findings_amount ON findings (amount);
"""


class Store:
    def __init__(self, db_path: str = "radar.db") -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # -- reference data ---------------------------------------------------

    def upsert_supplier(self, name: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO suppliers (name) VALUES (?)", (name.strip(),)
            )

    def list_suppliers(self) -> List[str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT name FROM suppliers ORDER BY name").fetchall()
        return [row["name"] for row in rows]

    def add_reference_price(self, price: ReferencePrice) -> None:
        self.upsert_supplier(price.supplier)
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO reference_prices
                   (supplier, article, article_norm, unit_price, valid_from, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    price.supplier.strip(),
                    price.article.strip(),
                    normalize_text(price.article),
                    float(price.unit_price),
                    price.valid_from.isoformat() if price.valid_from else None,
                    price.source,
                ),
            )

    def latest_reference_price(
        self, supplier: str, article: str, on_date: Optional[date] = None
    ) -> Optional[ReferencePrice]:
        article_norm = normalize_text(article)
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT supplier, article, unit_price, valid_from, source
                   FROM reference_prices
                   WHERE supplier = ? AND article_norm = ?
                   ORDER BY COALESCE(valid_from, '0000-00-00') DESC, id DESC""",
                (supplier.strip(), article_norm),
            ).fetchall()
        contracts = [r for r in rows if r["source"] == "contract"]
        accepted = [r for r in rows if r["source"] != "contract"]

        candidate = None
        for row in contracts:
            if on_date is None or row["valid_from"] is None:
                candidate = row
                break
            valid_from = parse_date(row["valid_from"])
            if valid_from is None or valid_from <= on_date:
                candidate = row
                break
        if candidate is None and accepted:
            candidate = accepted[0]
        if candidate is None:
            return None
        return ReferencePrice(
            supplier=candidate["supplier"],
            article=candidate["article"],
            unit_price=candidate["unit_price"],
            valid_from=parse_date(candidate["valid_from"]),
            source=candidate["source"],
        )

    # -- purchase orders --------------------------------------------------

    def add_purchase_order(self, po: PurchaseOrder) -> None:
        self.upsert_supplier(po.supplier)
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO purchase_orders (po_number, supplier) VALUES (?, ?)",
                (po.po_number.strip(), po.supplier.strip()),
            )
            conn.execute("DELETE FROM po_items WHERE po_number = ?", (po.po_number.strip(),))
            for item in po.items:
                conn.execute(
                    """INSERT INTO po_items
                       (po_number, article, article_norm, quantity, unit_price)
                       VALUES (?, ?, ?, ?, ?)""",
                    (
                        po.po_number.strip(),
                        item.article.strip(),
                        normalize_text(item.article),
                        float(item.quantity),
                        float(item.unit_price),
                    ),
                )

    def get_purchase_order(self, po_number: str) -> Optional[PurchaseOrder]:
        with self.connect() as conn:
            header = conn.execute(
                "SELECT po_number, supplier FROM purchase_orders WHERE po_number = ?",
                (po_number.strip(),),
            ).fetchone()
            if header is None:
                return None
            items = conn.execute(
                "SELECT article, quantity, unit_price FROM po_items WHERE po_number = ?",
                (po_number.strip(),),
            ).fetchall()
        return PurchaseOrder(
            po_number=header["po_number"],
            supplier=header["supplier"],
            items=[
                PurchaseOrderItem(
                    article=row["article"],
                    quantity=row["quantity"],
                    unit_price=row["unit_price"],
                )
                for row in items
            ],
        )

    # -- invoices ---------------------------------------------------------

    def add_invoice(self, invoice: Invoice) -> int:
        self.upsert_supplier(invoice.supplier)
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO invoices
                   (invoice_number, invoice_norm, supplier, invoice_date, po_number, total_amount)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    invoice.invoice_number.strip(),
                    normalize_document_number(invoice.invoice_number),
                    invoice.supplier.strip(),
                    invoice.invoice_date.isoformat() if invoice.invoice_date else None,
                    invoice.po_number.strip() if invoice.po_number else None,
                    invoice.total_amount,
                ),
            )
            invoice_id = int(cursor.lastrowid)
            for item in invoice.items:
                conn.execute(
                    """INSERT INTO invoice_items
                       (invoice_id, position, article, article_norm, quantity, unit_price)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        invoice_id,
                        int(item.position),
                        item.article.strip(),
                        normalize_text(item.article),
                        float(item.quantity),
                        float(item.unit_price),
                    ),
                )
        return invoice_id

    def get_invoice(self, invoice_id: int) -> Optional[Invoice]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
            ).fetchone()
            if row is None:
                return None
            item_rows = conn.execute(
                "SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY position",
                (invoice_id,),
            ).fetchall()
        return Invoice(
            invoice_id=row["id"],
            invoice_number=row["invoice_number"],
            supplier=row["supplier"],
            invoice_date=parse_date(row["invoice_date"]),
            po_number=row["po_number"],
            items=[
                InvoiceItem(
                    position=r["position"],
                    article=r["article"],
                    quantity=r["quantity"],
                    unit_price=r["unit_price"],
                )
                for r in item_rows
            ],
        )

    def list_invoices(self) -> List[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT i.*,
                          (SELECT COUNT(*) FROM findings f WHERE f.invoice_id = i.id) AS finding_count,
                          (SELECT COALESCE(SUM(amount), 0) FROM findings f
                             WHERE f.invoice_id = i.id AND f.status = 'open') AS open_amount
                   FROM invoices i
                   ORDER BY i.created_at DESC, i.id DESC"""
            ).fetchall()
        return [dict(row) for row in rows]

    def invoice_items(self, invoice_id: int) -> List[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM invoice_items WHERE invoice_id = ? ORDER BY position",
                (invoice_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def invoices_for_supplier(self, supplier: str) -> List[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM invoices WHERE supplier = ? ORDER BY id",
                (supplier.strip(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_reconciled(self, invoice_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE invoices SET reconciled_at = datetime('now') WHERE id = ?",
                (invoice_id,),
            )

    # -- findings ---------------------------------------------------------

    def clear_findings(self, invoice_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM findings WHERE invoice_id = ?", (invoice_id,))

    def add_finding(self, finding: Finding) -> int:
        with self.connect() as conn:
            cursor = conn.execute(
                """INSERT INTO findings
                   (invoice_id, invoice_item_id, finding_type, amount, message,
                    reference_kind, reference_value, actual_value, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    finding.invoice_id,
                    finding.invoice_item_id,
                    finding.finding_type,
                    float(finding.amount),
                    finding.message,
                    finding.reference_kind,
                    finding.reference_value,
                    finding.actual_value,
                    finding.status,
                ),
            )
            return int(cursor.lastrowid)

    def list_findings(
        self,
        status: Optional[str] = None,
        min_amount: Optional[float] = None,
        invoice_id: Optional[int] = None,
    ) -> List[dict]:
        query = "SELECT * FROM findings"
        clauses = []
        params: List[object] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if min_amount is not None:
            clauses.append("amount >= ?")
            params.append(float(min_amount))
        if invoice_id is not None:
            clauses.append("invoice_id = ?")
            params.append(int(invoice_id))
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY amount DESC, id ASC"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def get_finding(self, finding_id: int) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM findings WHERE id = ?", (finding_id,)
            ).fetchone()
        return dict(row) if row else None

    def set_feedback(self, finding_id: int, feedback: str) -> bool:
        status = "confirmed" if feedback == "confirmed" else "false_alarm"
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE findings SET feedback = ?, status = ? WHERE id = ?",
                (feedback, status, finding_id),
            )
            return cursor.rowcount > 0

    # -- accepted prices and stats ---------------------------------------

    def record_accepted_price(
        self, supplier: str, article: str, unit_price: float, accepted_at: Optional[date] = None
    ) -> None:
        self.add_reference_price(
            ReferencePrice(
                supplier=supplier,
                article=article,
                unit_price=unit_price,
                valid_from=accepted_at or date.today(),
                source="accepted",
            )
        )

    def stats(self) -> dict:
        with self.connect() as conn:
            invoices = conn.execute("SELECT COUNT(*) AS c FROM invoices").fetchone()["c"]
            findings = conn.execute("SELECT COUNT(*) AS c FROM findings").fetchone()["c"]
            open_amount = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) AS s FROM findings WHERE status = 'open'"
            ).fetchone()["s"]
            confirmed = conn.execute(
                "SELECT COUNT(*) AS c FROM findings WHERE feedback = 'confirmed'"
            ).fetchone()["c"]
            false_alarm = conn.execute(
                "SELECT COUNT(*) AS c FROM findings WHERE feedback = 'false_alarm'"
            ).fetchone()["c"]
        reviewed = confirmed + false_alarm
        precision = (confirmed / reviewed) if reviewed else None
        return {
            "invoices": invoices,
            "findings": findings,
            "open_amount": round(float(open_amount), 2),
            "confirmed": confirmed,
            "false_alarm": false_alarm,
            "precision": round(precision, 4) if precision is not None else None,
        }

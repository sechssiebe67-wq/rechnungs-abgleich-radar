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

from .config import Settings, effective_rule
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

CREATE TABLE IF NOT EXISTS supplier_rules (
    supplier TEXT PRIMARY KEY REFERENCES suppliers(name) ON DELETE CASCADE,
    tolerance_pct REAL,
    price_drift_pct REAL,
    duplicate_window_days INTEGER,
    min_amount_eur REAL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS supplier_rule_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier TEXT NOT NULL,
    changed_at TEXT NOT NULL DEFAULT (datetime('now')),
    tolerance_pct REAL,
    price_drift_pct REAL,
    duplicate_window_days INTEGER,
    min_amount_eur REAL
);
CREATE INDEX IF NOT EXISTS idx_rule_history_supplier
    ON supplier_rule_history (supplier, changed_at, id);

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
    entity TEXT NOT NULL DEFAULT 'default',
    cost_center TEXT NOT NULL DEFAULT '',
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


SUPPLIER_RULE_FIELDS = (
    "tolerance_pct",
    "price_drift_pct",
    "duplicate_window_days",
    "min_amount_eur",
)

# Cap for the ROI report's article breakdown. Keeps the response bounded when a
# customer reconciles thousands of distinct articles.
DEFAULT_ARTICLE_LIMIT = 50

# Default size of the invoice-level review workload top list. Keeps the response
# bounded while still concentrating on the invoices that bind the most open
# review effort; callers may override it with ``?limit=``.
DEFAULT_REVIEW_WORKLOAD_LIMIT = 25

# Default window (in calendar months) of the review-workload time series. Keeps
# the backlog view focused on recent months while callers may widen or narrow it
# with ``?months=``.
DEFAULT_REVIEW_WORKLOAD_TIMESERIES_MONTHS = 6

# Fields of one per-society rollup row below the digest's ``by_entity_rollup``
# key (in addition to the ``entity`` key). The ``totals`` row carries the exact
# same fields with ``entity`` set to ``None`` so all rows share one shape.
ROLLUP_FIELDS = (
    "open_finding_count",
    "open_amount",
    "open_review_minutes",
    "open_review_cost_eur",
    "confirmed",
    "false_alarm",
    "precision",
    "avoided_review_cost_eur",
    "avoided_mispayment_eur",
)

# Display label for findings that are not tied to a single invoice position
# (e.g. ``missing_po`` or ``duplicate``); they still count towards the report.
NO_POSITION_LABEL = "(ohne Position)"

# Whitelisted sort orders for the findings list. The keys are the public names
# accepted by ``GET /api/findings?sort=``; the values are the matching SQL
# ``ORDER BY`` fragments. The default (``amount_desc``) preserves the original
# behaviour. A secondary ``id`` key keeps the order deterministic for ties and
# for stable pagination.
FINDING_ORDER_BY = {
    "amount_desc": "f.amount DESC, f.id ASC",
    "amount_asc": "f.amount ASC, f.id ASC",
    "created_at_desc": "f.created_at DESC, f.id DESC",
    "created_at_asc": "f.created_at ASC, f.id ASC",
}
DEFAULT_FINDING_ORDER = "amount_desc"

# Whitelisted sort orders for the invoice list. ``created_at_desc`` is the
# default and reproduces the original ``ORDER BY i.created_at DESC, i.id DESC``
# exactly; the secondary ``id`` key keeps the order deterministic for ties and
# stable pagination.
INVOICE_ORDER_BY = {
    "created_at_desc": "i.created_at DESC, i.id DESC",
    "created_at_asc": "i.created_at ASC, i.id ASC",
    "invoice_number_asc": "i.invoice_number ASC, i.id ASC",
    "invoice_number_desc": "i.invoice_number DESC, i.id DESC",
    "open_amount_desc": "open_amount DESC, i.id ASC",
    "open_amount_asc": "open_amount ASC, i.id ASC",
    "open_finding_count_desc": "open_finding_count DESC, i.id ASC",
    "open_finding_count_asc": "open_finding_count ASC, i.id ASC",
}
DEFAULT_INVOICE_ORDER = "created_at_desc"

# Review-status values accepted by ``GET /api/findings?review_status=`` and used
# for the per-invoice ``review_status`` classification. ``all`` means "no
# filter" and is the backwards-compatible default.
REVIEW_STATUS_VALUES = ("all", "open", "reviewed")

# Whitelisted sort orders for the supplier-rule list. ``supplier_asc`` is the
# default and reproduces the original ``ORDER BY supplier``. ``supplier`` is the
# primary key (hence unique), so it doubles as the deterministic secondary key;
# the ``updated_at`` orders append it to break ties.
SUPPLIER_RULE_ORDER_BY = {
    "supplier_asc": "supplier ASC",
    "supplier_desc": "supplier DESC",
    "updated_at_desc": "updated_at DESC, supplier ASC",
    "updated_at_asc": "updated_at ASC, supplier ASC",
}
DEFAULT_SUPPLIER_RULE_ORDER = "supplier_asc"


def _impact_period(row: sqlite3.Row, prefix: str) -> dict:
    """Shape one before/after aggregate row into a report period."""
    confirmed = int(row[prefix + "_confirmed"])
    false_alarm = int(row[prefix + "_false_alarm"])
    reviewed = confirmed + false_alarm
    precision = (confirmed / reviewed) if reviewed else None
    return {
        "findings": int(row[prefix + "_findings"]),
        "confirmed": confirmed,
        "false_alarm": false_alarm,
        "precision": round(precision, 4) if precision is not None else None,
        "open_amount": round(float(row[prefix + "_open_amount"]), 2),
    }


def _shift_month(month: str, delta: int) -> str:
    """Shift a ``YYYY-MM`` string by ``delta`` calendar months."""
    year, mon = int(month[:4]), int(month[5:7])
    index = year * 12 + (mon - 1) + delta
    return "%04d-%02d" % (index // 12, index % 12 + 1)


def _pick_top_invoice(invoices: List[dict]) -> Optional[dict]:
    """Return the invoice binding the most open review load.

    ``invoices`` is a list of the per-invoice aggregates used by the review
    workload time series. The winner has the largest ``open_amount``; ties are
    broken by ``open_finding_count`` descending and then ``id`` ascending, so the
    selection — and therefore the emitted ``top_invoice`` — is deterministic.
    Returns ``None`` for an empty list.
    """
    if not invoices:
        return None
    return min(
        invoices,
        key=lambda inv: (-inv["open_amount"], -inv["open_finding_count"], inv["id"]),
    )


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
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Bring an existing database up to the current schema.

        SQLite cannot add a column via ``CREATE TABLE IF NOT EXISTS``, so legacy
        databases that predate the multi-entity rollup or the cost-center view
        are upgraded in place. The checks are idempotent and never drop data;
        existing invoices receive the ``default`` entity and an empty cost
        center.
        """
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(invoices)").fetchall()
        }
        if "entity" not in columns:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN entity TEXT NOT NULL DEFAULT 'default'"
            )
        if "cost_center" not in columns:
            conn.execute(
                "ALTER TABLE invoices ADD COLUMN cost_center TEXT NOT NULL DEFAULT ''"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_entity "
            "ON invoices (entity, supplier)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoices_cost_center "
            "ON invoices (entity, cost_center)"
        )

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

    # -- supplier rules ---------------------------------------------------
    #
    # A ``NULL`` field means "fall back to the global default from Settings".

    def upsert_supplier_rule(self, supplier: str, **fields: Optional[float]) -> None:
        supplier = supplier.strip()
        if not supplier:
            raise ValueError("supplier is required")
        self.upsert_supplier(supplier)
        values = [fields.get(name) for name in SUPPLIER_RULE_FIELDS]
        with self.connect() as conn:
            existing = conn.execute(
                """SELECT tolerance_pct, price_drift_pct,
                          duplicate_window_days, min_amount_eur
                   FROM supplier_rules WHERE supplier = ?""",
                (supplier,),
            ).fetchone()
            changed = existing is None or any(
                existing[name] != value
                for name, value in zip(SUPPLIER_RULE_FIELDS, values)
            )
            conn.execute(
                """INSERT INTO supplier_rules
                   (supplier, tolerance_pct, price_drift_pct,
                    duplicate_window_days, min_amount_eur, updated_at)
                   VALUES (?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(supplier) DO UPDATE SET
                       tolerance_pct = excluded.tolerance_pct,
                       price_drift_pct = excluded.price_drift_pct,
                       duplicate_window_days = excluded.duplicate_window_days,
                       min_amount_eur = excluded.min_amount_eur,
                       updated_at = datetime('now')""",
                (supplier, *values),
            )
            if changed:
                conn.execute(
                    """INSERT INTO supplier_rule_history
                       (supplier, tolerance_pct, price_drift_pct,
                        duplicate_window_days, min_amount_eur)
                       VALUES (?, ?, ?, ?, ?)""",
                    (supplier, *values),
                )

    def list_supplier_rule_history(self, supplier: str) -> List[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT * FROM supplier_rule_history
                   WHERE supplier = ? ORDER BY id""",
                (supplier.strip(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_supplier_rule(self, supplier: str) -> Optional[dict]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM supplier_rules WHERE supplier = ?", (supplier.strip(),)
            ).fetchone()
        return dict(row) if row else None

    def list_supplier_rules(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
    ) -> List[dict]:
        """List supplier rules, optionally sorted and paged.

        ``order_by`` must be one of :data:`SUPPLIER_RULE_ORDER_BY`; ``None``
        keeps the documented default ordering (``supplier_asc``) and is fully
        backwards compatible. ``limit``/``offset`` are applied only when
        provided, so an unpaged call returns exactly the rows it always did.
        """
        if order_by is None:
            order_by = DEFAULT_SUPPLIER_RULE_ORDER
        if order_by not in SUPPLIER_RULE_ORDER_BY:
            raise ValueError("unknown order_by: %s" % order_by)
        if limit is not None and int(limit) < 0:
            raise ValueError("limit must be >= 0")
        if offset is not None and int(offset) < 0:
            raise ValueError("offset must be >= 0")

        query = (
            "SELECT * FROM supplier_rules ORDER BY "
            + SUPPLIER_RULE_ORDER_BY[order_by]
        )
        params: List[object] = []
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
            if offset is not None:
                query += " OFFSET ?"
                params.append(int(offset))
        elif offset is not None:
            query += " LIMIT -1 OFFSET ?"
            params.append(int(offset))
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def count_supplier_rules(self) -> int:
        """Count all supplier rules, independent of any pagination window."""
        with self.connect() as conn:
            return int(
                conn.execute("SELECT COUNT(*) AS c FROM supplier_rules").fetchone()["c"]
            )

    def delete_supplier_rule(self, supplier: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM supplier_rules WHERE supplier = ?", (supplier.strip(),)
            )
            return cursor.rowcount > 0

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
            entity = (invoice.entity or "default").strip() or "default"
            cost_center = (invoice.cost_center or "").strip()
            cursor = conn.execute(
                """INSERT INTO invoices
                   (invoice_number, invoice_norm, supplier, invoice_date, po_number,
                    total_amount, entity, cost_center)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    invoice.invoice_number.strip(),
                    normalize_document_number(invoice.invoice_number),
                    invoice.supplier.strip(),
                    invoice.invoice_date.isoformat() if invoice.invoice_date else None,
                    invoice.po_number.strip() if invoice.po_number else None,
                    invoice.total_amount,
                    entity,
                    cost_center,
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
            entity=row["entity"] or "default",
            cost_center=row["cost_center"] or "",
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

    def list_invoices(
        self,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
    ) -> List[dict]:
        """List invoices, optionally filtered, sorted and paged.

        ``order_by`` must be one of :data:`INVOICE_ORDER_BY`; ``None`` keeps the
        documented default ordering (``created_at_desc``) and is fully backwards
        compatible. ``limit``/``offset`` are applied only when provided, so an
        unpaged call returns exactly the rows it always did.

        ``entities`` and ``cost_centers`` restrict the result to invoices of the
        chosen societies resp. cost centers and are AND-combined (an invoice must
        match both when both are given). Unknown values are not an error and
        simply yield an empty list; ``None``/empty applies no filter.
        """
        if order_by is None:
            order_by = DEFAULT_INVOICE_ORDER
        if order_by not in INVOICE_ORDER_BY:
            raise ValueError("unknown order_by: %s" % order_by)
        if limit is not None and int(limit) < 0:
            raise ValueError("limit must be >= 0")
        if offset is not None and int(offset) < 0:
            raise ValueError("offset must be >= 0")

        clauses: List[str] = []
        params: List[object] = []
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            clauses.append("i.entity IN (%s)" % placeholders)
            params.extend(entities)
        if cost_centers:
            placeholders = ", ".join("?" for _ in cost_centers)
            clauses.append("i.cost_center IN (%s)" % placeholders)
            params.extend(cost_centers)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        query = (
            """SELECT i.*,
                      COUNT(f.id) AS finding_count,
                      COALESCE(SUM(CASE WHEN f.id IS NOT NULL AND f.feedback IS NULL
                                        THEN 1 ELSE 0 END), 0) AS open_finding_count,
                      COALESCE(SUM(CASE WHEN f.id IS NOT NULL AND f.feedback IS NOT NULL
                                        THEN 1 ELSE 0 END), 0) AS reviewed_finding_count,
                      COALESCE(SUM(CASE WHEN f.status = 'open'
                                        THEN f.amount ELSE 0 END), 0) AS open_amount
               FROM invoices i
               LEFT JOIN findings f ON f.invoice_id = i.id
               %s
               GROUP BY i.id
               ORDER BY """ % where
            + INVOICE_ORDER_BY[order_by]
        )
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
            if offset is not None:
                query += " OFFSET ?"
                params.append(int(offset))
        elif offset is not None:
            query += " LIMIT -1 OFFSET ?"
            params.append(int(offset))
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        results: List[dict] = []
        for row in rows:
            entry = dict(row)
            finding_count = int(entry["finding_count"])
            open_count = int(entry["open_finding_count"])
            if finding_count == 0:
                entry["review_status"] = "clean"
            elif open_count > 0:
                entry["review_status"] = "open"
            else:
                entry["review_status"] = "reviewed"
            results.append(entry)
        return results

    def count_invoices(
        self,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
    ) -> int:
        """Count invoices, independent of any pagination window.

        Accepts the same optional ``entities``/``cost_centers`` filters as
        :meth:`list_invoices` so callers can report ``total_count`` for a
        filtered set.
        """
        clauses: List[str] = []
        params: List[object] = []
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            clauses.append("i.entity IN (%s)" % placeholders)
            params.extend(entities)
        if cost_centers:
            placeholders = ", ".join("?" for _ in cost_centers)
            clauses.append("i.cost_center IN (%s)" % placeholders)
            params.extend(cost_centers)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) AS c FROM invoices i" + where, params
                ).fetchone()["c"]
            )

    def review_workload(
        self,
        settings: Optional[Settings] = None,
        limit: Optional[int] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
    ) -> List[dict]:
        """Invoices that bind the most open review effort, top first.

        One row per invoice with at least one still-open finding
        (``open_finding_count > 0``), computed with a single ``LEFT JOIN``
        aggregate over ``findings`` (no N+1). ``open_amount`` sums the amounts
        of the invoice's open findings and therefore matches the ``open_amount``
        reported by ``GET /api/stats`` for the same scope. On top, the bound
        effort is derived from the ROI assumptions:
        ``open_review_minutes = open_finding_count × review_minutes_per_finding``
        and ``open_review_cost_eur = open_review_minutes / 60 × hourly_rate``
        (both rounded to two decimals, like the ROI reports).

        The list is sorted by ``open_amount`` descending, then
        ``open_finding_count`` descending, then ``id`` ascending — fully
        deterministic for ties. ``limit`` is applied server-side (default
        :data:`DEFAULT_REVIEW_WORKLOAD_LIMIT`); ``0`` yields an empty list.
        ``entities``/``cost_centers`` restrict the result exactly like
        :meth:`list_invoices` and are AND-combined; unknown values yield an
        empty list rather than an error.
        """
        base = settings or Settings()
        minutes = base.review_minutes_per_finding
        rate = base.review_hourly_rate_eur
        cap = DEFAULT_REVIEW_WORKLOAD_LIMIT if limit is None else int(limit)
        if cap < 0:
            raise ValueError("limit must be >= 0")

        clauses: List[str] = []
        params: List[object] = []
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            clauses.append("i.entity IN (%s)" % placeholders)
            params.extend(entities)
        if cost_centers:
            placeholders = ", ".join("?" for _ in cost_centers)
            clauses.append("i.cost_center IN (%s)" % placeholders)
            params.extend(cost_centers)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        query = (
            """SELECT i.id, i.invoice_number, i.supplier,
                      COALESCE(i.entity, 'default') AS entity,
                      COALESCE(i.cost_center, '') AS cost_center,
                      COUNT(f.id) AS finding_count,
                      COALESCE(SUM(CASE WHEN f.id IS NOT NULL AND f.feedback IS NULL
                                        THEN 1 ELSE 0 END), 0) AS open_finding_count,
                      COALESCE(SUM(CASE WHEN f.id IS NOT NULL AND f.feedback IS NOT NULL
                                        THEN 1 ELSE 0 END), 0) AS reviewed_finding_count,
                      COALESCE(SUM(CASE WHEN f.status = 'open'
                                        THEN f.amount ELSE 0 END), 0) AS open_amount
               FROM invoices i
               LEFT JOIN findings f ON f.invoice_id = i.id
               %s
               GROUP BY i.id
               HAVING open_finding_count > 0
               ORDER BY open_amount DESC, open_finding_count DESC, i.id ASC
               LIMIT ?"""
            % where
        )
        params.append(cap)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        results: List[dict] = []
        for row in rows:
            open_count = int(row["open_finding_count"])
            open_minutes = round(open_count * minutes, 2)
            results.append(
                {
                    "id": int(row["id"]),
                    "invoice_number": row["invoice_number"],
                    "supplier": row["supplier"],
                    "entity": row["entity"] or "default",
                    "cost_center": row["cost_center"] or "",
                    "finding_count": int(row["finding_count"]),
                    "open_finding_count": open_count,
                    "reviewed_finding_count": int(row["reviewed_finding_count"]),
                    "open_amount": round(float(row["open_amount"]), 2),
                    "open_review_minutes": open_minutes,
                    "open_review_cost_eur": round(open_minutes / 60 * rate, 2),
                }
            )
        return results

    def review_workload_timeseries(
        self,
        settings: Optional[Settings] = None,
        months: Optional[int] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
        review_minutes: Optional[float] = None,
        hourly_rate: Optional[float] = None,
    ) -> dict:
        """Open review load per (month, entity, cost center) over time.

        Answers the third controller question: how did the open review load
        develop over the last months, and which society/cost center is
        accumulating backlog? Invoices are bucketed by
        ``strftime('%Y-%m', invoices.created_at)``, ``invoices.entity`` and
        ``invoices.cost_center``; only invoices with at least one still-open
        finding enter the report (computed with a single ``JOIN`` aggregate per
        invoice — no N+1).

        Each row carries ``month``, ``entity``, ``cost_center``,
        ``invoice_count`` (invoices with at least one open finding),
        ``open_finding_count``, ``open_amount`` (sum of the amounts of open
        findings), the bound effort ``open_review_minutes`` /
        ``open_review_cost_eur`` (using exactly the ROI assumptions:
        ``open_finding_count × review_minutes_per_finding`` and
        ``open_review_minutes / 60 × hourly_rate``, rounded to two decimals) and
        ``top_invoice`` — the invoice with the largest ``open_amount`` in the
        bucket (ties: ``open_finding_count`` descending, then ``id`` ascending)
        with ``id``, ``invoice_number``, ``supplier``, ``open_finding_count`` and
        ``open_amount``. ``top_invoice`` is ``None`` for a bucket without a
        qualifying invoice.

        Rows are sorted by ``open_amount`` descending, then ``entity``,
        ``cost_center`` and ``month`` ascending for deterministic output.
        ``totals`` mirrors the row shape and is the exact sum over the returned
        rows (``top_invoice`` = the global largest). ``parameters`` echoes the
        effective assumptions.

        The ``months`` window (default
        :data:`DEFAULT_REVIEW_WORKLOAD_TIMESERIES_MONTHS`, must be ``>= 1``)
        keeps only the newest ``months`` calendar months present in the filtered
        data (anchor: newest month). ``entities``/``cost_centers`` restrict the
        view like :meth:`list_invoices` (AND-combined); unknown values are not an
        error and simply yield an empty ``rows`` list. ``review_minutes`` and
        ``hourly_rate`` override the settings for what-if analyses.
        """
        base = settings or Settings()
        minutes = base.review_minutes_per_finding if review_minutes is None else float(
            review_minutes
        )
        rate = (
            base.review_hourly_rate_eur if hourly_rate is None else float(hourly_rate)
        )
        if minutes < 0:
            raise ValueError("review_minutes must be >= 0")
        if rate < 0:
            raise ValueError("hourly_rate must be >= 0")
        window = (
            DEFAULT_REVIEW_WORKLOAD_TIMESERIES_MONTHS
            if months is None
            else int(months)
        )
        if window < 1:
            raise ValueError("months must be >= 1")

        clauses: List[str] = []
        params: List[object] = []
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            clauses.append("i.entity IN (%s)" % placeholders)
            params.extend(entities)
        if cost_centers:
            placeholders = ", ".join("?" for _ in cost_centers)
            clauses.append("i.cost_center IN (%s)" % placeholders)
            params.extend(cost_centers)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        with self.connect() as conn:
            rows = conn.execute(
                """SELECT strftime('%%Y-%%m', i.created_at) AS month,
                          COALESCE(i.entity, 'default') AS entity,
                          COALESCE(i.cost_center, '') AS cost_center,
                          i.id AS id,
                          i.invoice_number AS invoice_number,
                          i.supplier AS supplier,
                          COALESCE(SUM(CASE WHEN f.id IS NOT NULL AND f.feedback IS NULL
                                            THEN 1 ELSE 0 END), 0) AS open_finding_count,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN f.amount ELSE 0 END), 0) AS open_amount
                   FROM invoices i
                   JOIN findings f ON f.invoice_id = i.id
                   %s
                   GROUP BY i.id
                   HAVING open_finding_count > 0"""
                % where,
                params,
            ).fetchall()

        months_present = [row["month"] for row in rows if row["month"]]
        cutoff = None
        if months_present:
            cutoff = _shift_month(max(months_present), -(window - 1))

        groups: dict = {}
        kept_invoices: List[dict] = []
        for row in rows:
            month = row["month"]
            if not month:
                continue
            if cutoff is not None and month < cutoff:
                continue
            entity = row["entity"] or "default"
            cost_center = row["cost_center"] or ""
            invoice = {
                "id": int(row["id"]),
                "invoice_number": row["invoice_number"],
                "supplier": row["supplier"],
                "open_finding_count": int(row["open_finding_count"]),
                "open_amount": round(float(row["open_amount"]), 2),
            }
            kept_invoices.append(invoice)
            key = (month, entity, cost_center)
            group = groups.get(key)
            if group is None:
                group = {
                    "invoice_count": 0,
                    "open_finding_count": 0,
                    "open_amount": 0.0,
                    "invoices": [],
                }
                groups[key] = group
            group["invoice_count"] += 1
            group["open_finding_count"] += invoice["open_finding_count"]
            group["open_amount"] += invoice["open_amount"]
            group["invoices"].append(invoice)

        result_rows: List[dict] = []
        for (month, entity, cost_center), group in groups.items():
            open_count = group["open_finding_count"]
            open_minutes = round(open_count * minutes, 2)
            result_rows.append(
                {
                    "month": month,
                    "entity": entity,
                    "cost_center": cost_center,
                    "invoice_count": group["invoice_count"],
                    "open_finding_count": open_count,
                    "open_amount": round(group["open_amount"], 2),
                    "open_review_minutes": open_minutes,
                    "open_review_cost_eur": round(open_minutes / 60 * rate, 2),
                    "top_invoice": _pick_top_invoice(group["invoices"]),
                }
            )
        result_rows.sort(
            key=lambda entry: (
                -entry["open_amount"],
                entry["entity"],
                entry["cost_center"],
                entry["month"],
            )
        )

        total_minutes = round(
            sum(entry["open_review_minutes"] for entry in result_rows), 2
        )
        totals = {
            "month": None,
            "entity": None,
            "cost_center": None,
            "invoice_count": sum(entry["invoice_count"] for entry in result_rows),
            "open_finding_count": sum(
                entry["open_finding_count"] for entry in result_rows
            ),
            "open_amount": round(
                sum(entry["open_amount"] for entry in result_rows), 2
            ),
            "open_review_minutes": total_minutes,
            "open_review_cost_eur": round(
                sum(entry["open_review_cost_eur"] for entry in result_rows), 2
            ),
            "top_invoice": _pick_top_invoice(kept_invoices),
        }
        return {
            "rows": result_rows,
            "totals": totals,
            "parameters": {
                "months": window,
                "review_minutes_per_finding": minutes,
                "review_hourly_rate_eur": rate,
            },
        }

    def review_workload_by_supplier_timeseries(
        self,
        settings: Optional[Settings] = None,
        months: Optional[int] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
        review_minutes: Optional[float] = None,
        hourly_rate: Optional[float] = None,
    ) -> dict:
        """Open review load per (month, supplier) over time.

        Operative procurement view answering *which supplier is accumulating
        backlog over the months, and how much effort does it bind?* Invoices are
        bucketed by ``strftime('%Y-%m', invoices.created_at)`` and
        ``invoices.supplier``; only invoices with at least one still-open finding
        enter the report (computed with a single ``JOIN`` aggregate per invoice —
        no N+1).

        Each row carries ``month``, ``supplier``, ``invoice_count`` (invoices with
        at least one open finding), ``open_finding_count``, ``open_amount`` (sum
        of the amounts of open findings), the bound effort ``open_review_minutes``
        / ``open_review_cost_eur`` (using exactly the ROI assumptions:
        ``open_finding_count × review_minutes_per_finding`` and
        ``open_review_minutes / 60 × hourly_rate``, rounded to two decimals) and
        ``top_invoice`` — the invoice with the largest ``open_amount`` in the
        bucket (ties: ``open_finding_count`` descending, then ``id`` ascending)
        with ``id``, ``invoice_number``, ``entity``, ``cost_center``,
        ``open_finding_count`` and ``open_amount``. ``top_invoice`` is ``None``
        for a bucket without a qualifying invoice.

        Rows are sorted by ``open_amount`` descending, then ``supplier`` and
        ``month`` ascending for deterministic output. ``totals`` mirrors the row
        shape (``month``/``supplier`` are ``None``) and is the exact sum over the
        returned rows (``top_invoice`` = the global largest). ``parameters``
        echoes the effective assumptions.

        The ``months`` window (default
        :data:`DEFAULT_REVIEW_WORKLOAD_TIMESERIES_MONTHS`, must be ``>= 1``)
        keeps only the newest ``months`` calendar months present in the filtered
        data (anchor: newest month). ``entities``/``cost_centers`` restrict the
        view like :meth:`list_invoices` (AND-combined); unknown values are not an
        error and simply yield an empty ``rows`` list. ``review_minutes`` and
        ``hourly_rate`` override the settings for what-if analyses.
        """
        base = settings or Settings()
        minutes = base.review_minutes_per_finding if review_minutes is None else float(
            review_minutes
        )
        rate = (
            base.review_hourly_rate_eur if hourly_rate is None else float(hourly_rate)
        )
        if minutes < 0:
            raise ValueError("review_minutes must be >= 0")
        if rate < 0:
            raise ValueError("hourly_rate must be >= 0")
        window = (
            DEFAULT_REVIEW_WORKLOAD_TIMESERIES_MONTHS
            if months is None
            else int(months)
        )
        if window < 1:
            raise ValueError("months must be >= 1")

        clauses: List[str] = []
        params: List[object] = []
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            clauses.append("i.entity IN (%s)" % placeholders)
            params.extend(entities)
        if cost_centers:
            placeholders = ", ".join("?" for _ in cost_centers)
            clauses.append("i.cost_center IN (%s)" % placeholders)
            params.extend(cost_centers)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        with self.connect() as conn:
            rows = conn.execute(
                """SELECT strftime('%%Y-%%m', i.created_at) AS month,
                          i.supplier AS supplier,
                          i.id AS id,
                          i.invoice_number AS invoice_number,
                          COALESCE(i.entity, 'default') AS entity,
                          COALESCE(i.cost_center, '') AS cost_center,
                          COALESCE(SUM(CASE WHEN f.id IS NOT NULL AND f.feedback IS NULL
                                            THEN 1 ELSE 0 END), 0) AS open_finding_count,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN f.amount ELSE 0 END), 0) AS open_amount
                   FROM invoices i
                   JOIN findings f ON f.invoice_id = i.id
                   %s
                   GROUP BY i.id
                   HAVING open_finding_count > 0"""
                % where,
                params,
            ).fetchall()

        months_present = [row["month"] for row in rows if row["month"]]
        cutoff = None
        if months_present:
            cutoff = _shift_month(max(months_present), -(window - 1))

        groups: dict = {}
        kept_invoices: List[dict] = []
        for row in rows:
            month = row["month"]
            if not month:
                continue
            if cutoff is not None and month < cutoff:
                continue
            supplier = row["supplier"]
            invoice = {
                "id": int(row["id"]),
                "invoice_number": row["invoice_number"],
                "entity": row["entity"] or "default",
                "cost_center": row["cost_center"] or "",
                "open_finding_count": int(row["open_finding_count"]),
                "open_amount": round(float(row["open_amount"]), 2),
            }
            kept_invoices.append(invoice)
            key = (month, supplier)
            group = groups.get(key)
            if group is None:
                group = {
                    "invoice_count": 0,
                    "open_finding_count": 0,
                    "open_amount": 0.0,
                    "invoices": [],
                }
                groups[key] = group
            group["invoice_count"] += 1
            group["open_finding_count"] += invoice["open_finding_count"]
            group["open_amount"] += invoice["open_amount"]
            group["invoices"].append(invoice)

        result_rows: List[dict] = []
        for (month, supplier), group in groups.items():
            open_count = group["open_finding_count"]
            open_minutes = round(open_count * minutes, 2)
            result_rows.append(
                {
                    "month": month,
                    "supplier": supplier,
                    "invoice_count": group["invoice_count"],
                    "open_finding_count": open_count,
                    "open_amount": round(group["open_amount"], 2),
                    "open_review_minutes": open_minutes,
                    "open_review_cost_eur": round(open_minutes / 60 * rate, 2),
                    "top_invoice": _pick_top_invoice(group["invoices"]),
                }
            )
        result_rows.sort(
            key=lambda entry: (
                -entry["open_amount"],
                entry["supplier"] or "",
                entry["month"],
            )
        )

        total_minutes = round(
            sum(entry["open_review_minutes"] for entry in result_rows), 2
        )
        totals = {
            "month": None,
            "supplier": None,
            "invoice_count": sum(entry["invoice_count"] for entry in result_rows),
            "open_finding_count": sum(
                entry["open_finding_count"] for entry in result_rows
            ),
            "open_amount": round(
                sum(entry["open_amount"] for entry in result_rows), 2
            ),
            "open_review_minutes": total_minutes,
            "open_review_cost_eur": round(
                sum(entry["open_review_cost_eur"] for entry in result_rows), 2
            ),
            "top_invoice": _pick_top_invoice(kept_invoices),
        }
        return {
            "rows": result_rows,
            "totals": totals,
            "parameters": {
                "months": window,
                "review_minutes_per_finding": minutes,
                "review_hourly_rate_eur": rate,
            },
        }

    def list_entities(self) -> List[str]:
        """Return the distinct, sorted entities occurring in invoices."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT entity FROM invoices
                   WHERE entity IS NOT NULL AND entity <> ''
                   ORDER BY entity"""
            ).fetchall()
        return [row["entity"] for row in rows]

    def list_cost_centers(self) -> List[str]:
        """Return the distinct, sorted non-empty cost centers of invoices."""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT DISTINCT cost_center FROM invoices
                   WHERE cost_center IS NOT NULL AND cost_center <> ''
                   ORDER BY cost_center"""
            ).fetchall()
        return [row["cost_center"] for row in rows]

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

    @staticmethod
    def _finding_filters(
        status: Optional[str],
        min_amount: Optional[float],
        invoice_id: Optional[int],
        entities: Optional[List[str]],
        cost_centers: Optional[List[str]],
        review_status: Optional[str] = None,
    ) -> tuple:
        """Build the shared WHERE clauses/params for findings queries."""
        clauses: List[str] = []
        params: List[object] = []
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            clauses.append("i.entity IN (%s)" % placeholders)
            params.extend(entities)
        if cost_centers:
            placeholders = ", ".join("?" for _ in cost_centers)
            clauses.append("i.cost_center IN (%s)" % placeholders)
            params.extend(cost_centers)
        if status:
            clauses.append("f.status = ?")
            params.append(status)
        if min_amount is not None:
            clauses.append("f.amount >= ?")
            params.append(float(min_amount))
        if invoice_id is not None:
            clauses.append("f.invoice_id = ?")
            params.append(int(invoice_id))
        if review_status and review_status != "all":
            if review_status == "open":
                clauses.append(
                    "f.invoice_id IN (SELECT invoice_id FROM findings "
                    "WHERE feedback IS NULL)"
                )
            elif review_status == "reviewed":
                clauses.append(
                    "f.invoice_id IN (SELECT invoice_id FROM findings "
                    "GROUP BY invoice_id "
                    "HAVING SUM(CASE WHEN feedback IS NULL THEN 1 ELSE 0 END) = 0)"
                )
            else:
                raise ValueError(
                    "review_status must be one of: %s"
                    % ", ".join(REVIEW_STATUS_VALUES)
                )
        return clauses, params

    def list_findings(
        self,
        status: Optional[str] = None,
        min_amount: Optional[float] = None,
        invoice_id: Optional[int] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
        review_status: Optional[str] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
        order_by: Optional[str] = None,
    ) -> List[dict]:
        """List findings, optionally filtered, sorted and paged.

        Every row carries the owning invoice's ``entity``, ``cost_center``,
        ``supplier`` and ``invoice_number`` so callers (e.g. the digest or the
        CSV export) can show the society, cost center and supplier per finding
        and build per-group breakdowns.
        ``entities=None`` applies no
        entity filter and stays identical to the previous behaviour.
        ``cost_centers`` filters on the owning invoice's cost center and is
        AND-combined with ``entities`` (an unknown value yields an empty list,
        not an error). ``None`` applies no cost-center filter.

        ``review_status`` adds the per-invoice review axis: ``open`` keeps the
        findings of invoices that have at least one finding without feedback,
        ``reviewed`` keeps those of invoices whose findings all have feedback,
        and ``None``/``all`` applies no filter (backwards compatible).

        ``order_by`` must be one of :data:`FINDING_ORDER_BY`; ``None`` keeps the
        documented default ordering (amount descending) and is fully backwards
        compatible. ``limit``/``offset`` are applied only when provided, so an
        unpaged call returns exactly the rows it always did.
        """
        if order_by is None:
            order_by = DEFAULT_FINDING_ORDER
        if order_by not in FINDING_ORDER_BY:
            raise ValueError("unknown order_by: %s" % order_by)
        if limit is not None and int(limit) < 0:
            raise ValueError("limit must be >= 0")
        if offset is not None and int(offset) < 0:
            raise ValueError("offset must be >= 0")

        query = (
            "SELECT f.*, i.entity AS entity, i.cost_center AS cost_center, "
            "i.supplier AS supplier, i.invoice_number AS invoice_number "
            "FROM findings f "
            "LEFT JOIN invoices i ON i.id = f.invoice_id"
        )
        clauses, params = self._finding_filters(
            status, min_amount, invoice_id, entities, cost_centers, review_status
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY " + FINDING_ORDER_BY[order_by]
        if limit is not None:
            query += " LIMIT ?"
            params.append(int(limit))
            if offset is not None:
                query += " OFFSET ?"
                params.append(int(offset))
        elif offset is not None:
            query += " LIMIT -1 OFFSET ?"
            params.append(int(offset))
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def count_findings(
        self,
        status: Optional[str] = None,
        min_amount: Optional[float] = None,
        invoice_id: Optional[int] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
        review_status: Optional[str] = None,
    ) -> int:
        """Count findings matching the same filters as :meth:`list_findings`.

        Used by the API to report ``total_count`` (the full filtered set)
        independently of any ``limit``/``offset`` window.
        """
        query = (
            "SELECT COUNT(*) AS c FROM findings f "
            "LEFT JOIN invoices i ON i.id = f.invoice_id"
        )
        clauses, params = self._finding_filters(
            status, min_amount, invoice_id, entities, cost_centers, review_status
        )
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        with self.connect() as conn:
            return int(conn.execute(query, params).fetchone()["c"])

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

    @staticmethod
    def _stats_counters(row: sqlite3.Row) -> dict:
        """Shape the flat KPI counters from one aggregate row."""
        confirmed = int(row["confirmed"])
        false_alarm = int(row["false_alarm"])
        reviewed = confirmed + false_alarm
        precision = (confirmed / reviewed) if reviewed else None
        return {
            "invoices": int(row["invoices"]),
            "findings": int(row["findings"]),
            "open_amount": round(float(row["open_amount"]), 2),
            "confirmed": confirmed,
            "false_alarm": false_alarm,
            "precision": round(precision, 4) if precision is not None else None,
        }

    @staticmethod
    def _stats_groups(rows: List[sqlite3.Row], key_name: str) -> List[dict]:
        """Shape per-group KPI rows and apply the documented ordering.

        Sorted by ``open_amount`` descending, then by the group key ascending so
        the order is deterministic for ties.
        """
        entries: List[dict] = []
        for row in rows:
            confirmed = int(row["confirmed"])
            false_alarm = int(row["false_alarm"])
            reviewed = confirmed + false_alarm
            precision = (confirmed / reviewed) if reviewed else None
            key = row["grp"]
            entries.append(
                {
                    key_name: "" if key is None else key,
                    "findings": int(row["findings"]),
                    "confirmed": confirmed,
                    "false_alarm": false_alarm,
                    "open": int(row["open"]),
                    "open_amount": round(float(row["open_amount"]), 2),
                    "precision": round(precision, 4)
                    if precision is not None
                    else None,
                }
            )
        entries.sort(key=lambda entry: (-entry["open_amount"], entry[key_name]))
        return entries

    def stats(
        self,
        settings: Optional[Settings] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
    ) -> dict:
        """Flat KPIs, optionally sliced by society and/or cost center.

        Always returns the six flat counters (``invoices``, ``findings``,
        ``open_amount``, ``confirmed``, ``false_alarm``, ``precision``) plus two
        grouped blocks:

        - ``entities``: one entry per society occurring in the (filtered) invoices,
        - ``cost_centers``: one entry per cost center (empty rendered as ``""``).

        Each entry carries ``findings``, ``confirmed``, ``false_alarm``,
        ``open``, ``open_amount`` and ``precision`` and is sorted by
        ``open_amount`` descending, then by the group key ascending.

        Without filters the flat counters equal the historical global values and
        the blocks describe the same full set. With ``entities``/``cost_centers``
        the flat counters and both blocks are computed over exactly the matching
        invoices. The two filters are AND-combined; unknown values are not an
        error and simply yield empty blocks and zeroed counters. Only the
        existing aggregates are reused — no new tables or columns are required.
        The ``settings`` argument is accepted for symmetry with the other
        reports and is currently unused.

        Note: :meth:`radar.app.RadarHandler.h_stats` omits the two blocks when no
        filter is supplied so the HTTP response stays byte-for-byte identical to
        the pre-breakdown contract.
        """
        entity_filter = list(entities) if entities else []
        cost_center_filter = list(cost_centers) if cost_centers else []
        clauses: List[str] = []
        params: List[object] = []
        if entity_filter:
            placeholders = ", ".join("?" for _ in entity_filter)
            clauses.append("i.entity IN (%s)" % placeholders)
            params.extend(entity_filter)
        if cost_center_filter:
            placeholders = ", ".join("?" for _ in cost_center_filter)
            clauses.append("i.cost_center IN (%s)" % placeholders)
            params.extend(cost_center_filter)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        with self.connect() as conn:
            totals = conn.execute(
                """SELECT COUNT(DISTINCT i.id) AS invoices,
                          COUNT(f.id) AS findings,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN f.amount ELSE 0 END), 0) AS open_amount,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN 1 ELSE 0 END), 0) AS confirmed,
                          COALESCE(SUM(CASE WHEN f.feedback = 'false_alarm'
                                            THEN 1 ELSE 0 END), 0) AS false_alarm
                   FROM invoices i
                   LEFT JOIN findings f ON f.invoice_id = i.id
                   %s""" % where,
                params,
            ).fetchone()
            result = self._stats_counters(totals)
            grouped: dict = {}
            for key_name, expression in (
                ("entity", "COALESCE(i.entity, 'default')"),
                ("cost_center", "COALESCE(i.cost_center, '')"),
            ):
                grouped[key_name] = conn.execute(
                    """SELECT %s AS grp,
                              COUNT(f.id) AS findings,
                              COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                                THEN 1 ELSE 0 END), 0) AS confirmed,
                              COALESCE(SUM(CASE WHEN f.feedback = 'false_alarm'
                                                THEN 1 ELSE 0 END), 0) AS false_alarm,
                              COALESCE(SUM(CASE WHEN f.status = 'open'
                                                THEN 1 ELSE 0 END), 0) AS open,
                              COALESCE(SUM(CASE WHEN f.status = 'open'
                                                THEN f.amount ELSE 0 END), 0) AS open_amount
                       FROM invoices i
                       LEFT JOIN findings f ON f.invoice_id = i.id
                       %s
                       GROUP BY %s""" % (expression, where, expression),
                    params,
                ).fetchall()
        result["entities"] = self._stats_groups(grouped["entity"], "entity")
        result["cost_centers"] = self._stats_groups(
            grouped["cost_center"], "cost_center"
        )
        return result

    def _workload_rollup(
        self,
        group_expression: str,
        key_name: str,
        settings: Optional[Settings] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
        review_minutes: Optional[float] = None,
        hourly_rate: Optional[float] = None,
    ) -> List[dict]:
        """Aggregate open review load and realized ROI per ``key_name`` group.

        Shared by :meth:`entity_rollup` and :meth:`supplier_rollup`. Groups the
        (filtered) invoices by ``group_expression`` and combines, per group, the
        currently bound review load (``open_finding_count``, ``open_amount``,
        ``open_review_minutes``, ``open_review_cost_eur``) with the benefit
        realized from feedback (``confirmed``, ``false_alarm``, ``precision``,
        ``avoided_review_cost_eur``, ``avoided_mispayment_eur``). The effort
        fields follow exactly the ROI assumptions
        (``open_review_minutes = open_finding_count × review_minutes``,
        ``open_review_cost_eur = open_review_minutes / 60 × rate``, rounded to
        two decimals) and ``avoided_mispayment_eur`` is the summed amount of
        confirmed findings. Returns the unsorted rows; the caller applies its
        documented ordering and builds the ``totals`` row.
        """
        base = settings or Settings()
        minutes = (
            base.review_minutes_per_finding
            if review_minutes is None
            else float(review_minutes)
        )
        rate = (
            base.review_hourly_rate_eur
            if hourly_rate is None
            else float(hourly_rate)
        )
        if minutes < 0:
            raise ValueError("review_minutes must be >= 0")
        if rate < 0:
            raise ValueError("hourly_rate must be >= 0")

        clauses: List[str] = []
        params: List[object] = []
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            clauses.append("i.entity IN (%s)" % placeholders)
            params.extend(entities)
        if cost_centers:
            placeholders = ", ".join("?" for _ in cost_centers)
            clauses.append("i.cost_center IN (%s)" % placeholders)
            params.extend(cost_centers)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        with self.connect() as conn:
            rows = conn.execute(
                """SELECT %s AS grp,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN 1 ELSE 0 END), 0)
                              AS open_finding_count,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN f.amount ELSE 0 END), 0)
                              AS open_amount,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN 1 ELSE 0 END), 0) AS confirmed,
                          COALESCE(SUM(CASE WHEN f.feedback = 'false_alarm'
                                            THEN 1 ELSE 0 END), 0) AS false_alarm,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN f.amount ELSE 0 END), 0)
                              AS confirmed_amount
                   FROM invoices i
                   LEFT JOIN findings f ON f.invoice_id = i.id
                   %s
                   GROUP BY %s"""
                % (group_expression, where, group_expression),
                params,
            ).fetchall()

        rows_out: List[dict] = []
        for row in rows:
            confirmed = int(row["confirmed"])
            false_alarm = int(row["false_alarm"])
            reviewed = confirmed + false_alarm
            precision = (confirmed / reviewed) if reviewed else None
            open_count = int(row["open_finding_count"])
            open_minutes = round(open_count * minutes, 2)
            avoided_minutes = round(confirmed * minutes, 2)
            rows_out.append(
                {
                    key_name: row["grp"],
                    "open_finding_count": open_count,
                    "open_amount": round(float(row["open_amount"]), 2),
                    "open_review_minutes": open_minutes,
                    "open_review_cost_eur": round(open_minutes / 60 * rate, 2),
                    "confirmed": confirmed,
                    "false_alarm": false_alarm,
                    "precision": round(precision, 4)
                    if precision is not None
                    else None,
                    "avoided_review_cost_eur": round(
                        avoided_minutes / 60 * rate, 2
                    ),
                    "avoided_mispayment_eur": round(
                        float(row["confirmed_amount"]), 2
                    ),
                }
            )
        return rows_out

    def _rollup_totals(self, rows_out: List[dict], key_name: str) -> dict:
        """Sum per-group rollup rows into a ``totals`` row of the same shape.

        The totals row carries exactly the same fields with the group key set to
        ``None`` and is the exact sum over ``rows_out`` for every numeric field;
        ``precision`` is recomputed from the aggregated counts (never averaged).
        """
        total_confirmed = sum(entry["confirmed"] for entry in rows_out)
        total_false_alarm = sum(entry["false_alarm"] for entry in rows_out)
        reviewed_total = total_confirmed + total_false_alarm
        total_precision = (
            (total_confirmed / reviewed_total) if reviewed_total else None
        )
        return {
            key_name: None,
            "open_finding_count": sum(
                entry["open_finding_count"] for entry in rows_out
            ),
            "open_amount": round(
                sum(entry["open_amount"] for entry in rows_out), 2
            ),
            "open_review_minutes": round(
                sum(entry["open_review_minutes"] for entry in rows_out), 2
            ),
            "open_review_cost_eur": round(
                sum(entry["open_review_cost_eur"] for entry in rows_out), 2
            ),
            "confirmed": total_confirmed,
            "false_alarm": total_false_alarm,
            "precision": round(total_precision, 4)
            if total_precision is not None
            else None,
            "avoided_review_cost_eur": round(
                sum(entry["avoided_review_cost_eur"] for entry in rows_out), 2
            ),
            "avoided_mispayment_eur": round(
                sum(entry["avoided_mispayment_eur"] for entry in rows_out), 2
            ),
        }

    def entity_rollup(
        self,
        settings: Optional[Settings] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
        review_minutes: Optional[float] = None,
        hourly_rate: Optional[float] = None,
    ) -> dict:
        """Consolidated per-society workload and ROI for the weekly digest.

        For every society present in the (filtered) invoices this emits one row
        that combines the currently bound review load — ``open_finding_count``,
        ``open_amount``, ``open_review_minutes`` and ``open_review_cost_eur`` —
        with the benefit realized from feedback — ``confirmed``,
        ``false_alarm``, ``precision``, ``avoided_review_cost_eur`` and
        ``avoided_mispayment_eur`` (the summed amount of confirmed findings).

        ``open_amount`` matches ``GET /api/stats?entity=`` and the effort fields
        follow exactly the ROI assumptions (see :meth:`_workload_rollup`).

        A society without open findings still appears with zeros. Rows are
        sorted by ``open_amount`` descending, then ``entity`` ascending. The
        returned ``totals`` row carries the exact same fields (``entity`` is
        ``None``) and sums the per-society rows; ``precision`` is recomputed
        from the aggregated counts rather than averaged.

        ``entities``/``cost_centers`` restrict the view exactly like
        :meth:`list_invoices` (AND-combined); unknown values are not an error
        and yield empty rows with a zeroed totals row. ``review_minutes`` and
        ``hourly_rate`` override the settings for what-if analyses. Only the
        existing ``invoices``/``findings`` aggregates are reused — no new
        tables, columns or runtime dependencies.
        """
        rows_out = self._workload_rollup(
            "COALESCE(i.entity, 'default')",
            "entity",
            settings,
            entities,
            cost_centers,
            review_minutes,
            hourly_rate,
        )
        rows_out.sort(key=lambda entry: (-entry["open_amount"], entry["entity"]))
        return {
            "entities": rows_out,
            "totals": self._rollup_totals(rows_out, "entity"),
        }

    def supplier_rollup(
        self,
        settings: Optional[Settings] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
        review_minutes: Optional[float] = None,
        hourly_rate: Optional[float] = None,
    ) -> dict:
        """Consolidated per-supplier workload and ROI for the weekly digest.

        Mirrors :meth:`entity_rollup` but groups by ``invoices.supplier``, so the
        digest answers the procurement question *which supplier currently binds
        the most open review load, and how has it developed?* using the same
        fields as the per-society rollup: the bound review load
        (``open_finding_count``, ``open_amount``, ``open_review_minutes``,
        ``open_review_cost_eur``) and the benefit realized from feedback
        (``confirmed``, ``false_alarm``, ``precision``,
        ``avoided_review_cost_eur``, ``avoided_mispayment_eur``). The effort
        fields follow exactly the same ROI assumptions (see
        :meth:`_workload_rollup`), and ``open_amount`` matches the ``open_amount``
        reported by ``GET /api/stats`` for the same scope.

        A supplier without open findings still appears with zeros. Rows are
        sorted by ``open_amount`` descending, then ``open_finding_count``
        descending, then ``supplier`` ascending (fully deterministic). ``totals``
        carries the exact same fields (``supplier`` is ``None``) and sums the
        per-supplier rows; ``precision`` is recomputed from the aggregated
        counts rather than averaged.

        ``entities``/``cost_centers`` restrict the view exactly like
        :meth:`list_invoices` (AND-combined); unknown values are not an error
        and yield empty rows with a zeroed totals row. ``review_minutes`` and
        ``hourly_rate`` override the settings for what-if analyses. Only the
        existing ``invoices``/``findings`` aggregates are reused — no new
        tables, columns or runtime dependencies.
        """
        rows_out = self._workload_rollup(
            "i.supplier",
            "supplier",
            settings,
            entities,
            cost_centers,
            review_minutes,
            hourly_rate,
        )
        rows_out.sort(
            key=lambda entry: (
                -entry["open_amount"],
                -entry["open_finding_count"],
                entry["supplier"] or "",
            )
        )
        return {
            "entities": rows_out,
            "totals": self._rollup_totals(rows_out, "supplier"),
        }

    def stats_by_supplier(
        self,
        settings: Optional[Settings] = None,
        entities: Optional[List[str]] = None,
    ) -> List[dict]:
        """Per-supplier breakdown of findings, feedback and applied thresholds.

        Returns one entry per known supplier, sorted by open deviation volume
        (descending) and then by supplier name. Suppliers without findings are
        included with zeros and ``precision: null``. ``applied_rule`` mirrors
        :func:`radar.config.effective_rule` so callers can see which supplier
        overrides were in effect without a second request. When ``entities`` is
        given the view is restricted to invoices of those societies; suppliers
        without any invoice in scope are omitted.
        """
        base = settings or Settings()
        entity_clause = ""
        params: List[object] = []
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            entity_clause = " WHERE i.entity IN (%s)" % placeholders
            params = list(entities)
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT s.name AS supplier,
                          COUNT(f.id) AS findings,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN 1 ELSE 0 END), 0) AS confirmed,
                          COALESCE(SUM(CASE WHEN f.feedback = 'false_alarm'
                                            THEN 1 ELSE 0 END), 0) AS false_alarm,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN f.amount ELSE 0 END), 0) AS open_amount
                   FROM suppliers s
                   LEFT JOIN invoices i ON i.supplier = s.name
                   LEFT JOIN findings f ON f.invoice_id = i.id
                   %s
                   GROUP BY s.name"""
                % entity_clause,
                params,
            ).fetchall()

        results: List[dict] = []
        for row in rows:
            supplier = row["supplier"]
            confirmed = int(row["confirmed"])
            false_alarm = int(row["false_alarm"])
            reviewed = confirmed + false_alarm
            precision = (confirmed / reviewed) if reviewed else None
            rule = self.get_supplier_rule(supplier)
            results.append(
                {
                    "supplier": supplier,
                    "findings": int(row["findings"]),
                    "confirmed": confirmed,
                    "false_alarm": false_alarm,
                    "open_amount": round(float(row["open_amount"]), 2),
                    "precision": round(precision, 4) if precision is not None else None,
                    "applied_rule": effective_rule(base, rule),
                }
            )
        results.sort(key=lambda entry: (-entry["open_amount"], entry["supplier"]))
        return results

    def roi_report(
        self,
        settings: Optional[Settings] = None,
        review_minutes: Optional[float] = None,
        hourly_rate: Optional[float] = None,
        article_limit: Optional[int] = None,
        entities: Optional[List[str]] = None,
    ) -> dict:
        """Turn accumulated feedback into a defensible ROI number.

        For every known supplier the report derives the number of findings,
        confirmed hits, false alarms, still-open findings (and their open
        amount) plus the precision from the *same* semantics as
        :meth:`stats_by_supplier`. On top it converts the confirmed hits into
        avoided review effort: ``confirmed × review_minutes`` minutes and, in
        aggregate, the corresponding cost in euro. All figures come from
        ``findings``, ``invoices``, ``invoice_items`` and ``suppliers`` — no new
        tables.

        ``articles`` is an *additional* view that groups the same findings by
        (supplier, article) using :func:`normalize_text`, so recurring, costly
        article groups become visible. Each entry uses the same parameters and
        formula as the supplier block; findings without an invoice position
        (e.g. ``missing_po``/``duplicate``) are grouped under
        :data:`NO_POSITION_LABEL`. The list is sorted by avoided cost
        (descending), then confirmed amount, and capped to ``article_limit``
        entries (default :data:`DEFAULT_ARTICLE_LIMIT`, ``>= 0``). The article
        view never changes ``suppliers`` or ``totals``.

        ``entities`` is an *additional* grouped view and filter for the
        multi-entity rollup: when given, findings/invoices are restricted to
        those societies, ``suppliers``/``articles`` are filtered accordingly and
        ``totals`` covers exactly the filtered societies. The ``entities`` block
        uses the same fields as ``suppliers`` and is sorted by avoided cost
        (descending), then confirmed amount. ``entities=None`` applies no filter
        (all societies), which for pre-rollup data equals the ``default``
        society. An unknown filter yields an empty ``entities`` list and zeroed
        totals rather than an error.

        ``review_minutes`` and ``hourly_rate`` default to the values from
        ``settings`` and may be overridden per call for what-if analyses.
        """
        base = settings or Settings()
        minutes = base.review_minutes_per_finding if review_minutes is None else float(
            review_minutes
        )
        rate = (
            base.review_hourly_rate_eur if hourly_rate is None else float(hourly_rate)
        )
        if minutes < 0:
            raise ValueError("review_minutes must be >= 0")
        if rate < 0:
            raise ValueError("hourly_rate must be >= 0")
        limit = (
            DEFAULT_ARTICLE_LIMIT if article_limit is None else int(article_limit)
        )
        if limit < 0:
            raise ValueError("article_limit must be >= 0")

        entity_clause = ""
        entity_params: List[object] = []
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            entity_clause = " WHERE i.entity IN (%s)" % placeholders
            entity_params = list(entities)

        with self.connect() as conn:
            rows = conn.execute(
                """SELECT s.name AS supplier,
                          COUNT(f.id) AS findings,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN 1 ELSE 0 END), 0) AS confirmed,
                          COALESCE(SUM(CASE WHEN f.feedback = 'false_alarm'
                                            THEN 1 ELSE 0 END), 0) AS false_alarm,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN 1 ELSE 0 END), 0) AS open,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN f.amount ELSE 0 END), 0) AS open_amount,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN f.amount ELSE 0 END), 0) AS confirmed_amount
                   FROM suppliers s
                   LEFT JOIN invoices i ON i.supplier = s.name
                   LEFT JOIN findings f ON f.invoice_id = i.id
                   %s
                   GROUP BY s.name"""
                % entity_clause,
                entity_params,
            ).fetchall()
            article_rows = conn.execute(
                """SELECT i.supplier AS supplier,
                          COALESCE(ii.article_norm, '') AS article_norm,
                          MAX(COALESCE(ii.article, '')) AS article,
                          COUNT(f.id) AS findings,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN 1 ELSE 0 END), 0) AS confirmed,
                          COALESCE(SUM(CASE WHEN f.feedback = 'false_alarm'
                                            THEN 1 ELSE 0 END), 0) AS false_alarm,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN 1 ELSE 0 END), 0) AS open,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN f.amount ELSE 0 END), 0) AS open_amount,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN f.amount ELSE 0 END), 0) AS confirmed_amount
                   FROM findings f
                   JOIN invoices i ON i.id = f.invoice_id
                   LEFT JOIN invoice_items ii ON ii.id = f.invoice_item_id
                   %s
                   GROUP BY i.supplier, COALESCE(ii.article_norm, '')"""
                % entity_clause,
                entity_params,
            ).fetchall()
            entity_rows = conn.execute(
                """SELECT i.entity AS entity,
                          COUNT(f.id) AS findings,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN 1 ELSE 0 END), 0) AS confirmed,
                          COALESCE(SUM(CASE WHEN f.feedback = 'false_alarm'
                                            THEN 1 ELSE 0 END), 0) AS false_alarm,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN 1 ELSE 0 END), 0) AS open,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN f.amount ELSE 0 END), 0) AS open_amount,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN f.amount ELSE 0 END), 0) AS confirmed_amount
                   FROM invoices i
                   LEFT JOIN findings f ON f.invoice_id = i.id
                   %s
                   GROUP BY i.entity"""
                % entity_clause,
                entity_params,
            ).fetchall()

        suppliers: List[dict] = []
        total_confirmed = 0
        total_false_alarm = 0
        total_open = 0
        total_open_amount = 0.0
        total_confirmed_amount = 0.0

        for row in rows:
            confirmed = int(row["confirmed"])
            false_alarm = int(row["false_alarm"])
            open_count = int(row["open"])
            open_amount = float(row["open_amount"])
            confirmed_amount = float(row["confirmed_amount"])
            reviewed = confirmed + false_alarm
            precision = (confirmed / reviewed) if reviewed else None
            avoided_minutes = round(confirmed * minutes, 2)

            total_confirmed += confirmed
            total_false_alarm += false_alarm
            total_open += open_count
            total_open_amount += open_amount
            total_confirmed_amount += confirmed_amount

            suppliers.append(
                {
                    "supplier": row["supplier"],
                    "findings": int(row["findings"]),
                    "confirmed": confirmed,
                    "false_alarm": false_alarm,
                    "open": open_count,
                    "open_amount": round(open_amount, 2),
                    "precision": round(precision, 4)
                    if precision is not None
                    else None,
                    "confirmed_amount": round(confirmed_amount, 2),
                    "avoided_review_minutes": avoided_minutes,
                    "avoided_review_cost_eur": round(
                        avoided_minutes / 60 * rate, 2
                    ),
                }
            )

        suppliers.sort(
            key=lambda entry: (
                -entry["avoided_review_minutes"],
                -entry["open_amount"],
                entry["supplier"],
            )
        )

        articles: List[dict] = []
        for row in article_rows:
            confirmed = int(row["confirmed"])
            false_alarm = int(row["false_alarm"])
            reviewed = confirmed + false_alarm
            precision = (confirmed / reviewed) if reviewed else None
            avoided_minutes = round(confirmed * minutes, 2)
            articles.append(
                {
                    "article": row["article"] or NO_POSITION_LABEL,
                    "supplier": row["supplier"],
                    "findings": int(row["findings"]),
                    "confirmed": confirmed,
                    "false_alarm": false_alarm,
                    "open": int(row["open"]),
                    "open_amount": round(float(row["open_amount"]), 2),
                    "precision": round(precision, 4)
                    if precision is not None
                    else None,
                    "confirmed_amount": round(float(row["confirmed_amount"]), 2),
                    "avoided_review_minutes": avoided_minutes,
                    "avoided_review_cost_eur": round(avoided_minutes / 60 * rate, 2),
                }
            )
        articles.sort(
            key=lambda entry: (
                -entry["avoided_review_cost_eur"],
                -entry["confirmed_amount"],
                entry["article"],
                entry["supplier"],
            )
        )
        articles = articles[:limit]

        entities_out: List[dict] = []
        for row in entity_rows:
            confirmed = int(row["confirmed"])
            false_alarm = int(row["false_alarm"])
            reviewed = confirmed + false_alarm
            precision = (confirmed / reviewed) if reviewed else None
            avoided_minutes = round(confirmed * minutes, 2)
            entities_out.append(
                {
                    "entity": row["entity"] or "default",
                    "findings": int(row["findings"]),
                    "confirmed": confirmed,
                    "false_alarm": false_alarm,
                    "open": int(row["open"]),
                    "open_amount": round(float(row["open_amount"]), 2),
                    "precision": round(precision, 4)
                    if precision is not None
                    else None,
                    "confirmed_amount": round(float(row["confirmed_amount"]), 2),
                    "avoided_review_minutes": avoided_minutes,
                    "avoided_review_cost_eur": round(avoided_minutes / 60 * rate, 2),
                }
            )
        entities_out.sort(
            key=lambda entry: (
                -entry["avoided_review_cost_eur"],
                -entry["confirmed_amount"],
                entry["entity"],
            )
        )

        reviewed_total = total_confirmed + total_false_alarm
        total_precision = (total_confirmed / reviewed_total) if reviewed_total else None
        total_minutes = round(total_confirmed * minutes, 2)
        totals = {
            "suppliers": len(suppliers),
            "findings": sum(entry["findings"] for entry in suppliers),
            "confirmed": total_confirmed,
            "false_alarm": total_false_alarm,
            "open": total_open,
            "open_amount": round(total_open_amount, 2),
            "precision": round(total_precision, 4)
            if total_precision is not None
            else None,
            "confirmed_amount": round(total_confirmed_amount, 2),
            "avoided_review_minutes": total_minutes,
            "avoided_review_cost_eur": round(total_minutes / 60 * rate, 2),
        }
        return {
            "suppliers": suppliers,
            "articles": articles,
            "entities": entities_out,
            "totals": totals,
            "parameters": {
                "review_minutes_per_finding": minutes,
                "hourly_rate_eur": rate,
                "article_limit": limit,
            },
        }

    def roi_timeseries(
        self,
        settings: Optional[Settings] = None,
        months: Optional[int] = None,
        entities: Optional[List[str]] = None,
        cost_centers: Optional[List[str]] = None,
        review_minutes: Optional[float] = None,
        hourly_rate: Optional[float] = None,
    ) -> dict:
        """Time-bucketed, cost-center-aware ROI report.

        Findings are grouped by calendar month (derived from
        ``invoices.invoice_date``, falling back to ``invoices.created_at`` for
        invoices without a date), by ``invoices.entity`` and by
        ``invoices.cost_center``. Each bucket uses exactly the same formulas as
        :meth:`roi_report`: ``avoided_review_minutes = confirmed × minutes`` and
        ``avoided_review_cost_eur = minutes / 60 × rate``; ``confirmed_amount``
        sums the amounts of confirmed findings and ``open_amount`` the amounts
        of still-open findings.

        On top, each bucket exposes the currently bound review load:
        ``open`` counts the still-open findings, ``reviewed = confirmed +
        false_alarm`` the already graded ones, and the bound effort
        ``open_review_minutes = open × minutes`` and
        ``open_review_cost_eur = open_review_minutes / 60 × rate`` (rounded to
        two decimals). ``totals`` carries the same fields and is the exact sum
        over the returned buckets — including ``open``, ``reviewed``,
        ``open_review_minutes`` and ``open_review_cost_eur``.

        The ``months`` window (default ``12``, must be ``>= 1``) keeps only the
        buckets within the newest ``months`` calendar months; the anchor is the
        newest month present in the filtered data, so the report stays
        deterministic even when the data lies in the past. ``entities`` and
        ``cost_centers`` restrict the view; unknown values are not an error and
        simply yield an empty ``buckets`` list with zeroed totals.

        Returns ``buckets`` sorted by ``month`` ascending, then ``entity``, then
        ``cost_center``; ``totals`` is the exact sum over the returned buckets
        with the precision recomputed from the aggregated counts (never
        averaged); ``parameters`` echoes the effective assumptions and filters.
        """
        base = settings or Settings()
        minutes = base.review_minutes_per_finding if review_minutes is None else float(
            review_minutes
        )
        rate = (
            base.review_hourly_rate_eur if hourly_rate is None else float(hourly_rate)
        )
        if minutes < 0:
            raise ValueError("review_minutes must be >= 0")
        if rate < 0:
            raise ValueError("hourly_rate must be >= 0")
        window = 12 if months is None else int(months)
        if window < 1:
            raise ValueError("months must be >= 1")

        clauses: List[str] = []
        params: List[object] = []
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            clauses.append("i.entity IN (%s)" % placeholders)
            params.extend(entities)
        if cost_centers:
            placeholders = ", ".join("?" for _ in cost_centers)
            clauses.append("i.cost_center IN (%s)" % placeholders)
            params.extend(cost_centers)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

        with self.connect() as conn:
            rows = conn.execute(
                """SELECT substr(COALESCE(i.invoice_date, i.created_at), 1, 7) AS month,
                          i.entity AS entity,
                          COALESCE(i.cost_center, '') AS cost_center,
                          COUNT(f.id) AS findings,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN 1 ELSE 0 END), 0) AS confirmed,
                          COALESCE(SUM(CASE WHEN f.feedback = 'false_alarm'
                                            THEN 1 ELSE 0 END), 0) AS false_alarm,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN 1 ELSE 0 END), 0) AS open,
                          COALESCE(SUM(CASE WHEN f.status = 'open'
                                            THEN f.amount ELSE 0 END), 0) AS open_amount,
                          COALESCE(SUM(CASE WHEN f.feedback = 'confirmed'
                                            THEN f.amount ELSE 0 END), 0) AS confirmed_amount
                   FROM findings f
                   JOIN invoices i ON i.id = f.invoice_id
                   %s
                   GROUP BY month, i.entity, i.cost_center"""
                % where,
                params,
            ).fetchall()

        months_present = [row["month"] for row in rows if row["month"]]
        cutoff = None
        if months_present:
            cutoff = _shift_month(max(months_present), -(window - 1))

        buckets: List[dict] = []
        for row in rows:
            month = row["month"]
            if cutoff is not None and month < cutoff:
                continue
            confirmed = int(row["confirmed"])
            false_alarm = int(row["false_alarm"])
            reviewed = confirmed + false_alarm
            precision = (confirmed / reviewed) if reviewed else None
            avoided_minutes = round(confirmed * minutes, 2)
            open_count = int(row["open"])
            open_minutes = round(open_count * minutes, 2)
            buckets.append(
                {
                    "month": month,
                    "entity": row["entity"] or "default",
                    "cost_center": row["cost_center"] or "",
                    "findings": int(row["findings"]),
                    "confirmed": confirmed,
                    "false_alarm": false_alarm,
                    "reviewed": reviewed,
                    "open": open_count,
                    "open_amount": round(float(row["open_amount"]), 2),
                    "precision": round(precision, 4)
                    if precision is not None
                    else None,
                    "confirmed_amount": round(float(row["confirmed_amount"]), 2),
                    "avoided_review_minutes": avoided_minutes,
                    "avoided_review_cost_eur": round(avoided_minutes / 60 * rate, 2),
                    "open_review_minutes": open_minutes,
                    "open_review_cost_eur": round(open_minutes / 60 * rate, 2),
                }
            )
        buckets.sort(
            key=lambda entry: (entry["month"], entry["entity"], entry["cost_center"])
        )

        total_confirmed = sum(entry["confirmed"] for entry in buckets)
        total_false_alarm = sum(entry["false_alarm"] for entry in buckets)
        reviewed_total = total_confirmed + total_false_alarm
        total_precision = (
            (total_confirmed / reviewed_total) if reviewed_total else None
        )
        total_minutes = round(
            sum(entry["avoided_review_minutes"] for entry in buckets), 2
        )
        totals = {
            "buckets": len(buckets),
            "findings": sum(entry["findings"] for entry in buckets),
            "confirmed": total_confirmed,
            "false_alarm": total_false_alarm,
            "reviewed": reviewed_total,
            "open": sum(entry["open"] for entry in buckets),
            "open_amount": round(sum(entry["open_amount"] for entry in buckets), 2),
            "precision": round(total_precision, 4)
            if total_precision is not None
            else None,
            "confirmed_amount": round(
                sum(entry["confirmed_amount"] for entry in buckets), 2
            ),
            "avoided_review_minutes": total_minutes,
            "avoided_review_cost_eur": round(total_minutes / 60 * rate, 2),
            "open_review_minutes": round(
                sum(entry["open_review_minutes"] for entry in buckets), 2
            ),
            "open_review_cost_eur": round(
                sum(entry["open_review_cost_eur"] for entry in buckets), 2
            ),
        }
        return {
            "buckets": buckets,
            "totals": totals,
            "parameters": {
                "months": window,
                "review_minutes_per_finding": minutes,
                "hourly_rate_eur": rate,
                "entities": list(entities) if entities else [],
                "cost_centers": list(cost_centers) if cost_centers else [],
            },
        }

    def rule_impact(
        self,
        settings: Optional[Settings] = None,
        entities: Optional[List[str]] = None,
    ) -> List[dict]:
        """Before/after comparison around each supplier's latest rule update.

        Only suppliers with at least one :data:`supplier_rule_history` entry are
        reported. The most recent history entry (highest ``id``) marks the
        ``changed_at`` cut-off: findings created *before* it form ``before``,
        findings created *at or after* it form ``after``. Each period carries
        ``findings``, ``confirmed``, ``false_alarm``, ``precision`` (``null``
        without feedback) and ``open_amount``. ``applied_rule`` reflects the
        currently effective rule. Sorted by ``before.open_amount`` descending.
        When ``entities`` is given, only findings of those societies are counted.
        """
        base = settings or Settings()
        entity_clause = ""
        if entities:
            placeholders = ", ".join("?" for _ in entities)
            entity_clause = " AND i.entity IN (%s)" % placeholders
        periods: List[tuple] = []
        with self.connect() as conn:
            latest_rows = conn.execute(
                """SELECT h.supplier AS supplier, h.changed_at AS changed_at
                   FROM supplier_rule_history h
                   JOIN (
                       SELECT supplier, MAX(id) AS id
                       FROM supplier_rule_history
                       GROUP BY supplier
                   ) latest ON latest.id = h.id"""
            ).fetchall()
            for row in latest_rows:
                supplier = row["supplier"]
                changed_at = row["changed_at"]
                aggregate = conn.execute(
                    """SELECT
                         COALESCE(SUM(CASE WHEN f.created_at < ? THEN 1 ELSE 0 END), 0) AS before_findings,
                         COALESCE(SUM(CASE WHEN f.created_at < ? AND f.feedback = 'confirmed' THEN 1 ELSE 0 END), 0) AS before_confirmed,
                         COALESCE(SUM(CASE WHEN f.created_at < ? AND f.feedback = 'false_alarm' THEN 1 ELSE 0 END), 0) AS before_false_alarm,
                         COALESCE(SUM(CASE WHEN f.created_at < ? AND f.status = 'open' THEN f.amount ELSE 0 END), 0) AS before_open_amount,
                         COALESCE(SUM(CASE WHEN f.created_at >= ? THEN 1 ELSE 0 END), 0) AS after_findings,
                         COALESCE(SUM(CASE WHEN f.created_at >= ? AND f.feedback = 'confirmed' THEN 1 ELSE 0 END), 0) AS after_confirmed,
                         COALESCE(SUM(CASE WHEN f.created_at >= ? AND f.feedback = 'false_alarm' THEN 1 ELSE 0 END), 0) AS after_false_alarm,
                         COALESCE(SUM(CASE WHEN f.created_at >= ? AND f.status = 'open' THEN f.amount ELSE 0 END), 0) AS after_open_amount
                       FROM findings f
                       JOIN invoices i ON i.id = f.invoice_id
                       WHERE i.supplier = ?""" + entity_clause,
                    (
                        changed_at,
                        changed_at,
                        changed_at,
                        changed_at,
                        changed_at,
                        changed_at,
                        changed_at,
                        changed_at,
                        supplier,
                    )
                    + (tuple(entities) if entities else ()),
                ).fetchone()
                periods.append((supplier, changed_at, aggregate))

        results: List[dict] = []
        for supplier, changed_at, aggregate in periods:
            results.append(
                {
                    "supplier": supplier,
                    "changed_at": changed_at,
                    "before": _impact_period(aggregate, "before"),
                    "after": _impact_period(aggregate, "after"),
                    "applied_rule": effective_rule(
                        base, self.get_supplier_rule(supplier)
                    ),
                }
            )
        results.sort(
            key=lambda entry: (-entry["before"]["open_amount"], entry["supplier"])
        )
        return results

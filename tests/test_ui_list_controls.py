"""Tests for the pagination/sort controls of the web interface.

The dashboard is a single dependency-free HTML file with inline JavaScript.
These tests pin down the *client* query building without launching a browser:
the pure helpers are extracted from ``index.html`` and exercised with Node (the
same approach as ``test_dashboard_security``), plus structural assertions that
always run so the documented defaults stay in sync with the API.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from radar.db import (  # noqa: E402
    DEFAULT_FINDING_ORDER,
    DEFAULT_INVOICE_ORDER,
    DEFAULT_SUPPLIER_RULE_ORDER,
    FINDING_ORDER_BY,
    INVOICE_ORDER_BY,
    SUPPLIER_RULE_ORDER_BY,
)

_INDEX = os.path.join(_HERE, "..", "src", "radar", "static", "index.html")


def _read_index():
    with open(_INDEX, encoding="utf-8") as handle:
        return handle.read()


def _extract_function(source, name):
    marker = "function %s(" % name
    start = source.index(marker)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError("unbalanced braces in %s()" % name)


def _run_js(program):
    result = subprocess.run(
        ["node", "-e", program],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(result.stdout)


class ClientControlStructureTests(unittest.TestCase):
    """Defaults in the UI must match the backend's documented defaults."""

    def test_default_sort_matches_backend_defaults(self):
        source = _read_index()
        self.assertIn("findings: { sort: '%s'" % DEFAULT_FINDING_ORDER, source)
        self.assertIn("invoices: { sort: '%s'" % DEFAULT_INVOICE_ORDER, source)
        self.assertIn("rules: { sort: '%s'" % DEFAULT_SUPPLIER_RULE_ORDER, source)

    def test_sort_whitelists_cover_backend_options(self):
        source = _read_index()
        for key, allowed in (
            ("findings", FINDING_ORDER_BY),
            ("invoices", INVOICE_ORDER_BY),
            ("rules", SUPPLIER_RULE_ORDER_BY),
        ):
            for value in allowed:
                self.assertIn(
                    "'%s'" % value, source, "%s whitelist missing %s" % (key, value)
                )

    def test_controls_are_present_for_every_list(self):
        source = _read_index()
        for prefix in ("findings", "invoices", "rules"):
            for suffix in ("sort", "range", "prev", "next", "error"):
                self.assertIn('id="%s-%s"' % (prefix, suffix), source)

    def test_audit_position_section_is_present(self):
        source = _read_index()
        self.assertIn("Positionen", source)
        self.assertIn('id="audit-items"', source)
        self.assertIn('id="audit-hint"', source)
        self.assertIn("loadAudit()", source)
        self.assertIn("/api/invoices/", source)


class ClientQueryBuildingTests(unittest.TestCase):
    """Exercise the extracted ``buildListQuery``/``rangeLabel`` helpers."""

    @unittest.skipUnless(
        shutil.which("node"), "node is required to exercise the JS helpers"
    )
    def test_defaults_without_interaction(self):
        source = _read_index()
        program = (
            _extract_function(source, "buildListQuery")
            + "\nprocess.stdout.write(JSON.stringify({"
            + "findings: buildListQuery({ sort: 'amount_desc', limit: 25, offset: 0,"
            + " extra: { status: 'open' } }),"
            + "invoices: buildListQuery({ sort: 'created_at_desc', limit: 25, offset: 0 }),"
            + "rules: buildListQuery({ sort: 'supplier_asc', limit: 25, offset: 0 }),"
            + "}));"
        )
        out = _run_js(program)
        self.assertEqual(
            out["findings"], "?status=open&sort=amount_desc&limit=25&offset=0"
        )
        self.assertEqual(out["invoices"], "?sort=created_at_desc&limit=25&offset=0")
        self.assertEqual(out["rules"], "?sort=supplier_asc&limit=25&offset=0")

    @unittest.skipUnless(
        shutil.which("node"), "node is required to exercise the JS helpers"
    )
    def test_sort_and_paging_are_set(self):
        source = _read_index()
        program = (
            _extract_function(source, "buildListQuery")
            + "\nprocess.stdout.write(JSON.stringify(buildListQuery("
            + "{ sort: 'invoice_number_asc', limit: 25, offset: 25 })));"
        )
        out = _run_js(program)
        self.assertEqual(out, "?sort=invoice_number_asc&limit=25&offset=25")

    @unittest.skipUnless(
        shutil.which("node"), "node is required to exercise the JS helpers"
    )
    def test_entity_filter_is_repeated(self):
        source = _read_index()
        program = (
            _extract_function(source, "buildListQuery")
            + "\nprocess.stdout.write(JSON.stringify(buildListQuery("
            + "{ sort: 'amount_desc', limit: 25, offset: 0,"
            + " extra: { status: 'open', entity: ['DE01', 'DE02'] } })));"
        )
        out = _run_js(program)
        self.assertEqual(
            out,
            "?status=open&entity=DE01&entity=DE02&sort=amount_desc&limit=25&offset=0",
        )

    @unittest.skipUnless(
        shutil.which("node"), "node is required to exercise the JS helpers"
    )
    def test_range_label_matches_count_and_total(self):
        source = _read_index()
        program = (
            _extract_function(source, "rangeLabel")
            + "\nprocess.stdout.write(JSON.stringify(["
            + "rangeLabel({ count: 25, total_count: 100, offset: 0 }),"
            + "rangeLabel({ count: 25, total_count: 100, offset: 25 }),"
            + "rangeLabel({ count: 0, total_count: 0, offset: 0 }),"
            + "rangeLabel({ count: 2, total_count: 12, offset: 10 })"
            + "]));"
        )
        out = _run_js(program)
        self.assertEqual(
            out, ["1–25 von 100", "26–50 von 100", "0–0 von 0", "11–12 von 12"]
        )


if __name__ == "__main__":
    unittest.main()

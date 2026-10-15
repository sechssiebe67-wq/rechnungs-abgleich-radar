"""Regression tests for XSS-safe rendering in the dashboard.

The supplier name is user-controlled and ends up both in text nodes and in a
``data-supplier`` HTML attribute. The shared ``esc`` helper must therefore
encode quotes as well, otherwise a name like ``x" onmouseover="alert(1)`` can
break out of the attribute and inject an event handler (stored XSS).
"""

import json
import os
import shutil
import subprocess
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_INDEX = os.path.join(_HERE, "..", "src", "radar", "static", "index.html")

_ALLOWED_ENTITIES = ("&quot;", "&amp;", "&lt;", "&gt;", "&#39;")


def _read_index():
    with open(_INDEX, encoding="utf-8") as handle:
        return handle.read()


def _extract_esc(source):
    start = source.index("function esc(")
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
    raise AssertionError("unbalanced braces in esc()")


def _run_esc(payloads):
    esc_source = _extract_esc(_read_index())
    program = (
        esc_source
        + "\nconst payloads = "
        + json.dumps(payloads)
        + ";\nprocess.stdout.write(JSON.stringify(payloads.map(esc)));\n"
    )
    result = subprocess.run(["node", "-e", program], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


class DashboardEscapingTests(unittest.TestCase):
    def test_data_supplier_attribute_is_escaped(self):
        self.assertIn('data-supplier="${esc(', _read_index())

    @unittest.skipUnless(shutil.which("node"), "node is required to exercise the JS helper")
    def test_esc_encodes_attribute_breaking_characters(self):
        payloads = [
            'x" onmouseover="alert(1)',
            "<script>alert(1)</script>",
            "ACME & Co. 'Elektro' GmbH",
        ]
        outputs = _run_esc(payloads)

        for output in outputs:
            self.assertNotIn('"', output, "raw quote must not survive escaping")
            self.assertNotIn("<", output, "raw '<' must not survive escaping")
            self.assertNotIn(">", output, "raw '>' must not survive escaping")
            remainder = output
            for entity in _ALLOWED_ENTITIES:
                remainder = remainder.replace(entity, "")
            self.assertNotIn("&", remainder, "raw '&' must not survive escaping")

        self.assertEqual(outputs[0], "x&quot; onmouseover=&quot;alert(1)")
        self.assertEqual(outputs[1], "&lt;script&gt;alert(1)&lt;/script&gt;")
        self.assertEqual(outputs[2], "ACME &amp; Co. &#39;Elektro&#39; GmbH")


if __name__ == "__main__":
    unittest.main()

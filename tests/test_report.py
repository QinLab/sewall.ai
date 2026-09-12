"""Report serialization and isolation checks; run with unittest discovery."""

import json
import os
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sewall.report import render_report

NODE_EXECUTABLE = os.environ.get("SEWALL_NODE_BIN") or shutil.which("node")

class DocumentParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.payload = []
        self.in_manifest = False

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        self.tags.append((tag, attributes))
        if tag == "script" and attributes.get("id") == "manifest-data":
            self.in_manifest = True

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_manifest = False

    def handle_data(self, data):
        if self.in_manifest:
            self.payload.append(data)


class ReportTests(unittest.TestCase):
    def parse(self, manifest):
        document = render_report(manifest)
        parser = DocumentParser()
        parser.feed(document)
        return document, parser

    def test_untrusted_records_cannot_end_json_script(self):
        attack = '</script><script src="https://attacker.invalid/x.js"></script><img src=x onerror=alert(1)>'
        manifest = {
            "mode": "synthetic_fixture",
            "question": attack,
            "graph": {
                "nodes": [{"id": attack, "label": attack, "source_id": attack}],
                "edges": [{"source": attack, "target": attack,
                           "relation": "supports", "evidence": {"quote": attack}}],
            },
            "policies": [{"source_id": attack}],
            "claims": [{"statement": attack}],
        }
        document, parsed = self.parse(manifest)
        self.assertEqual(json.loads("".join(parsed.payload)), manifest)
        self.assertNotIn(attack, document)
        self.assertEqual(len([tag for tag, _ in parsed.tags if tag == "script"]), 2)
        self.assertFalse(any(tag == "img" for tag, _ in parsed.tags))
        self.assertNotIn("innerHTML", document)
        self.assertNotIn("insertAdjacentHTML", document)
        self.assertNotIn("document.write", document)

    def test_unicode_delimiters_round_trip(self):
        manifest = {"question": "AlphaEarth & EOL <context> café \u2028 \u2029"}
        document, parsed = self.parse(manifest)
        self.assertEqual(json.loads("".join(parsed.payload)), manifest)
        self.assertNotIn("\u2028", document)
        self.assertNotIn("\u2029", document)
        self.assertIn("\\u003ccontext\\u003e", document)

    def test_no_external_resources_or_execution_requests(self):
        document, parsed = self.parse({})
        for tag, attrs in parsed.tags:
            if tag in {"script", "img", "iframe", "source", "audio", "video"}:
                self.assertNotIn("src", attrs)
            if tag == "link":
                self.fail("Offline report must not load linked resources")
        self.assertIn("connect-src 'none'", document)
        self.assertNotIn("fetch(", document)
        self.assertNotIn("XMLHttpRequest", document)
        self.assertNotIn("WebSocket", document)

    def test_missing_fields_remain_serializable(self):
        document, parsed = self.parse({})
        self.assertTrue(document.startswith("<!doctype html>"))
        self.assertEqual(json.loads("".join(parsed.payload)), {})
        self.assertIn("No scientific claims recorded.", document)
        self.assertIn("Status unavailable", document)

    def test_modes_and_review_states_are_explicit(self):
        document = render_report({"mode": "synthetic_fixture", "status": "abstained"})
        self.assertIn("SYNTHETIC FIXTURE SIMULATION", document)
        self.assertIn("The workflow abstained.", document)
        self.assertIn("The workflow requires review.", document)
        self.assertIn("The workflow failed.", document)
        self.assertIn("Knowledge / context link", document)
        self.assertIn("Execution dependency", document)
        self.assertIn("No edge evidence recorded.", document)

    def test_live_metadata_and_model_text_remain_inert_and_inspectable(self):
        attack = '</script><img src=x onerror=alert(1)>'
        manifest = {
            "mode": "live_public_metadata", "status": "no_evidence",
            "metadata_summary": {"summary": attack, "record_ids": [], "limitations": [attack]},
            "records": [{"id": "ncbi:gds:1", "title": attack, "source_url": "javascript:alert(1)"}],
            "model_calls": [{"role": "planner", "attempt": 1, "trace": {"output": {"reason": attack}}}],
        }
        document, parsed = self.parse(manifest)
        self.assertEqual(json.loads("".join(parsed.payload)), manifest)
        self.assertNotIn(attack, document)
        self.assertFalse(any(tag == "img" for tag, _ in parsed.tags))
        self.assertIn("LIVE PUBLIC METADATA / MODEL-ASSISTED", document)
        self.assertIn('id="model-calls"', document)
        self.assertIn('id="live-records"', document)
        self.assertIn('id="compute-context"', document)
        self.assertIn('link.rel = "noopener noreferrer"', document)
        self.assertIn("No controlled data access or biological discovery is established.", document)

    def test_input_is_not_mutated(self):
        manifest = {"graph": {"nodes": [{"id": "n1", "details": {"x": 1}}]}}
        before = json.dumps(manifest, sort_keys=True)
        render_report(manifest)
        self.assertEqual(json.dumps(manifest, sort_keys=True), before)

    @unittest.skipUnless(NODE_EXECUTABLE, "Node.js is unavailable for JavaScript syntax validation")
    def test_embedded_javascript_syntax(self):
        document = render_report({"mode": "live_public_metadata"})
        javascript = document.rsplit("<script>", 1)[1].split("</script>", 1)[0]
        result = subprocess.run([NODE_EXECUTABLE, "--check"], input=javascript,
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_non_dictionary_and_non_json_numbers(self):
        with self.assertRaises(TypeError):
            render_report([])
        with self.assertRaises(ValueError):
            render_report({"metrics": {"invalid": float("nan")}})


if __name__ == "__main__":
    unittest.main()

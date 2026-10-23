"""Record reproducible checks for the dated catalog graph; no source/model requests.

Run from prototype/: python scripts/check_knowledge_map.py --node node
Requires the optional validation extra (jsonschema) and Node.js for script checks.
"""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
from importlib.metadata import version
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jsonschema import Draft202012Validator
from sewall.knowledge_map import validate_graph


def h(p):
    return sha256(p.read_bytes()).hexdigest()


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--node", default="node")
parser.add_argument("--wheel", type=Path)
args = parser.parse_args()
registry_path = ROOT / "docs/knowledge-map-source-registry-2026-10-23.json"
graph_path = ROOT / "knowledge-map/graph.json"
schema_path = ROOT / "schemas/knowledge_map.schema.json"
graph = json.loads(graph_path.read_text())
validate_graph(graph, json.loads(registry_path.read_text()))
schema = json.loads(schema_path.read_text())
Draft202012Validator.check_schema(schema)
Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(graph)
log = io.StringIO()
suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_knowledge_map.py")
tests = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
(ROOT / "knowledge-map/unit-tests.txt").write_text(log.getvalue())
assert tests.wasSuccessful(), log.getvalue()
viewer = subprocess.run([args.node, str(ROOT / "tests/knowledge_map_viewer.cjs"), str(ROOT / "knowledge-map/index.html")], check=True, capture_output=True, text=True)
viewer_result = json.loads(viewer.stdout)
assert viewer_result["passed"]
wheel = None
if args.wheel:
    with ZipFile(args.wheel) as z:
        for name in ("knowledge_map.py", "knowledge_map_viewer.html"):
            assert z.read("sewall/" + name) == (ROOT / "sewall" / name).read_bytes(), name
    wheel = {"filename": args.wheel.name, "sha256": h(args.wheel), "module_and_viewer_match_source": True}
checks = {"checked_at": datetime.now(timezone.utc).isoformat(), "graph_sha256": graph["graph_sha256"],
          "deterministic_replay": True, "json_schema_validation": {"passed": True, "validator": "jsonschema", "version": version("jsonschema"), "schema_sha256": h(schema_path)},
          "python_tests": {"passed": True, "run": tests.testsRun, "failures": len(tests.failures), "errors": len(tests.errors), "log": "unit-tests.txt"},
          "viewer_script_checks": viewer_result, "wheel": wheel,
          "browser_visual_check": {"completed": False, "reason": "Browser-control inventory empty; native Chrome pipe failed to initialize"},
          "files_sha256": {str(p.relative_to(ROOT)): h(p) for p in [ROOT / "sewall/knowledge_map.py", ROOT / "sewall/knowledge_map_viewer.html", ROOT / "tests/test_knowledge_map.py", ROOT / "tests/knowledge_map_viewer.cjs", ROOT / "knowledge-map/index.html", graph_path]},
          "scientific_validation_performed": False, "authorization_grants_issued": 0}
(ROOT / "knowledge-map/checks.json").write_text(json.dumps(checks, indent=2) + "\n")
print(json.dumps({"python_tests": checks["python_tests"], "viewer_script_checks": viewer_result, "schema_passed": True, "wheel_checked": wheel is not None}, indent=2))

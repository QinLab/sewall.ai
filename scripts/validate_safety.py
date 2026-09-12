"""Run offline regression tests and retain the synthetic safe-stop demonstrations.

Use validate_safety.sbatch on an allocated Slurm CPU node. No cloud-model or
scientific-source calls are made by this validation driver.
"""
import json
import hashlib
from pathlib import Path
import socket
import sys
import unittest
from datetime import datetime, timezone
from html import escape

PROTOTYPE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROTOTYPE))


def main():
    from sewall.agent import require_cpu_allocation
    from sewall.safety import SCENARIOS, run_safety_demo, render_safety_report, verify_safety_manifest
    runtime = require_cpu_allocation()
    output = PROTOTYPE / "output/safety" / ("sewall-safe-stop-" + runtime["job_id"])
    output.mkdir(parents=True, exist_ok=False)
    suite = unittest.defaultTestLoader.discover(str(PROTOTYPE / "tests"))
    with (output / "tests.log").open("x", encoding="utf-8") as log:
        result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    receipt = {
        "kind": "synthetic_safety_validation", "created_at": datetime.now(timezone.utc).isoformat(),
        "runtime": {**runtime, "hostname": socket.gethostname()},
        "tests_run": result.testsRun, "failures": len(result.failures),
        "errors": len(result.errors), "skipped": len(result.skipped),
        "tests_passed": result.wasSuccessful(), "scenarios": [],
        "validation_complete": result.wasSuccessful() and not result.skipped,
        "source_sha256": {str(path.relative_to(PROTOTYPE)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (PROTOTYPE / "sewall/safety.py", PROTOTYPE / "sewall/__main__.py",
                                       PROTOTYPE / "tests/test_safety.py", PROTOTYPE / "tests/test_safety_cli.py",
                                       Path(__file__).resolve())},
        "model_calls": 0, "source_requests": 0,
        "scope": "Synthetic supervisor behavior, not universal integrity detection or a formal fail-safe guarantee.",
    }
    if result.wasSuccessful():
        for scenario in SCENARIOS:
            manifest = run_safety_demo(scenario)
            verification = verify_safety_manifest(manifest)
            if not verification["valid"]:
                raise AssertionError("Invalid generated safety snapshot: " + scenario)
            expected = "completed" if scenario == "clean" else "safe_stopped"
            if manifest["status"] != expected or manifest["claims"]:
                raise AssertionError("Unexpected safety outcome: " + scenario)
            folder = output / scenario
            folder.mkdir()
            (folder / "manifest.json").write_text(json.dumps(manifest, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            (folder / "report.html").write_text(render_safety_report(manifest), encoding="utf-8")
            receipt["scenarios"].append({"scenario": scenario, "status": manifest["status"],
                                          "snapshot_valid": True, "claims": len(manifest["claims"])})
    (output / "validation-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    rows = "".join('<li><a href="' + escape(item["scenario"], quote=True) + '/report.html">'
                   + escape(item["scenario"].replace("_", " ")) + "</a>: " + escape(item["status"])
                   + "</li>" for item in receipt["scenarios"])
    (output / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><title>Sewall.ai safe-stop demonstrations</title>'
        '<style>body{font:18px/1.6 system-ui;max-width:900px;margin:3rem auto;padding:1rem;color:#16313d}'
        'a{color:#087f8c}strong{color:#9a5700}</style><h1>Sewall.ai: research-integrity safe stop</h1>'
        '<p><strong>Synthetic executable demonstrations. No biological result or formal fail-safe guarantee.</strong></p>'
        '<p>Critical faults stop new dispatch, quarantine affected outputs, withhold conclusions and preserve an inspectable trace.</p>'
        '<ul>' + rows + '</ul><p><a href="validation-receipt.json">Validation receipt</a> | '
        '<a href="tests.log">Regression test log</a></p></html>', encoding="utf-8")
    print(json.dumps({"output": str(output), **receipt}, indent=2))
    return 0 if result.wasSuccessful() and not result.skipped else 1


if __name__ == "__main__":
    raise SystemExit(main())

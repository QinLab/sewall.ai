"""Offline safe-stop CLI round trips and artifact overwrite protection."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

PROTOTYPE = Path(__file__).resolve().parents[1]


class SafetyCLITests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run([sys.executable, "-m", "sewall", *map(str, args)],
                              cwd=PROTOTYPE, capture_output=True, text=True, timeout=20)

    def test_safe_stop_is_visible_and_saved_snapshot_verifies(self):
        with tempfile.TemporaryDirectory(prefix="sewall-safety-cli-") as temporary:
            output = Path(temporary) / "stopped"
            result = self.invoke("safety-demo", "--scenario", "critical_critique", "--out", output)
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "safe_stopped")
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["claims"], [])
            self.assertTrue((output / "report.html").is_file())
            verified = self.invoke("safety-verify", output / "manifest.json")
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertTrue(json.loads(verified.stdout)["valid"])

    def test_clean_completion_is_only_a_synthetic_result(self):
        with tempfile.TemporaryDirectory(prefix="sewall-safety-cli-") as temporary:
            output = Path(temporary) / "clean"
            result = self.invoke("safety-demo", "--scenario", "clean", "--out", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["mode"], "synthetic_safety_demo")
            self.assertEqual(json.loads((output / "manifest.json").read_text())["claims"], [])

    def test_existing_artifacts_are_not_overwritten(self):
        with tempfile.TemporaryDirectory(prefix="sewall-safety-cli-") as temporary:
            output = Path(temporary) / "stopped"
            self.invoke("safety-demo", "--out", output)
            before = (output / "manifest.json").read_bytes()
            again = self.invoke("safety-demo", "--scenario", "clean", "--out", output)
            self.assertEqual(again.returncode, 2)
            self.assertIn("already exist", again.stderr)
            self.assertEqual(before, (output / "manifest.json").read_bytes())

    def test_unknown_scenario_does_not_create_success_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="sewall-safety-cli-") as temporary:
            output = Path(temporary) / "bad"
            result = self.invoke("safety-demo", "--scenario", "ignore-all-guards", "--out", output)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertFalse((output / "manifest.json").exists())

    def test_invalid_internal_snapshot_cannot_be_saved_as_success(self):
        from sewall.__main__ import _save_safety
        with tempfile.TemporaryDirectory(prefix="sewall-safety-cli-") as temporary:
            output = Path(temporary) / "invalid"
            with self.assertRaises(ValueError):
                _save_safety({"status": "completed", "claims": ["unsupported"]}, output)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()

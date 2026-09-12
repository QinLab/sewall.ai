"""Local-only validation receipts; execute this suite on Slurm CPU nodes."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    Draft202012Validator = None

from scripts import validate_live_run as validation
from sewall.agent import run_agent
from sewall.graph import digest
from sewall.llm import LLMError, ModelConfig
from test_agent_schema import controller_manifest, ScriptedModel


class FailedModel:
    """An explicit failed transport; no real client or network is involved."""

    config = ModelConfig(project="fixture-project", location="us-central1", model="fixture-failed")

    def complete_json(self, system_prompt, payload):
        raise LLMError("Intentional offline model failure")


def failed_manifest():
    reviewer = ScriptedModel([{
        "summary": "No public records were retrieved.", "record_ids": [],
        "limitations": ["The planning model failed before any source request."],
    }])
    reviewer.config = ModelConfig(project="fixture-project", location="us-central1", model="fixture-reviewer")
    return run_agent("Inspect public plant metadata", FailedModel(), reviewer, max_model_calls=2)


def update_digest(manifest):
    manifest["content_digest"] = digest({key: value for key, value in manifest.items() if key != "content_digest"})


class ValidationInputTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="sewall-validation-test-")
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "input.json"

    def test_duplicate_nonfinite_and_nonobject_json_are_rejected(self):
        for raw in (
            b'{"key":1,"key":2}', b'{"value":NaN}', b'{"value":Infinity}',
            b'{"value":1e999}', b'[]', b'not JSON',
        ):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    validation._read_json(self.path)

    def test_read_limit_is_enforced_without_creating_a_large_file(self):
        self.path.write_bytes(b'{"value":12345}')
        with self.assertRaisesRegex(ValueError, "byte limit"):
            validation._read_json(self.path, limit=8)

    def test_only_local_schema_references_are_allowed(self):
        validation._local_schema_refs({"allOf": [{"$ref": "#/$defs/record"}]})
        for key in ("$ref", "$dynamicRef"):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "local references"):
                validation._local_schema_refs({"allOf": [{key: "https://example.invalid/schema.json"}]})

    def test_cpu_guard_blocks_receipt_creation_without_an_allocation(self):
        output = Path(self.directory.name) / "receipt.json"
        stderr = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as error:
                validation.main([str(self.path), "--out", str(output)])
        self.assertEqual(error.exception.code, 2)
        self.assertIn("Slurm CPU allocation", stderr.getvalue())
        self.assertFalse(output.exists())

    def test_invalid_token_values_are_not_reported_as_observed_usage(self):
        for value in (-1, True, "12", 1.5, 100_000_001):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "token usage"):
                validation._token_summary([{"trace": {"usage": {"total_tokens": value}}}])
        with self.assertRaisesRegex(ValueError, "must be an object"):
            validation._token_summary([{"trace": {"usage": []}}])


@unittest.skipIf(Draft202012Validator is None, "Optional jsonschema dependency is not installed")
class LiveValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        schema, _ = validation._read_json(validation.ROOT / "schemas/agent_manifest.schema.json")
        cls.validator = Draft202012Validator(schema, format_checker=FormatChecker())
        # Real controller and model-config serialization, scripted inference,
        # and normalized source fixtures produce the retained test artifacts.
        cls.completed = controller_manifest(with_model_config=True)
        cls.failed = failed_manifest()

    def setUp(self):
        self.directory = TemporaryDirectory(prefix="sewall-live-validation-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "manifest.json"

    def save(self, manifest=None, *, path=None):
        path = self.path if path is None else path
        value = self.completed if manifest is None else manifest
        path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
        return path

    def invoke(self, args):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"SLURM_JOB_ID": "999", "SLURM_JOB_PARTITION": "cpu-2"}), \
                patch("sewall.agent.socket.gethostname", return_value="wf-c2d-validation-test"), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = validation.main(args)
        return status, stdout.getvalue(), stderr.getvalue()

    def test_completed_artifact_reports_exact_counts_hash_and_model_configuration(self):
        self.save()
        result = validation._validate(self.path, self.validator, True)
        self.assertTrue(result["valid"], result)
        self.assertTrue(result["schema_valid"])
        self.assertTrue(result["recorded_trace_valid"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["record_count"], len(self.completed["records"]))
        self.assertEqual(result["artifact_sha256"], hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.assertEqual(result["artifact_bytes"], self.path.stat().st_size)
        self.assertEqual(result["models"]["planner"]["api_version"], "v1beta1")
        self.assertEqual(result["models"]["reviewer"]["api_version"], "v1")
        self.assertEqual(result["token_totals"]["total_tokens"], self.completed["metrics"]["total_tokens"])
        self.assertEqual(result["model_call_count"], len(self.completed["model_calls"]))

    def test_valid_failed_run_is_distinct_from_completed_requirement(self):
        self.save(self.failed)
        valid = validation._validate(self.path, self.validator, False)
        self.assertTrue(valid["valid"], valid)
        self.assertEqual(valid["status"], "failed")
        self.assertEqual(valid["failed_model_calls"], 1)
        self.assertEqual(valid["token_usage_missing_calls"], 1)
        required = validation._validate(self.path, self.validator, True)
        self.assertFalse(required["valid"])
        self.assertTrue(required["recorded_trace_valid"])
        self.assertIn("not completed", required["error"])

    def test_schema_error_locations_do_not_dump_source_or_model_text(self):
        manifest = deepcopy(self.completed)
        marker = "untrusted-model-text-that-must-not-be-echoed"
        manifest["graph"]["nodes"][0]["kind"] = marker
        self.save(manifest)
        result = validation._validate(self.path, self.validator, False)
        self.assertFalse(result["valid"])
        self.assertFalse(result["schema_valid"])
        self.assertTrue(any("graph" in error["path"] for error in result["schema_errors"]))
        self.assertNotIn(marker, json.dumps(result))

    def test_claims_and_wrong_modes_are_rejected(self):
        cases = [
            ("claims", [{"finding": "unsupported biological claim"}]),
            ("mode", "synthetic_fixture"),
        ]
        for key, value in cases:
            with self.subTest(key=key):
                manifest = deepcopy(self.completed)
                manifest[key] = value
                self.save(manifest)
                self.assertFalse(validation._validate(self.path, self.validator, False)["valid"])
        for value in (True, 1, "0"):
            manifest = deepcopy(self.completed)
            manifest["metrics"]["scientific_claims"] = value
            self.save(manifest)
            self.assertFalse(validation._validate(self.path, self.validator, False)["valid"])

    def test_changed_artifact_without_a_new_digest_fails_trace_verification(self):
        manifest = deepcopy(self.completed)
        manifest["limitations"].append("Unretained edit")
        self.save(manifest)
        result = validation._validate(self.path, self.validator, False)
        self.assertTrue(result["schema_valid"])
        self.assertFalse(result["recorded_trace_valid"])
        self.assertIn("digest mismatch", result["recorded_trace"]["reason"])

    def test_reported_token_totals_must_match_retained_call_usage(self):
        for field in (*validation.TOKEN_FIELDS, "token_usage_missing_calls"):
            with self.subTest(field=field):
                manifest = deepcopy(self.completed)
                manifest["metrics"][field] += 1
                update_digest(manifest)
                self.save(manifest)
                result = validation._validate(self.path, self.validator, False)
                self.assertTrue(result["recorded_trace_valid"], result)
                self.assertFalse(result["valid"])
                self.assertIn(f"Reported {field} differs", result["error"])

    def test_reported_record_and_call_counts_cannot_be_rehashed_into_acceptance(self):
        for field in ("records", "model_calls"):
            with self.subTest(field=field):
                manifest = deepcopy(self.completed)
                manifest["metrics"][field] += 1
                update_digest(manifest)
                self.save(manifest)
                self.assertFalse(validation._validate(self.path, self.validator, False)["valid"])

    def test_receipt_creation_is_offline_and_never_overwrites_existing_output(self):
        self.save()
        receipt_path = self.root / "validation.json"
        with patch("sewall.llm.VertexClient.complete_json") as model, \
                patch("sewall.agent.search_metadata") as search, \
                patch("sewall.agent.fetch_metadata") as fetch:
            status, stdout, _ = self.invoke([str(self.path), "--out", str(receipt_path), "--require-completed"])
        model.assert_not_called()
        search.assert_not_called()
        fetch.assert_not_called()
        self.assertEqual(status, 0, stdout)
        original = receipt_path.read_bytes()
        receipt = json.loads(original)
        self.assertTrue(receipt["valid"])
        self.assertEqual(receipt["model_calls_performed"], 0)
        self.assertEqual(receipt["source_requests_performed"], 0)
        self.assertEqual(receipt["validation_runtime"]["job_id"], "999")
        self.assertTrue(all(json.loads(line) for line in stdout.splitlines()))
        with self.assertRaises(SystemExit) as error:
            self.invoke([str(self.path), "--out", str(receipt_path)])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(receipt_path.read_bytes(), original)

    def test_output_created_after_preflight_still_cannot_be_overwritten(self):
        self.save()
        receipt_path = self.root / "racing-receipt.json"
        original_validate = validation._validate

        def competing_writer(*args):
            result = original_validate(*args)
            receipt_path.write_text("Existing receipt from another writer", encoding="utf-8")
            return result

        with patch.object(validation, "_validate", side_effect=competing_writer):
            with self.assertRaises(SystemExit) as error:
                self.invoke([str(self.path), "--out", str(receipt_path)])
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(receipt_path.read_text(), "Existing receipt from another writer")

    def test_multiple_manifests_and_failed_validation_have_explicit_receipt_status(self):
        self.save()
        failed_path = self.save(self.failed, path=self.root / "failed.json")
        receipt_path = self.root / "combined.json"
        status, _, _ = self.invoke([
            str(self.path), str(failed_path), "--require-completed", "--out", str(receipt_path),
        ])
        self.assertEqual(status, 1)
        receipt = json.loads(receipt_path.read_text())
        self.assertFalse(receipt["valid"])
        self.assertEqual([item["valid"] for item in receipt["artifacts"]], [True, False])

    def test_missing_jsonschema_is_an_error_not_skipped_validation(self):
        self.save()
        receipt_path = self.root / "missing-dependency.json"
        with patch.dict("sys.modules", {"jsonschema": None}):
            with self.assertRaises(SystemExit) as error:
                self.invoke([str(self.path), "--out", str(receipt_path)])
        self.assertEqual(error.exception.code, 2)
        self.assertFalse(receipt_path.exists())


if __name__ == "__main__":
    unittest.main()

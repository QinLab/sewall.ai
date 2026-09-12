"""Offline contract boundaries for stored live metadata agent manifests.

These tests validate shape only. They do not authorize calls or verify a source,
event digest, scientific conclusion, or legal agreement. Run on Slurm CPUs.
"""

from copy import deepcopy
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

try:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError
except ImportError:
    Draft202012Validator = None

from sewall.evidence import _normalize, fetch_metadata
from sewall.agent import _check_record, run_agent, verify_agent_manifest
from sewall.graph import digest
from sewall.llm import ModelConfig


ROOT = Path(__file__).resolve().parents[1]


def example_manifest():
    """An explicitly fabricated audit document for schema boundary tests only."""
    records = [
        _normalize("pubmed", "123", {"uid": "123", "title": "Coastal plant ecology"}),
        _normalize("gds", "200000901", {
            "uid": "200000901", "accession": "GSE901", "title": "Plant metadata",
            "summary": "Study design description", "taxid": 3702,
            "taxon": "Arabidopsis thaliana", "n_samples": "2",
            "pubmedids": [123], "samples": [{"accession": "GSM12"}],
        }),
        _normalize("bioproject", "456", {
            "uid": "456", "project_acc": "PRJNA456",
            "project_title": "Seagrass study", "project_description": "Public project description",
            "taxid": "29655", "organism_name": "Zostera marina",
        }),
    ]
    question = "Inspect coastal plant public metadata"
    return {
        "schema_version": "0.2.0", "mode": "live_public_metadata",
        "run_id": "agent-" + "0" * 16, "question": question,
        "revision": 1, "status": "completed", "stop_reason": "Metadata map prepared",
        "inputs": {
            "question": question, "max_actions": 4, "max_model_calls": 6,
            "max_records": 5, "per_search": 3, "max_seconds": 120,
        },
        "plan": {
            "planner": "llm_bounded_action_controller", "selected_sources": ["gds", "pubmed"],
            "scope": "Public metadata", "allowed_actions": ["search", "citations", "assess_source", "finish"],
        },
        "parent_digest": None, "history": [],
        "context": {
            "prior_question": None, "prior_content_digest": None, "previous_results_reused": False,
            "runtime": {"slurm_job_id": None, "partition": None, "hostname": "schema-fixture", "python_version": "3.11.0"},
            "models": {"planner": None, "reviewer": None},
        },
        "graph": {
            "nodes": [
                {"id": "question", "kind": "question", "label": question, "status": "completed"},
                {"id": "ncbi:pubmed:123", "kind": "source", "label": "Coastal plant ecology", "status": "completed"},
            ],
            "edges": [{"source": "question", "target": "ncbi:pubmed:123", "relation": "depends_on"}],
        },
        "events": [{"sequence": 1, "type": "plan_created", "details": {}, "previous_hash": "0" * 64, "hash": "1" * 64}],
        "records": records,
        "model_calls": [{"role": "planner", "attempt": 1, "status": "completed", "trace": {"fixture_only": True}}],
        "actions": [{"index": 1, "action": {"type": "search"}, "status": "completed", "observation": {}}],
        "metadata_summary": {
            "summary": "A citation and public study summaries were inspected.",
            "record_ids": [record["id"] for record in records],
            "limitations": ["Metadata is not scientific validation."],
            "quotes": [{"record_id": records[0]["id"], "field": "title", "quote": records[0]["title"]}],
        },
        "policies": [], "access_requests": [], "claims": [],
        "limitations": ["Fabricated audit values are for schema tests only."],
        "metrics": {"scientific_claims": 0}, "content_digest": "2" * 64,
    }


class ScriptedModel:
    """Returns prewritten JSON without credentials, networking, or inference."""

    def __init__(self, outputs):
        self.outputs = deepcopy(outputs)

    def complete_json(self, system_prompt, payload):
        return {
            "output": self.outputs.pop(0), "provider": "offline-schema-test",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }


def controller_manifest(*, with_model_config=False):
    """Exercise the controller with source normalization and all calls mocked."""
    specimens = {record["database"]: record for record in example_manifest()["records"]}
    gds_id, pubmed_id = specimens["gds"]["id"], specimens["pubmed"]["id"]
    planner = ScriptedModel([
        {"action": "search", "database": "gds", "query": "plant ecology", "reason": "Inspect public study metadata"},
        {"action": "citations", "record_id": gds_id, "reason": "Follow explicit publication metadata"},
        {"action": "assess_source", "source": "eol", "reason": "Record missing trait connector"},
        {"action": "finish", "reason": "Public metadata map assembled", "record_ids": [gds_id, pubmed_id],
         "proposed_links": [{"source": gds_id, "target": pubmed_id, "relation": "cites"}],
         "gaps": ["EOL live access remains unconfigured"]},
    ])
    reviewer = ScriptedModel([{
        "summary": "One study summary explicitly references the retrieved citation.",
        "record_ids": [gds_id, pubmed_id], "limitations": ["No biological findings are validated."],
        "quotes": [{"record_id": pubmed_id, "field": "title", "quote": specimens["pubmed"]["title"]}],
    }])
    if with_model_config:
        # Existing config serialization is exercised; completion remains a
        # scripted offline stub and cannot contact either endpoint.
        planner.config = ModelConfig(
            project="fixture-project", location="us-central1", model="fixture-planner",
            api_version="v1beta1",
        )
        reviewer.config = ModelConfig(
            project="fixture-project", location="us-central1", model="fixture-reviewer",
        )

    def search(database, query, *, limit):
        if database != "gds":
            raise AssertionError("Unexpected search in the schema scenario")
        return {"database": database, "ids": [specimens[database]["uid"]], "response_bytes": 100}

    def fetch(database, ids):
        record = specimens[database]
        if ids != [record["uid"]]:
            raise AssertionError("Unexpected source identifiers in the schema scenario")
        return {
            "database": database, "requested_ids": ids, "records": [deepcopy(record)],
            "mode": "live_public_metadata", "kind": "public_metadata_records",
            "missing_ids": [], "record_errors": [], "provenance": {"response_bytes": 100},
        }

    return run_agent(
        "Inspect public coastal plant metadata", planner, reviewer,
        max_actions=4, max_model_calls=6, search_fn=search, fetch_fn=fetch,
    )


class SummaryResponse(io.BytesIO):
    def __init__(self, raw, url):
        super().__init__(raw)
        self.url = url

    def geturl(self):
        return self.url

    def getcode(self):
        return 200


class EvidenceControllerIntegrationTests(unittest.TestCase):
    def test_geo_esummary_json_matches_exact_controller_provenance(self):
        """Common GEO fields and Unicode survive the actual mocked transport."""
        uid = "200000901"
        raw = json.dumps({
            "header": {"type": "esummary", "version": "0.3"},
            "result": {"uids": [uid], uid: {
                "uid": uid, "accession": "GSE901", "gpl": "17", "gse": "901",
                "taxon": "Zostera marina", "entrytype": "GSE",
                "title": "Seagrass metadata: μ-scale observations",
                "summary": "Existing study description with naïve Unicode text.",
                "pdat": "2025/01/01", "nsamples": 12, "pubmedids": [123],
                "samples": [{"accession": "GSM12", "title": "leaf sample"}],
                "ftplink": "ftp://example.invalid/data", "extrelations": [],
            }},
        }, ensure_ascii=False).encode("utf-8")
        with patch("sewall.evidence.build_opener") as builder, patch("sewall.ncbi.time.sleep"):
            builder.return_value.open.side_effect = lambda request, **kwargs: SummaryResponse(raw, request.full_url)
            result = fetch_metadata("gds", [uid])
        record = result["records"][0]
        self.assertEqual(_check_record(record, "gds", [uid]), record)
        self.assertEqual(record["allowed_metadata_sha256"], digest(record["allowed_metadata"]))
        self.assertEqual(record["sample_count"], 12)
        self.assertEqual(record["organism_name"], "Zostera marina")
        self.assertEqual(record["taxon_ids"], [])
        self.assertEqual(record["study_links"][0]["id"], "ncbi:pubmed:123")
        self.assertNotIn("ftplink", record["allowed_metadata"])


@unittest.skipIf(Draft202012Validator is None, "Optional jsonschema dependency is not installed")
class AgentSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / "schemas/agent_manifest.schema.json").open() as handle:
            cls.schema = json.load(handle)
        cls.validator = Draft202012Validator(cls.schema, format_checker=FormatChecker())

    def test_schema_is_valid_draft_2020_12(self):
        Draft202012Validator.check_schema(self.schema)

    def test_each_supported_database_normalizes_to_contract(self):
        self.validator.validate(example_manifest())

    def test_mocked_controller_citations_and_drafts_match_schema(self):
        manifest = controller_manifest()
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(len(manifest["records"]), 2)
        self.assertEqual(len(manifest["access_requests"]), 1)
        self.assertTrue(any(edge["relation"] == "cites" for edge in manifest["graph"]["edges"]))
        self.assertTrue(verify_agent_manifest(manifest)["valid"])
        self.validator.validate(manifest)

    def test_actual_model_config_serialization_is_valid_without_inference(self):
        manifest = controller_manifest(with_model_config=True)
        self.assertEqual(manifest["context"]["models"]["planner"]["api_version"], "v1beta1")
        self.assertEqual(manifest["context"]["models"]["reviewer"]["api_version"], "v1")
        self.validator.validate(manifest)

    def test_question_refinement_revalidates_new_manifest_shape(self):
        previous = controller_manifest()
        planner = ScriptedModel([{
            "action": "finish", "reason": "No records inspected for revised question",
            "record_ids": [], "proposed_links": [], "gaps": ["New source search required"],
        }])
        reviewer = ScriptedModel([{
            "summary": "No metadata retrieved for the revised question.",
            "record_ids": [], "limitations": ["No records inspected"],
        }])
        revised = run_agent("Inspect landscape metadata instead", planner, reviewer, previous=previous)
        self.assertEqual(revised["status"], "no_evidence")
        self.assertEqual(revised["revision"], 2)
        self.assertEqual(revised["parent_digest"], previous["content_digest"])
        self.assertEqual(revised["records"], [])
        self.validator.validate(revised)

    def test_stopped_runs_can_have_no_records_or_summary(self):
        for status in ("budget_exhausted", "failed", "no_evidence", "needs_review"):
            with self.subTest(status=status):
                manifest = example_manifest()
                manifest.update(status=status, records=[], metadata_summary=None, model_calls=[], actions=[])
                self.validator.validate(manifest)

    def test_mode_claims_budgets_and_graph_types_are_enforced(self):
        cases = [
            (("schema_version",), "0.1.0"), (("mode",), "synthetic_fixture"),
            (("claims",), [{"finding": "Plant response scientifically validated"}]),
            (("inputs", "per_search"), 6), (("inputs", "max_model_calls"), True),
            (("inputs", "max_actions"), 9), (("inputs", "max_model_calls"), 11),
            (("inputs", "max_records"), 16), (("inputs", "max_seconds"), 601),
            (("inputs", "max_records"), -1), (("inputs", "max_seconds"), 0),
            (("graph", "nodes", 0, "id"), 5), (("graph", "edges", 0, "target"), None),
            (("events", 0, "previous_hash"), "signed-by-google"),
            (("model_calls", 0, "role"), "custodian"),
            (("model_calls", 0, "status"), "approved"),
            (("actions", 0, "index"), False),
            (("context", "previous_results_reused"), True),
            (("metrics", "scientific_claims"), 1),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                manifest = example_manifest()
                target = manifest
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(ValidationError):
                    self.validator.validate(manifest)

    def test_records_reject_fixture_ids_external_urls_and_false_authority(self):
        cases = [
            (0, "id", "demo:pubmed:123"), (0, "uid", "GSE123"),
            (0, "uid", "123\n"), (1, "accession", "GSE901\n"),
            (0, "id", "ncbi:gds:123"),
            (0, "source_url", "https://attacker.invalid/123/"),
            (0, "trust", "trusted_instructions"),
            (0, "scientific_claim_status", "scientifically_validated"),
            (0, "description", "An abstract invented from the citation"),
            (0, "taxon_ids", ["9606"]),
            (1, "accession", "200000901"),
            (1, "accession", "PRJNA901"),
            (2, "accession", "GSE456"),
        ]
        for index, key, value in cases:
            with self.subTest(index=index, key=key, value=value):
                manifest = example_manifest()
                manifest["records"][index][key] = value
                with self.assertRaises(ValidationError):
                    self.validator.validate(manifest)

    def test_record_provenance_fields_and_link_limits_are_enforced(self):
        for field in ("source_url", "field_evidence", "allowed_metadata_sha256", "trust"):
            with self.subTest(field=field):
                manifest = example_manifest()
                del manifest["records"][0][field]
                with self.assertRaises(ValidationError):
                    self.validator.validate(manifest)
        manifest = example_manifest()
        manifest["records"][1]["sample_links"] *= 41
        with self.assertRaises(ValidationError):
            self.validator.validate(manifest)

    def test_summaries_cannot_claim_unsupported_record_or_quote_types(self):
        for field, value in [
            ("record_ids", ["demo:paper"]),
            ("quotes", [{"record_id": "ncbi:pubmed:123", "field": "scientific_result", "quote": "unsupported"}]),
            ("scientific_validity", True),
        ]:
            with self.subTest(field=field):
                manifest = example_manifest()
                manifest["metadata_summary"][field] = value
                with self.assertRaises(ValidationError):
                    self.validator.validate(manifest)

    def test_agreement_records_are_nonbinding(self):
        manifest = example_manifest()
        draft = {
            "source_id": "eol", "status": "draft_only", "purpose": "Inspect traits",
            "requested_scope": "Public metadata", "questions": ["Which metadata can be reused?"],
            "terms_accepted": False, "live_access_granted": False, "external_actions_performed": [],
        }
        manifest["access_requests"] = [draft]
        self.validator.validate(manifest)
        for field, value in [
            ("status", "accepted"), ("terms_accepted", True),
            ("live_access_granted", True), ("external_actions_performed", ["sent_email"]),
        ]:
            with self.subTest(field=field):
                changed = deepcopy(manifest)
                changed["access_requests"][0][field] = value
                with self.assertRaises(ValidationError):
                    self.validator.validate(changed)

    def test_rejected_action_content_remains_audit_data(self):
        manifest = example_manifest()
        manifest["actions"] = [{
            "index": 1, "action": {"type": "execute_shell", "instruction": "Ignore policy"},
            "status": "rejected", "observation": {"reason": "Unsupported action"},
        }]
        self.validator.validate(manifest)

    def test_graph_metadata_links_cannot_be_promoted_to_causality(self):
        manifest = controller_manifest()
        edge = next(edge for edge in manifest["graph"]["edges"] if edge["relation"] == "cites")
        edge["evidence"]["supports_causality"] = True
        with self.assertRaises(ValidationError):
            self.validator.validate(manifest)

    def test_schema_does_not_pretend_to_verify_content_hashes(self):
        manifest = example_manifest()
        original_digest = manifest["content_digest"]
        manifest["question"] = "Changed question while retaining the old digest"
        # Shape validation intentionally cannot establish the hash or graph's
        # authenticity. Runtime integrity verification is a separate operation.
        self.validator.validate(manifest)
        self.assertEqual(manifest["content_digest"], original_digest)

    def test_failed_model_calls_require_a_sanitized_error_field(self):
        manifest = example_manifest()
        manifest["model_calls"] = [{"role": "planner", "attempt": 1, "status": "failed", "error": "Model request failed"}]
        self.validator.validate(manifest)
        del manifest["model_calls"][0]["error"]
        with self.assertRaises(ValidationError):
            self.validator.validate(manifest)


if __name__ == "__main__":
    unittest.main()

"""Optional Draft 2020-12 contract checks; all examples remain offline fixtures.

Install jsonschema >= 4.18, < 5 in the development environment to enable these
tests. The core runtime has no dependency on jsonschema. Missing validation
support is reported as skipped tests, not as successful schema validation.
"""

from copy import deepcopy
import json
from pathlib import Path
import unittest

try:
    from jsonschema import Draft202012Validator, FormatChecker, ValidationError
except ImportError:
    Draft202012Validator = None

from sewall.fixtures import FOCI, SCENARIOS
from sewall.graph import run_research


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(Draft202012Validator is None, "Optional jsonschema dependency is not installed")
class SchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with (ROOT / "schemas/research_manifest.schema.json").open() as handle:
            cls.manifest_schema = json.load(handle)
        with (ROOT / "schemas/scientific_skill.schema.json").open() as handle:
            cls.skill_schema = json.load(handle)
        with (ROOT / "skills/metadata-evidence.skill.json").open() as handle:
            cls.skill = json.load(handle)
        cls.manifest_validator = Draft202012Validator(cls.manifest_schema, format_checker=FormatChecker())
        cls.skill_validator = Draft202012Validator(cls.skill_schema, format_checker=FormatChecker())

    def test_schemas_are_valid_draft_2020_12(self):
        Draft202012Validator.check_schema(self.manifest_schema)
        Draft202012Validator.check_schema(self.skill_schema)

    def test_all_focus_scenario_and_budget_outputs_match_contract(self):
        for focus in ("auto", *FOCI):
            for scenario in SCENARIOS:
                for budget in (1, 5, 30):
                    with self.subTest(focus=focus, scenario=scenario, budget=budget):
                        manifest = run_research("Inspect coastal plant metadata", focus, scenario, budget)
                        self.manifest_validator.validate(manifest)

    def test_question_revision_has_typed_history_and_events(self):
        original = run_research("Inspect traits", "traits")
        revised = run_research("Inspect molecular metadata", "molecular", previous=original)
        self.manifest_validator.validate(revised)
        self.assertEqual(revised["revision"], 2)
        self.assertTrue(any(event["type"] == "question_revised" for event in revised["events"]))

    def test_manifest_rejects_nested_type_and_scope_changes(self):
        original = run_research("Inspect coastal metadata")
        cases = [
            (("inputs", "max_steps"), True),
            (("inputs", "focus"), "unrestricted"),
            (("inputs", "scenario"), "live"),
            (("claims",), [{"finding": "unsupported"}]),
            (("mode",), "live_federation"),
            (("graph", "nodes", 0, "status"), "authorized"),
            (("graph", "edges", 0, "target"), 12),
            (("events", 0, "sequence"), "1"),
            (("events", 0, "details", "external_actions"), True),
            (("policies", 0, "request", "estimated_bytes"), -1),
            (("policies", 0, "profile", "authorization_required"), "false"),
            (("policies", 0, "obligations", "live_access_granted"), True),
        ]
        for path, value in cases:
            with self.subTest(path=path):
                changed = deepcopy(original)
                target = changed
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value
                with self.assertRaises(ValidationError):
                    self.manifest_validator.validate(changed)

    def test_access_draft_cannot_claim_signature_or_external_action(self):
        for field, value in [("terms_accepted", True), ("external_actions_performed", ["signed"])]:
            with self.subTest(field=field):
                changed = run_research("Inspect metadata", scenario="policy-review")
                changed["access_requests"][0][field] = value
                with self.assertRaises(ValidationError):
                    self.manifest_validator.validate(changed)

    def test_skill_descriptor_and_reference_files(self):
        self.skill_validator.validate(self.skill)
        self.assertEqual({tool["id"] for tool in self.skill["tools"]}, {"metadata.inspect", "link.verify"})
        for reference in self.skill["validation"]["references"]:
            path = reference.split(":", 1)[0]
            self.assertTrue((ROOT / "skills" / path).is_file(), reference)
        for entry in self.skill["inputs"] + self.skill["outputs"]:
            if "schema_reference" in entry:
                self.assertTrue((ROOT / "skills" / entry["schema_reference"]).is_file())

    def test_skill_cannot_expand_into_live_authorization(self):
        for path, value in [
            (("scope",), "live"),
            (("tools", 0, "external_actions"), True),
            (("compute", "network_required"), True),
            (("policy_constraints", "may_sign_agreements"), True),
        ]:
            with self.subTest(path=path):
                changed = deepcopy(self.skill)
                target = changed
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = value
                with self.assertRaises(ValidationError):
                    self.skill_validator.validate(changed)


if __name__ == "__main__":
    unittest.main()

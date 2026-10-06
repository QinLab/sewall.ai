"""Skill registry tests: descriptor validation, binding and controller dispatch.

These tests never contact a model or data repository.
"""

from copy import deepcopy
import json
from pathlib import Path
import unittest

try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

from sewall.agent import IMPLEMENTATIONS, default_registry, planner_prompt, run_agent, verify_agent_manifest
from sewall.skills import LIVE_DIRECTORY, Implementation, SkillError, SkillRegistry, validate_descriptor
from test_agent import Client, Sources, finish, review, search


ROOT = Path(__file__).resolve().parents[1]


def descriptors():
    return [json.loads(path.read_text()) for path in sorted(LIVE_DIRECTORY.glob("*.skill.json"))]


class RegistryTests(unittest.TestCase):
    def test_default_registry_preserves_action_names(self):
        registry = default_registry(earth_engine=False)
        self.assertEqual(sorted(registry.operations()), ["assess_source", "citations", "search", "taxon_inventory"])
        self.assertEqual(sorted(default_registry(earth_engine=True).operations()),
                         ["alphaearth_context", "assess_source", "chlorophyll_timeseries", "citations", "search", "taxon_inventory"])
        for entry in registry.entries():
            self.assertRegex(entry["descriptor_sha256"], r"^[a-f0-9]{64}$")

    def test_prompt_derives_from_descriptors(self):
        prompt = planner_prompt(default_registry(earth_engine=True))
        for descriptor in descriptors():
            self.assertIn(descriptor["planner_template"], prompt)
            for line in descriptor["planner_guidance"]:
                self.assertIn(line, prompt)
        self.assertIn('"action":"finish"', prompt)

    def test_descriptor_cannot_widen_implementation_values(self):
        changed = descriptors()
        search_descriptor = next(item for item in changed if item["operation"] == "search")
        search_descriptor["arguments"][0]["enum"].append("snp")
        with self.assertRaisesRegex(SkillError, "widens"):
            SkillRegistry(changed, IMPLEMENTATIONS)

    def test_descriptor_can_narrow_values(self):
        changed = descriptors()
        search_descriptor = next(item for item in changed if item["operation"] == "search")
        search_descriptor["arguments"][0]["enum"] = ["pubmed"]
        search_descriptor["planner_template"] = search_descriptor["planner_template"].replace("pubmed|gds|bioproject", "pubmed")
        registry = SkillRegistry(changed, IMPLEMENTATIONS)
        self.assertEqual(registry["search"].allowed_values("database"), frozenset({"pubmed"}))

    def test_unknown_implementation_and_duplicates_rejected(self):
        changed = descriptors()
        changed[0]["implementation"] = "sewall.agent:not_registered"
        with self.assertRaisesRegex(SkillError, "allowlisted"):
            SkillRegistry(changed, IMPLEMENTATIONS)
        with self.assertRaisesRegex(SkillError, "Duplicate"):
            SkillRegistry(descriptors() + descriptors()[:1], IMPLEMENTATIONS)
        with self.assertRaises(SkillError):
            SkillRegistry([], IMPLEMENTATIONS)

    def test_descriptor_policy_and_shape_guards(self):
        base = descriptors()[0]
        cases = [
            ("policy_constraints", "may_grant_access", True),
            ("evidence", "supports_causality", True),
            ("policy_constraints", "egress", "controlled_data"),
        ]
        for block, key, value in cases:
            with self.subTest(key=key):
                changed = deepcopy(base)
                changed[block][key] = value
                with self.assertRaises(SkillError):
                    validate_descriptor(changed)
        for field, value in (("operation", "finish"), ("planner_template", '{"action":"other"}'), ("extra", 1)):
            with self.subTest(field=field):
                changed = deepcopy(base)
                changed[field] = value
                with self.assertRaises(SkillError):
                    validate_descriptor(changed)

    def test_arguments_must_match_implementation(self):
        changed = descriptors()
        implementations = dict(IMPLEMENTATIONS)
        name = changed[0]["implementation"]
        original = implementations[name]
        implementations[name] = Implementation(name, original.arguments + ("extra",), original.allowed,
                                               original.check, original.execute)
        with self.assertRaisesRegex(SkillError, "differ"):
            SkillRegistry(changed, implementations)

    @unittest.skipIf(Draft202012Validator is None, "Optional jsonschema dependency is not installed")
    def test_descriptors_satisfy_json_schema(self):
        schema = json.loads((ROOT / "schemas/executable_skill.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for descriptor in descriptors():
            validator.validate(descriptor)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.sources = Sources()

    def run_agent(self, planner, reviewer, **kwargs):
        return run_agent("Find existing coastal plant studies", planner, reviewer,
                         search_fn=self.sources.search, fetch_fn=self.sources.fetch, **kwargs)

    def test_manifest_records_skills_and_replays(self):
        result = self.run_agent(Client(search(), finish(["ncbi:gds:1"])), Client(review(["ncbi:gds:1"])))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["plan"]["skills"], default_registry().entries())
        self.assertEqual(result["plan"]["allowed_actions"][-1], "finish")
        self.assertTrue(verify_agent_manifest(result)["valid"])

    def test_narrowed_registry_blocks_unlisted_database(self):
        changed = descriptors()
        search_descriptor = next(item for item in changed if item["operation"] == "search")
        search_descriptor["arguments"][0]["enum"] = ["pubmed"]
        search_descriptor["planner_template"] = search_descriptor["planner_template"].replace("pubmed|gds|bioproject", "pubmed")
        registry = SkillRegistry(changed, IMPLEMENTATIONS)
        result = self.run_agent(Client(search("gds")), Client(review()), registry=registry)
        self.assertEqual(result["stop_reason"], "invalid_model_action")
        self.assertEqual(self.sources.searches, [])
        self.assertNotIn("gds", result["model_calls"][0]["request"]["system_prompt"].split("database")[1][:40])

    def test_operation_absent_from_registry_is_rejected(self):
        registry = SkillRegistry([item for item in descriptors() if item["operation"] != "assess_source"], IMPLEMENTATIONS)
        action = {"action": "assess_source", "source": "eol", "reason": "check"}
        result = self.run_agent(Client(action), Client(review()), registry=registry)
        self.assertEqual(result["stop_reason"], "invalid_model_action")
        self.assertEqual(result["access_requests"], [])

    def test_tampered_skill_list_fails_replay(self):
        result = self.run_agent(Client(search(), finish(["ncbi:gds:1"])), Client(review(["ncbi:gds:1"])))
        from sewall.graph import digest
        tampered = deepcopy(result)
        tampered["plan"]["skills"].pop()
        tampered["content_digest"] = digest({k: v for k, v in tampered.items() if k != "content_digest"})
        self.assertFalse(verify_agent_manifest(tampered)["valid"])


if __name__ == "__main__":
    unittest.main()

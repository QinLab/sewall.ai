"""Scientific and policy failure boundaries for the synthetic research graph."""

from copy import deepcopy
import unittest
from unittest.mock import patch

from sewall.fixtures import DEFAULT_QUESTION, fixture_source
from sewall.graph import check_integrity, digest, plan_research, replay_manifest, run_research


def node_by_id(manifest, node_id):
    return next(node for node in manifest["graph"]["nodes"] if node["id"] == node_id)


def rehash_manifest(manifest):
    manifest["content_digest"] = digest({key: value for key, value in manifest.items() if key != "content_digest"})
    return manifest


class ResearchGraphTests(unittest.TestCase):
    def test_scenarios_report_expected_terminal_states_and_no_scientific_claims(self):
        expected = {
            "coastal": "completed", "missing-link": "abstained",
            "policy-review": "needs_review", "policy-denied": "abstained",
            "source-failure": "failed", "future-leakage": "abstained",
        }
        for scenario, status in expected.items():
            with self.subTest(scenario=scenario):
                manifest = run_research(DEFAULT_QUESTION, scenario=scenario)
                self.assertEqual(manifest["status"], status)
                self.assertEqual(node_by_id(manifest, "result")["status"], status)
                self.assertEqual(manifest["claims"], [])
                self.assertEqual(manifest["metrics"]["network_bytes"], 0)
                self.assertTrue(check_integrity(manifest)["valid"])
        completed = run_research(DEFAULT_QUESTION)
        self.assertEqual(completed["metrics"]["inspected_sources"], 5)
        self.assertEqual(completed["metrics"]["verified_links"], 4)
        self.assertTrue(all(node["status"] == "completed" for node in completed["graph"]["nodes"]))

    def test_policy_deny_and_review_prevent_record_access(self):
        class GuardedFixture(dict):
            def __getitem__(self, key):
                if key == "record":
                    raise AssertionError("A blocked source record was accessed")
                return super().__getitem__(key)

        def guarded_source(source_id, scenario):
            fixture = fixture_source(source_id, scenario)
            return GuardedFixture(fixture) if source_id == "ncbi_genotype" else fixture

        for scenario, decision in (("policy-review", "review"), ("policy-denied", "deny")):
            with self.subTest(scenario=scenario), patch("sewall.graph.fixture_source", side_effect=guarded_source):
                manifest = run_research(DEFAULT_QUESTION, scenario=scenario)
                ids = {node["id"] for node in manifest["graph"]["nodes"]}
                self.assertNotIn("source:ncbi_genotype", ids)
                self.assertEqual(node_by_id(manifest, "policy:ncbi_genotype")["status"], "blocked")
                self.assertEqual(node_by_id(manifest, "check:specimen")["status"], "blocked")
                self.assertEqual(manifest["access_requests"][0]["status"], "draft_only")
                blocked = [event for event in manifest["events"] if event["type"] == "execution_blocked"]
                self.assertEqual(blocked[0]["details"]["decision"], decision)
                self.assertEqual(manifest["metrics"]["inspected_sources"], 4)

    def test_matching_taxon_cannot_repair_missing_specimen(self):
        manifest = run_research(DEFAULT_QUESTION, scenario="missing-link")
        self.assertEqual(node_by_id(manifest, "check:taxon")["status"], "completed")
        specimen = node_by_id(manifest, "check:specimen")
        self.assertEqual(specimen["status"], "rejected")
        self.assertEqual(specimen["details"]["evidence"]["field"], "specimen_id")
        self.assertNotIn("same_specimen", {edge["relation"] for edge in manifest["graph"]["edges"]})
        self.assertEqual(manifest["status"], "abstained")

    def test_time_interval_and_availability_are_independently_checked(self):
        cases = (
            ({"period_end": "2022-01-01"}, "rejected"),
            ({"available_at": "2022-01-02"}, "rejected"),
            ({"available_at": "2022-01-01"}, "completed"),
        )
        for modifications, expected_status in cases:
            def modified_source(source_id, scenario):
                fixture = fixture_source(source_id, scenario)
                if source_id == "alphaearth":
                    fixture["record"].update(modifications)
                return fixture

            with self.subTest(modifications=modifications), patch("sewall.graph.fixture_source", side_effect=modified_source):
                manifest = run_research(DEFAULT_QUESTION)
                self.assertEqual(node_by_id(manifest, "check:time")["status"], expected_status)
                temporal_edges = [edge for edge in manifest["graph"]["edges"] if edge["relation"] == "historical_context"]
                self.assertEqual(bool(temporal_edges), expected_status == "completed")
                self.assertEqual(manifest["status"], "completed" if expected_status == "completed" else "abstained")

    def test_source_failure_produces_no_fake_record_or_dependent_link(self):
        manifest = run_research(DEFAULT_QUESTION, scenario="source-failure")
        source = node_by_id(manifest, "source:geo")
        self.assertEqual(source["status"], "failed")
        self.assertNotIn("record", source["details"])
        self.assertNotIn("record_digest", source["details"])
        self.assertEqual(node_by_id(manifest, "check:specimen")["status"], "blocked")
        self.assertEqual(node_by_id(manifest, "check:site")["status"], "blocked")
        evidence_edges = [edge for edge in manifest["graph"]["edges"] if "evidence" in edge]
        self.assertTrue(all(edge["source"] != "source:geo" and edge["target"] != "source:geo" for edge in evidence_edges))
        self.assertEqual(manifest["status"], "failed")

    def test_step_budget_stops_before_additional_source_access(self):
        with patch("sewall.graph.fixture_source", wraps=fixture_source) as source:
            manifest = run_research(DEFAULT_QUESTION, max_steps=1)
        self.assertEqual(source.call_count, 1)
        self.assertEqual(manifest["metrics"]["steps"], 1)
        self.assertEqual(manifest["metrics"]["inspected_sources"], 1)
        self.assertEqual(node_by_id(manifest, "budget")["status"], "failed")
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["claims"], [])
        self.assertTrue(replay_manifest(manifest)["valid"])

    def test_refinement_removes_irrelevant_sources_and_replays_exactly(self):
        previous = run_research(DEFAULT_QUESTION)
        before = deepcopy(previous)
        refined = run_research("Inspect organism traits", focus="traits", previous=previous)
        self.assertEqual(previous, before)
        self.assertEqual(refined["revision"], 2)
        self.assertEqual(refined["parent_digest"], previous["content_digest"])
        self.assertEqual(refined["plan"]["selected_sources"], ["eol", "pubmed"])
        source_nodes = [node["source_id"] for node in refined["graph"]["nodes"] if node["kind"] == "source"]
        self.assertEqual(source_nodes, ["eol", "pubmed"])
        event = next(event for event in refined["events"] if event["type"] == "question_revised")
        self.assertEqual(event["details"]["sources_removed"], ["alphaearth", "geo", "ncbi_genotype"])
        self.assertEqual(refined["history"], [previous["inputs"]])
        replay = replay_manifest(refined)
        self.assertTrue(replay["valid"])
        self.assertEqual(replay["actual_digest"], replay["expected_digest"])
        repeated = run_research("Inspect organism traits", focus="traits", previous=previous)
        self.assertEqual(refined, repeated)

    def test_replay_and_refinement_refuse_a_tampered_parent(self):
        manifest = run_research(DEFAULT_QUESTION)
        manifest["question"] = "Tampered question"
        self.assertFalse(check_integrity(manifest)["valid"])
        self.assertFalse(replay_manifest(manifest)["replayed"])
        with self.assertRaises(ValueError):
            run_research("Inspect traits", focus="traits", previous=manifest)

    def test_event_chain_detects_event_edit_even_if_manifest_is_rehashed(self):
        manifest = run_research(DEFAULT_QUESTION)
        manifest["events"][0]["details"]["purpose"] = "Tampered purpose"
        rehash_manifest(manifest)
        result = check_integrity(manifest)
        self.assertFalse(result["valid"])
        self.assertIn("Event chain", result["reason"])

    def test_replay_detects_changed_graph_after_content_digest_is_recomputed(self):
        manifest = run_research(DEFAULT_QUESTION)
        node_by_id(manifest, "question")["label"] = "Different graph question"
        rehash_manifest(manifest)
        replay = replay_manifest(manifest)
        self.assertFalse(replay["valid"])

    def test_invalid_graph_references_and_duplicate_ids_are_rejected(self):
        for mutation in ("dangling", "duplicate"):
            manifest = run_research(DEFAULT_QUESTION)
            if mutation == "dangling":
                manifest["graph"]["edges"][0]["source"] = "missing-node"
            else:
                manifest["graph"]["nodes"].append(deepcopy(manifest["graph"]["nodes"][0]))
            rehash_manifest(manifest)
            with self.subTest(mutation=mutation):
                self.assertFalse(check_integrity(manifest)["valid"])

    def test_malformed_event_is_rejected_without_raising(self):
        manifest = run_research(DEFAULT_QUESTION)
        manifest["events"] = [None]
        rehash_manifest(manifest)
        self.assertFalse(check_integrity(manifest)["valid"])

    def test_invalid_planner_parameters(self):
        invalid = (
            {"question": ""}, {"question": " "}, {"question": None},
            {"question": "x" * 4001}, {"focus": "unknown"},
            {"scenario": "unknown"}, {"max_steps": True}, {"max_steps": 0},
            {"max_steps": 101}, {"max_steps": 1.5},
        )
        for values in invalid:
            kwargs = {"question": DEFAULT_QUESTION, **values}
            with self.subTest(values=values), self.assertRaises(ValueError):
                plan_research(**kwargs)

    def test_rule_planner_selects_sources_for_explicit_and_inferred_focus(self):
        self.assertEqual(plan_research("Inspect traits")["selected_sources"], ["eol", "pubmed"])
        self.assertEqual(plan_research("Inspect expression")["selected_sources"], ["ncbi_genotype", "geo", "pubmed"])
        self.assertEqual(plan_research("Inspect habitat")["selected_sources"], ["eol", "alphaearth", "pubmed"])
        self.assertEqual(plan_research("Inspect expression and habitat")["focus"], "cross-scale")
        self.assertEqual(plan_research("Inspect expression", focus="traits")["selected_sources"], ["eol", "pubmed"])


if __name__ == "__main__":
    unittest.main()

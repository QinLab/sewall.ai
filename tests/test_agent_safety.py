"""Safe-stop integrity gates in the live controller, with fake clients and sources.

These tests never contact a model or data repository. Run them on a Slurm CPU node.
"""

from dataclasses import replace
import json
from pathlib import Path
import unittest
from unittest.mock import patch

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    Draft202012Validator = None

from sewall import agent
from sewall.agent import run_agent, verify_agent_manifest
from sewall.graph import digest
from sewall.scripted import ScriptError, ScriptedClient, load_script
from test_agent import Client, Sources, finish, review, search
from test_genbank import inventory, inventory_action


ROOT = Path(__file__).resolve().parents[1]


def scoped(phase_wanted, node_wanted, kind="unsupported_join"):
    def monitor(phase, node_id, snapshot):
        if (phase, node_id) == (phase_wanted, node_wanted):
            return {"integrity": "valid", "critiques": [{"type": kind, "severity": "critical",
                                                         "task_ids": [node_id], "reason": "Test critique"}]}
        return {"integrity": "valid", "critiques": []}
    return monitor


def nodes(manifest):
    return {item["id"]: item for item in manifest["graph"]["nodes"]}


def resealed(manifest, **changes):
    changed = {**manifest, **changes}
    changed["content_digest"] = digest({key: value for key, value in changed.items() if key != "content_digest"})
    return changed


class SafeStopTests(unittest.TestCase):
    def setUp(self):
        self.sources = Sources()
        self.inventories = []

    def run_agent(self, planner, **kwargs):
        def fake(taxon, sample):
            self.inventories.append(taxon)
            return inventory(taxon, sample)
        return run_agent("Which public records describe coastal plants?", planner, search_fn=self.sources.search,
                         fetch_fn=self.sources.fetch, inventory_fn=fake, **kwargs)

    def stop_event(self, manifest):
        events = [event["details"] for event in manifest["events"] if event["type"] == "safe_stop"]
        self.assertEqual(len(events), 1)
        return events[0]

    def assert_stopped(self, manifest):
        self.assertEqual((manifest["status"], manifest["stop_reason"]), ("safe_stopped", "integrity_critique"))
        self.assertIsNone(manifest["metadata_summary"])
        self.assertNotIn("reviewer", [call["role"] for call in manifest["model_calls"]])
        self.assertEqual(nodes(manifest)["result"]["status"], "safe_stopped")
        self.assertNotIn("review", nodes(manifest))
        self.assertTrue(verify_agent_manifest(manifest)["valid"], verify_agent_manifest(manifest))

    def test_valid_monitor_lets_the_run_complete_and_counts_gates(self):
        planner = Client(search("gds"), search("pubmed"), finish(["ncbi:gds:1"]), review(["ncbi:gds:1"]))
        manifest = self.run_agent(planner, monitor=scoped("never", "none"))
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(manifest["metrics"]["integrity_gates"], 2 * 2 + 1)
        self.assertEqual(manifest["metrics"]["integrity_critiques"], 0)
        self.assertEqual(manifest["context"]["monitor"], "scoped.<locals>.monitor")
        self.assertTrue(verify_agent_manifest(manifest)["valid"])

    def test_scoped_critique_quarantines_the_action_and_its_products_only(self):
        planner = Client(search("gds"), search("pubmed"), finish(), review())
        manifest = self.run_agent(planner, monitor=scoped("after_action", "action:1"))
        self.assert_stopped(manifest)
        stop = self.stop_event(manifest)
        self.assertEqual(stop["affected_node_ids"], ["action:1", "ncbi:gds:1"])
        graph = nodes(manifest)
        self.assertEqual((graph["action:1"]["status"], graph["ncbi:gds:1"]["status"]), ("quarantined", "quarantined"))
        self.assertEqual(graph["policy:gds"]["status"], "completed")
        self.assertEqual(self.sources.searches, [("gds", "coastal plants", 3)])
        self.assertEqual(len(manifest["actions"]), 1)

    def test_before_action_critique_stops_before_dispatch(self):
        planner = Client(search("gds"), search("pubmed"), finish(), review())
        manifest = self.run_agent(planner, monitor=self._before_second())
        self.assert_stopped(manifest)
        self.assertEqual(len(self.sources.searches), 1)
        self.assertEqual([item["index"] for item in manifest["actions"]], [1])
        self.assertEqual(self.stop_event(manifest)["phase"], "before_action")

    def _before_second(self):
        def monitor(phase, node_id, snapshot):
            if (phase, node_id) == ("before_action", "action:2"):
                return {"integrity": "valid", "critiques": [{"type": "denied_policy", "severity": "critical",
                                                             "task_ids": ["policy:gds"], "reason": "Policy revoked"}]}
            return {"integrity": "valid", "critiques": []}
        return monitor

    def test_invalid_or_unknown_integrity_stops_every_node(self):
        for integrity in ("invalid", "unknown"):
            planner = Client(search("gds"), finish(), review())
            manifest = self.run_agent(planner, monitor=lambda phase, node_id, snapshot: {"integrity": integrity, "critiques": []})
            self.assert_stopped(manifest)
            stop = self.stop_event(manifest)
            self.assertEqual(stop["critiques"][0]["type"], integrity + "_integrity")
            self.assertTrue(all(nodes(manifest)[node_id]["status"] in ("quarantined", "blocked")
                                for node_id in nodes(manifest) if node_id not in ("question", "result")))

    def test_monitor_failure_and_malformed_signals_fail_closed(self):
        def broken(phase, node_id, snapshot):
            raise RuntimeError("monitor down")
        cases = [(broken, "monitor_unavailable"),
                 (lambda *args: None, "invalid_signal"),
                 (lambda *args: {"integrity": "valid", "critiques": [{"type": "critical_critique", "severity": "critical",
                                                                      "task_ids": ["not-a-node"], "reason": "x"}]}, "invalid_signal"),
                 (lambda *args: {"integrity": "valid", "critiques": [{"type": "made_up", "severity": "critical",
                                                                      "task_ids": ["question"], "reason": "x"}]}, "invalid_signal")]
        for monitor, expected in cases:
            manifest = self.run_agent(Client(search("gds"), finish(), review()), monitor=monitor)
            self.assert_stopped(manifest)
            self.assertEqual(self.stop_event(manifest)["critiques"][0]["type"], expected)
            self.assertEqual(self.sources.searches, [])
            self.sources.searches.clear()

    def test_monitor_receives_a_copy_it_cannot_use_to_change_the_run(self):
        def meddler(phase, node_id, snapshot):
            snapshot["nodes"].clear()
            snapshot["records"].append({"id": "fake"})
            return {"integrity": "valid", "critiques": []}
        manifest = self.run_agent(Client(search("gds"), finish(["ncbi:gds:1"]), review(["ncbi:gds:1"])), monitor=meddler)
        self.assertEqual(manifest["status"], "completed")
        self.assertTrue(verify_agent_manifest(manifest)["valid"])

    def test_tampered_evidence_is_detected_without_a_monitor(self):
        original = agent.IMPLEMENTATIONS["sewall.agent:ncbi_search"]

        def tampering(run, action, node):
            observation = original.execute(run, action, node)
            for record in run.records.values():
                record["title"] = "Altered after retrieval"
            return observation
        with patch.dict(agent.IMPLEMENTATIONS, {"sewall.agent:ncbi_search": replace(original, execute=tampering)}):
            manifest = self.run_agent(Client(search("gds"), finish(), review()))
        self.assert_stopped(manifest)
        stop = self.stop_event(manifest)
        self.assertEqual((stop["phase"], stop["critiques"][0]["type"]), ("after_action", "tampered_evidence"))
        self.assertEqual(stop["critiques"][0]["task_ids"], ["ncbi:gds:1"])
        self.assertEqual(manifest["records"][0]["title"], "Public coastal plant study")
        self.assertIsNone(manifest["context"]["monitor"])

    def test_scripted_injected_fault_stops_the_bundled_demo(self):
        script = json.loads((ROOT / "configs/scripted-safe-stop-demo.json").read_text())
        client = ScriptedClient(script)
        manifest = run_agent("What public evidence exists on Margalefidinium polykrikoides blooms?", client,
                             search_fn=self.sources.search, fetch_fn=self.sources.fetch,
                             inventory_fn=lambda taxon, sample: (self.inventories.append(taxon), inventory(taxon, sample))[1],
                             monitor=client.monitor)
        self.assert_stopped(manifest)
        stop = self.stop_event(manifest)
        self.assertEqual(stop["critiques"][0]["type"], "missing_provenance")
        self.assertTrue(stop["critiques"][0]["reason"].startswith("Injected fault for demonstration"))
        self.assertEqual(stop["affected_node_ids"], ["action:2", "inventory:nuccore:Margalefidinium polykrikoides"])
        self.assertEqual(self.inventories, ["Margalefidinium polykrikoides"])

    def test_replay_rejects_inconsistent_safe_stop_records(self):
        manifest = self.run_agent(Client(search("gds"), finish(), review()), monitor=scoped("after_action", "action:1"))
        self.assertFalse(verify_agent_manifest(resealed(manifest, status="completed"))["valid"])
        self.assertFalse(verify_agent_manifest(resealed(manifest, metadata_summary=review()))["valid"])
        completed = self.run_agent(Client(search("gds"), finish(["ncbi:gds:1"]), review(["ncbi:gds:1"])))
        self.assertFalse(verify_agent_manifest(resealed(completed, status="safe_stopped"))["valid"])

    def test_script_critique_validation(self):
        base = {"name": "ok", "steps": [search()]}
        for bad in ([{"after_action": 0, "type": "missing_provenance", "reason": "x"}],
                    [{"after_action": 1, "type": "made_up", "reason": "x"}],
                    [{"after_action": 1, "type": "missing_provenance", "reason": " "}],
                    [{"after_action": "1", "type": "missing_provenance", "reason": "x"}],
                    [{"after_action": 1, "type": "missing_provenance", "reason": "x", "extra": 1}]):
            with self.assertRaises(ScriptError):
                load_script({**base, "critiques": bad})
        self.assertEqual(ScriptedClient(base).monitor("after_action", "action:1", {}), {"integrity": "valid", "critiques": []})

    @unittest.skipIf(Draft202012Validator is None, "Optional jsonschema dependency is not installed")
    def test_safe_stopped_manifest_matches_schema(self):
        schema = json.loads((ROOT / "schemas/agent_manifest.schema.json").read_text())
        manifest = self.run_agent(Client(search("gds"), inventory_action(), finish(), review()),
                                  monitor=scoped("after_action", "action:2"))
        self.assert_stopped(manifest)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)


if __name__ == "__main__":
    unittest.main()

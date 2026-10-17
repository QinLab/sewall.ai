"""Gated release risk assessment tests over offline manifests. No model or source is contacted.

Run on a Slurm CPU node with the rest of the suite.
"""

from copy import deepcopy
from datetime import datetime, timezone
import unittest

from sewall.agent import default_registry, run_agent
from sewall.earthengine import chlorophyll_timeseries
from sewall.floodsensors import FloodSensorError, flood_sensors
from sewall import riskgate
from sewall.riskgate import CATALOG, assess, render_risk_html, verify_risk_gate
from test_agent import Client, Sources, finish, review, search
from test_earthengine import S2, Computed, s2_action
from test_floodsensors import NOW, Services

try:
    from sewall.forum import run_forum
except ImportError:
    run_forum = None
needs_forum = unittest.skipIf(run_forum is None, "sewall.forum is not present")


def build(question="Did the chlorophyll proxy rise during the 2020 bloom in the Lafayette River?"):
    sources = Sources()

    def earth(operation, arguments):
        return chlorophyll_timeseries(compute=Computed(S2), **arguments)

    ids = ["ncbi:pubmed:11"]
    return run_agent(question, Client(search("pubmed", "bloom"), s2_action(), finish(ids)), Client(review(ids)),
                     search_fn=sources.search, fetch_fn=sources.fetch, earth_fn=earth,
                     registry=default_registry(earth_engine=True))


def flood_run(now=NOW, fail=False):
    def flood(operation, arguments):
        if fail and arguments["area"] == "baltimore":
            raise FloodSensorError("Every flood sensor service failed; Request failed (URLError); no retry attempted")
        return flood_sensors(get=Services(), now=now, **arguments)

    sources = Sources()
    steps = [{"action": "flood_sensors", "area": area, "reason": "Current levels"} for area in ("norfolk", "baltimore")]
    return run_agent("Is anything flooding right now?", Client(*steps, finish()), Client(review()),
                     search_fn=sources.search, fetch_fn=sources.fetch, flood_fn=flood,
                     registry=default_registry(earth_engine=False))


def item(gate, operation):
    return next(i for i in gate["items"] if i["operation"] == operation)


def modes(entry):
    return {m["id"]: m for m in entry["modes"]}


class GateTests(unittest.TestCase):
    def test_unreviewed_proxy_is_held_and_holds_the_result(self):
        manifest = build()
        gate = assess(manifest)
        self.assertEqual([g["id"] for g in gate["gates"]],
                         ["G0-manifest", "G1-run-status", "G2-containment", "G3-peer-review", "G4-risk"])
        self.assertFalse(next(g for g in gate["gates"] if g["id"] == "G3-peer-review")["passed"])
        s2 = item(gate, "chlorophyll_timeseries")
        found = modes(s2)
        self.assertEqual(found["FM06"]["occurrence"], 6)
        self.assertEqual(found["FM08"]["detection"], CATALOG["FM08"][3] + 1)
        self.assertFalse(s2["reviewed"])
        self.assertEqual(found["FM08"]["rpn"], 168)
        self.assertEqual(s2["decision"], "hold_for_validation")
        self.assertEqual(item(gate, "search")["decision"], "release")
        self.assertIn(s2["node"], gate["held"])
        self.assertEqual(gate["decision"], "hold_for_validation")
        for mode in s2["modes"]:
            self.assertEqual(mode["rpn"], mode["severity"] * mode["occurrence"] * mode["detection"])
        self.assertEqual(gate["items"][-1]["node"], "result")
        self.assertEqual(assess(manifest)["content_digest"], gate["content_digest"])
        self.assertTrue(verify_risk_gate(gate, manifest)["valid"])

    @needs_forum
    def test_forum_concerns_attach_to_the_outputs_they_cite(self):
        manifest = build()
        forum = run_forum(manifest)
        gate = assess(manifest, forum)
        self.assertTrue(all(g["passed"] for g in gate["gates"] if g["id"].startswith("G0")))
        self.assertEqual(gate["forum_digest"], forum["content_digest"])
        cited = {r for p in forum["posts"] if riskgate._is_review(p) for r in p["refs"]}
        s2 = item(gate, "chlorophyll_timeseries")
        self.assertEqual(s2["reviewed"], s2["node"] in cited)
        unresolved = [c for c in forum["synthesis"]["concerns"] if c["status"] != "resolved"]
        attached = [m for i in gate["items"] for m in i["modes"] if m["id"] in ("FM12", "FM13")]
        self.assertEqual(bool(unresolved), bool(attached))
        self.assertTrue(verify_risk_gate(gate, manifest, forum)["valid"])
        self.assertFalse(verify_risk_gate(gate, manifest)["valid"])

    @needs_forum
    def test_support_does_not_count_as_review(self):
        from sewall.forum import ModelReviewer
        manifest = build()
        node = item(assess(manifest), "chlorophyll_timeseries")["node"]

        def gate_for(kind):
            post = {"kind": kind, "text": "Looks fine to me.", "refs": [node]}
            reviewer = ModelReviewer("fan", Client({"posts": [post]}, {"posts": []}))
            return item(assess(manifest, run_forum(manifest, reviewers=[("fan", reviewer)])), "chlorophyll_timeseries")

        self.assertFalse(gate_for("support")["reviewed"])
        self.assertEqual(modes(gate_for("support"))["FM08"]["rpn"], 168)
        self.assertTrue(gate_for("limitation")["reviewed"])

    @needs_forum
    def test_forum_for_another_run_blocks_everything(self):
        forum = run_forum(build())
        other = build(question="What does the chlorophyll proxy show in the Lafayette River in 2020?")
        gate = assess(other, forum)
        self.assertEqual(gate["decision"], "blocked")
        self.assertEqual(gate["items"], [])
        self.assertFalse(next(g for g in gate["gates"] if g["id"] == "G0-forum")["passed"])

    def test_tampered_manifest_blocks_everything(self):
        manifest = build()
        manifest["actions"][1]["observation"]["scenes_with_water_pixels"] = 40
        gate = assess(manifest)
        self.assertEqual((gate["decision"], gate["released"]), ("blocked", []))

    def test_fresh_flood_readings_release_and_stale_readings_hold(self):
        fresh = assess(flood_run())
        norfolk = item(fresh, "flood_sensors")
        self.assertNotIn("FM01", modes(norfolk))
        self.assertIn("FM04", modes(norfolk))
        self.assertIn("FM05", modes(norfolk))
        self.assertIn(norfolk["decision"], ("release", "release_with_limitations"))
        stale = assess(flood_run(now=datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)))
        norfolk = item(stale, "flood_sensors")
        self.assertEqual(modes(norfolk)["FM01"]["occurrence"], 10)
        self.assertEqual(norfolk["decision"], "hold_for_validation")
        self.assertIn(norfolk["node"], stale["held"])
        empty = {"nwps_gauges": [], "coops_stations": [], "usgs_series": [], "unavailable": []}
        self.assertEqual([m["id"] for m in riskgate._flood_modes(empty, True)], ["FM10"])
        result = stale["items"][-1]
        self.assertIn("FM16", modes(result))
        self.assertEqual(stale["decision"], result["decision"])

    def test_failed_action_is_blocked_and_marks_the_result(self):
        gate = assess(flood_run(fail=True))
        failed = [i for i in gate["items"] if i["operation"] == "flood_sensors" and i["decision"] == "blocked"]
        self.assertEqual(len(failed), 1)
        self.assertIn("failed", failed[0]["reason"])
        self.assertIn("FM14", modes(gate["items"][-1]))
        self.assertIn("FM15", modes(gate["items"][-1]))

    def test_quarantined_node_is_contained(self):
        manifest = build()
        gate = assess(manifest)
        node = item(gate, "chlorophyll_timeseries")["node"]
        for entry in manifest["graph"]["nodes"]:
            if entry["id"] == node:
                entry["status"] = "quarantined"
        original = riskgate.verify_agent_manifest
        riskgate.verify_agent_manifest = lambda m: {"valid": True}
        try:
            contained = assess(manifest)
        finally:
            riskgate.verify_agent_manifest = original
        self.assertEqual(item(contained, "chlorophyll_timeseries")["decision"], "blocked")
        self.assertFalse(next(g for g in contained["gates"] if g["id"] == "G2-containment")["passed"])

    def test_verification_rejects_edits(self):
        manifest = build()
        gate = assess(manifest)
        lowered = deepcopy(gate)
        lowered["items"][0]["decision"] = "blocked"
        self.assertFalse(verify_risk_gate(lowered, manifest)["valid"])
        from sewall.graph import digest
        lowered["content_digest"] = digest({k: v for k, v in lowered.items() if k != "content_digest"})
        self.assertIn("Recomputed", verify_risk_gate(lowered, manifest)["reason"])
        self.assertFalse(verify_risk_gate(gate, flood_run())["valid"])

    def test_html_is_escaped(self):
        gate = assess(build())
        gate["items"][0]["modes"].append({"id": "FM99", "mode": "<script>", "severity": 1, "occurrence": 1,
                                          "detection": 1, "rpn": 1, "evidence": "x", "mitigation": "y"})
        page = render_risk_html(gate)
        self.assertNotIn("<script>", page)
        self.assertIn("Release gate", page)


if __name__ == "__main__":
    unittest.main()

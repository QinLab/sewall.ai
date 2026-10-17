"""Cross-run critique panel tests over offline manifests. No model or source is contacted.

Run on a Slurm CPU node with the rest of the suite.
"""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest

from sewall.__main__ import main
from sewall.graph import digest
from sewall.panel import ModelPanelist, check_panel_draft, render_panel_html, run_panel, verify_panel
from sewall.forum import manifest_view
from test_agent import Client
from test_forum import build

NO_BLOOM = "What does the chlorophyll proxy show in the Lafayette River in 2020?"


def runs():
    return {"alice": build(), "bob": build(bloom_title=True), "carol": build(question=NO_BLOOM)}


def entries(manifests, **panelists):
    return [(name, manifest, panelists.get(name)) for name, manifest in manifests.items()]


def find(panel, author=None, target=None, topic=None, kind=None):
    return [p for p in panel["posts"] if (author is None or p["author"] == author)
            and (target is None or p["target"] == target) and (topic is None or p.get("topic") == topic)
            and (kind is None or p["kind"] == kind)]


class RulePanelTests(unittest.TestCase):
    def setUp(self):
        self.manifests = runs()
        self.panel = run_panel(entries(self.manifests))

    def test_researchers_critique_only_other_runs(self):
        panel = self.panel
        self.assertEqual(panel["kind"], "sewall_review_panel")
        self.assertEqual(panel["rejected"], [])
        self.assertTrue(panel["posts"])
        for post in panel["posts"]:
            if post["kind"] != "reply":
                self.assertNotEqual(post["author"], post["target"])
        # Every run's critique comes from its peers, and shared records are noted as reproduced.
        for name in self.manifests:
            self.assertTrue(find(panel, target=name, kind="concern"))
            self.assertTrue(find(panel, target=name, topic="shared-records"))
        cross = [r for p in find(panel, topic="shared-records") for r in p["refs"] if "::" in r]
        self.assertTrue(cross)
        self.assertTrue(verify_panel(panel, self.manifests)["valid"])
        self.assertEqual(run_panel(entries(self.manifests))["content_digest"], panel["content_digest"])

    def test_owner_defends_concedes_and_peers_second(self):
        runs_ = self.panel["synthesis"]["runs"]
        # Bob holds a bloom paper, so he answers the in situ critique; Carol seconds it, so it is contested.
        concern = find(self.panel, target="bob", topic="no-in-situ")[0]
        stances = {p["author"]: p["stance"] for p in self.panel["posts"] if p.get("reply_to") == concern["id"]}
        self.assertEqual(stances, {"bob": "answer", "carol": "agree"})
        status = {c["post"]: c["status"] for c in runs_["bob"]["concerns"]}
        self.assertEqual(status[concern["id"]], "contested")
        # Alice has no bloom paper, so she concedes and the concern stays blocking.
        concern = find(self.panel, target="alice", topic="no-in-situ")[0]
        self.assertIn(concern["id"], runs_["alice"]["conceded"])
        self.assertIn(concern["id"], runs_["alice"]["blocking_concerns"])
        self.assertEqual({n: r["verdict"] for n, r in runs_.items()},
                         {"alice": "needs_revision", "bob": "needs_revision",
                          "carol": "acceptable_as_retrieved_evidence"})
        self.assertIs(self.panel["synthesis"]["biological_claims_validated"], False)

    def test_tampering_and_wrong_runs_are_detected(self):
        edited = deepcopy(self.panel)
        edited["posts"][0]["target"] = edited["posts"][0]["author"]
        edited["content_digest"] = digest({k: v for k, v in edited.items() if k != "content_digest"})
        self.assertFalse(verify_panel(edited, self.manifests)["valid"])
        verdict = deepcopy(self.panel)
        verdict["synthesis"]["runs"]["alice"]["verdict"] = "acceptable_as_retrieved_evidence"
        verdict["content_digest"] = digest({k: v for k, v in verdict.items() if k != "content_digest"})
        self.assertIn("synthesis", verify_panel(verdict, self.manifests)["reason"])
        swapped = dict(self.manifests, alice=self.manifests["bob"], bob=self.manifests["alice"])
        self.assertFalse(verify_panel(self.panel, swapped)["valid"])
        self.assertIn("one manifest per panelist",
                      verify_panel(self.panel, {"alice": self.manifests["alice"]})["reason"])

    def test_panel_requires_distinct_verified_runs(self):
        with self.assertRaises(ValueError):
            run_panel(entries({"alice": self.manifests["alice"]}))
        with self.assertRaises(ValueError):
            run_panel([("alice", self.manifests["alice"], None), ("bob", self.manifests["alice"], None)])
        broken = deepcopy(self.manifests["bob"])
        broken["question"] = "Something else"
        with self.assertRaises(ValueError):
            run_panel(entries({"alice": self.manifests["alice"], "bob": broken}))
        with self.assertRaises(ValueError):
            run_panel(entries({"alice": self.manifests["alice"], "Bob Smith": self.manifests["bob"]}))

    def test_html_escapes_text(self):
        page = render_panel_html(run_panel(entries(self.manifests), topic="<script>"))
        self.assertIn("bob's run", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertNotIn("<script>", page)


class ModelPanelistTests(unittest.TestCase):
    def setUp(self):
        self.manifests = runs()
        self.node = self.manifests["alice"]["actions"][1]["observation"]["summary_node"]

    def test_model_posts_pass_the_same_checks(self):
        round1 = {"posts": [
            {"target": "alice", "kind": "concern", "severity": "major", "text": "One season shows no trend.",
             "refs": [self.node]},
            {"target": "dave", "kind": "concern", "severity": "minor", "text": "Own run.", "refs": [self.node]},
            {"target": "alice", "kind": "support", "text": "Wrong run's node.", "refs": ["ncbi:pubmed:999"]},
            {"target": "alice", "kind": "support", "text": "Cross-run citation.",
             "refs": [self.node, f"dave::{self.node}"]},
            {"target": ["alice"], "kind": "support", "text": "Bad target.", "refs": [self.node]},
        ]}

        def round2(payload):
            first = [p for p in payload["thread"] if p["author"] == "alice"][0]  # Alice critiques bob first.
            return {"posts": [{"target": "carol", "kind": "reply", "stance": "agree", "reply_to": first["id"],
                               "text": "Target differs from the post.", "refs": [first["id"]]},
                              {"target": first["target"], "kind": "reply", "stance": "agree",
                               "reply_to": first["id"], "text": "Agreed.", "refs": [first["id"]]}]}

        client = Client(round1, round2)
        manifests = dict(self.manifests, dave=build(question="Chlorophyll in 2020?"))
        panel = run_panel(entries(manifests, dave=ModelPanelist(client)))
        reasons = [r["reason"] for r in panel["rejected"]]
        self.assertTrue(any("own run" in r for r in reasons))
        self.assertTrue(any("not a node" in r for r in reasons))
        self.assertTrue(any("target must name" in r for r in reasons))
        self.assertTrue(any("same target" in r for r in reasons))
        self.assertEqual(len(find(panel, author="dave")), 3)
        self.assertEqual([c["round"] for c in panel["model_calls"]], [1, 2])
        self.assertFalse(panel["rule_only"])
        self.assertIn("never validate", client.requests[0][0])
        self.assertTrue(verify_panel(panel, manifests)["valid"])

    def test_failing_model_panelist_is_recorded(self):
        client = Client(RuntimeError("down"), {"posts": "not a list"})
        panel = run_panel(entries(self.manifests, carol=ModelPanelist(client)))
        self.assertEqual([c["status"] for c in panel["model_calls"]],
                         ["failed (RuntimeError)", "failed (ValueError)"])
        self.assertFalse(find(panel, author="carol"))

    def test_check_panel_draft(self):
        views = {name: manifest_view(m) for name, m in self.manifests.items()}
        draft = {"target": "bob", "kind": "support", "text": "x", "refs": [f"alice::{self.node}"]}
        self.assertEqual(check_panel_draft(draft, "alice", 1, views, [])["target"], "bob")
        with self.assertRaises(ValueError):
            check_panel_draft(dict(draft, target="alice"), "alice", 1, views, [])
        with self.assertRaises(ValueError):
            check_panel_draft(dict(draft, refs=["alice::nope"]), "alice", 1, views, [])


class CommandTests(unittest.TestCase):
    def test_panel_and_panel_verify_commands(self):
        manifests = runs()
        with tempfile.TemporaryDirectory() as tmp:
            args = []
            for name, manifest in manifests.items():
                path = Path(tmp) / f"{name}.json"
                path.write_text(json.dumps(manifest))
                args += ["--run", name, str(path)]
            out = Path(tmp) / "panel"
            with redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(["panel", *args, "--out", str(out)]), 0)
            self.assertEqual(json.loads(printed.getvalue())["verdicts"]["carol"], "acceptable_as_retrieved_evidence")
            self.assertTrue((out / "panel.html").exists())
            with redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(["panel-verify", str(out / "panel.json"), *args]), 0)
            self.assertTrue(json.loads(printed.getvalue())["valid"])
            with self.assertRaises(SystemExit):
                main(["panel", *args, "--out", str(out)])


if __name__ == "__main__":
    unittest.main()

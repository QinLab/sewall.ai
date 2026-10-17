"""Agentic peer-review forum tests over offline manifests. No model or source is contacted.

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
from sewall.agent import default_registry, run_agent
from sewall.earthengine import chlorophyll_timeseries
from sewall.evidence import _normalize
from sewall.forum import _LOCAL, ModelReviewer, check_draft, manifest_view, render_forum_html, run_forum, synthesize, verify_forum
from sewall.graph import digest
from test_agent import Client, Sources, finish, review, search
from test_earthengine import S2, Computed, s2_action

QUESTION = "Did the chlorophyll proxy rise during the 2020 bloom in the Lafayette River?"


def build(question=QUESTION, bloom_title=False, actions=None, ids=("ncbi:pubmed:11",)):
    sources = Sources()
    if bloom_title:
        sources.records["pubmed", "11"] = _normalize("pubmed", "11", {"uid": "11", "title": "A dinoflagellate bloom in the Lafayette River"})

    def earth(operation, arguments):
        return chlorophyll_timeseries(compute=Computed(S2), **arguments)

    steps = actions if actions is not None else [search("pubmed", "bloom"), s2_action()]
    return run_agent(question, Client(*steps, finish(list(ids))), Client(review(list(ids))),
                     search_fn=sources.search, fetch_fn=sources.fetch, earth_fn=earth,
                     registry=default_registry(earth_engine=True))


def by(forum, author=None, topic=None, kind=None):
    return [p for p in forum["posts"] if (author is None or p["author"] == author)
            and (topic is None or p.get("topic") == topic) and (kind is None or p["kind"] == kind)]


class RuleForumTests(unittest.TestCase):
    def setUp(self):
        self.manifest = build()
        self.assertEqual(self.manifest["status"], "completed")

    def test_rule_forum_discusses_and_verifies(self):
        forum = run_forum(self.manifest)
        self.assertTrue(verify_forum(forum, self.manifest)["valid"])
        self.assertTrue(forum["rule_only"])
        self.assertEqual(forum["model_calls"], [])
        self.assertEqual(forum["rejected"], [])
        self.assertEqual(forum["manifest_digest"], self.manifest["content_digest"])
        # Two of the three fake scenes have water pixels, so the month is flagged as sparse.
        self.assertTrue(by(forum, author="earthengine", kind="concern"))
        question = by(forum, topic="scene-counts", kind="question")[0]
        answer = [p for p in forum["posts"] if p.get("reply_to") == question["id"]][0]
        self.assertEqual((answer["author"], answer["stance"]), ("earthengine", "answer"))
        self.assertTrue(by(forum, topic="review-omits-observations"))
        # No bloom paper, so nobody answers the in situ concern and it stays open and blocking.
        concern = by(forum, topic="no-in-situ")[0]
        status = {c["post"]: c["status"] for c in forum["synthesis"]["concerns"]}
        self.assertEqual(status[concern["id"]], "open")
        self.assertEqual(forum["synthesis"]["verdict"], "needs_revision")
        self.assertIs(forum["synthesis"]["biological_claims_validated"], False)
        self.assertEqual(run_forum(self.manifest)["content_digest"], forum["content_digest"])

    def test_answered_and_agreed_concern_is_contested(self):
        manifest = build(bloom_title=True)
        forum = run_forum(manifest)
        concern = by(forum, topic="no-in-situ")[0]
        stances = {p["author"]: p["stance"] for p in forum["posts"] if p.get("reply_to") == concern["id"]}
        self.assertEqual(stances, {"ncbi": "answer", "earthengine": "agree"})
        status = {c["post"]: c["status"] for c in forum["synthesis"]["concerns"]}
        self.assertEqual(status[concern["id"]], "contested")
        self.assertIn(concern["id"], forum["synthesis"]["blocking_concerns"])

    def test_only_replies_with_new_run_evidence_resolve_a_concern(self):
        view = manifest_view(self.manifest)
        node = self.manifest["actions"][1]["observation"]["summary_node"]
        concern = {"id": "post:1", "author": "a", "kind": "concern", "severity": "major", "refs": [node]}

        def status(*replies):
            posts = [concern, *({"id": f"post:{n}", "author": "b", "kind": "reply", "reply_to": "post:1", **r}
                                for n, r in enumerate(replies, 2))]
            return synthesize(view, posts)["concerns"][0]["status"]

        self.assertEqual(status({"stance": "answer", "refs": ["post:1"]}), "open")
        self.assertEqual(status({"stance": "answer", "refs": ["post:1", node]}), "open")
        self.assertEqual(status({"stance": "answer", "refs": ["question", "result"]}), "open")
        self.assertEqual(status({"stance": "answer", "refs": ["ncbi:pubmed:11"]}), "resolved")
        self.assertEqual(status({"stance": "dispute", "refs": ["ncbi:pubmed:11"]}), "resolved")
        self.assertEqual(status({"stance": "dispute", "refs": ["post:1"]}), "open")
        self.assertEqual(status({"stance": "answer", "refs": ["ncbi:pubmed:11"]},
                                {"stance": "agree", "refs": ["post:1"]}), "contested")

    def test_local_origin_and_bloom_match_whole_words(self):
        self.assertIsNone(_LOCAL.search("South Korea: Busan"))
        self.assertIsNotNone(_LOCAL.search("USA: Virginia, York River"))
        forum = run_forum(build(question="What does the chlorophyll proxy show near Bloomington in 2020?"))
        self.assertFalse(by(forum, author="ecology"))

    def test_without_bloom_question_and_no_major_concern_is_acceptable(self):
        forum = run_forum(build(question="What does the chlorophyll proxy show in the Lafayette River in 2020?"))
        self.assertFalse(by(forum, author="ecology"))
        self.assertEqual(forum["synthesis"]["verdict"], "acceptable_as_retrieved_evidence")

    def test_run_without_evidence_is_insufficient(self):
        manifest = build(actions=[s2_action()], ids=())
        self.assertEqual(manifest["status"], "no_evidence")
        forum = run_forum(manifest)
        self.assertEqual(forum["synthesis"]["verdict"], "insufficient_evidence")
        self.assertTrue(by(forum, topic="run-incomplete"))
        self.assertTrue(verify_forum(forum, manifest)["valid"])

    def test_tampering_and_wrong_binding_are_detected(self):
        forum = run_forum(self.manifest)
        edited = deepcopy(forum)
        edited["posts"][0]["text"] = "Edited after the fact."
        self.assertFalse(verify_forum(edited, self.manifest)["valid"])
        edited["content_digest"] = digest({k: v for k, v in edited.items() if k != "content_digest"})
        self.assertIn("hash", verify_forum(edited, self.manifest)["reason"])
        verdict = deepcopy(forum)
        verdict["synthesis"]["verdict"] = "acceptable_as_retrieved_evidence"
        verdict["content_digest"] = digest({k: v for k, v in verdict.items() if k != "content_digest"})
        self.assertIn("synthesis", verify_forum(verdict, self.manifest)["reason"])
        other = build(bloom_title=True)
        self.assertIn("different manifest", verify_forum(forum, other)["reason"])

    def test_unverified_manifest_is_refused(self):
        broken = deepcopy(self.manifest)
        broken["question"] = "Something else"
        with self.assertRaises(ValueError):
            run_forum(broken)

    def test_html_escapes_post_text(self):
        forum = run_forum(self.manifest, topic="<script>")
        page = render_forum_html(forum)
        self.assertIn("needs revision", page)
        self.assertNotIn("<script>", page)


class ModelReviewerTests(unittest.TestCase):
    def setUp(self):
        self.manifest = build()
        self.node = self.manifest["actions"][1]["observation"]["summary_node"]

    def test_model_posts_pass_the_same_checks(self):
        round1 = {"posts": [
            {"kind": "concern", "severity": "major", "text": "One season cannot show a trend.", "refs": [self.node]},
            {"kind": "support", "text": "Cites a node that does not exist.", "refs": ["ncbi:pubmed:999"]},
            {"kind": "reply", "stance": "agree", "reply_to": "post:1", "text": "Too early.", "refs": ["post:1"]},
        ]}

        def round2(payload):
            mine = [p for p in payload["thread"] if p["author"] == "skeptic"][0]
            first = payload["thread"][0]
            return {"posts": [{"kind": "reply", "stance": "agree", "reply_to": first["id"], "text": "Agreed.",
                               "refs": [first["id"]]},
                              {"kind": "reply", "stance": "answer", "reply_to": mine["id"], "text": "Self answer.",
                               "refs": [mine["id"]]}]}

        client = Client(round1, round2)
        from sewall.forum import RULE_REVIEWERS
        forum = run_forum(self.manifest, reviewers=[*RULE_REVIEWERS, ("skeptic", ModelReviewer("skeptic", client))])
        reasons = [r["reason"] for r in forum["rejected"]]
        self.assertTrue(any("not a node" in r for r in reasons))
        self.assertTrue(any("round 2" in r for r in reasons))
        self.assertTrue(any("another reviewer" in r for r in reasons))
        self.assertEqual(len(by(forum, author="skeptic")), 2)
        self.assertEqual([c["round"] for c in forum["model_calls"]], [1, 2])
        self.assertEqual(forum["model_calls"][0]["request_id"], "mock-call")
        self.assertFalse(forum["rule_only"])
        self.assertEqual(len(client.requests), 2)
        self.assertIn("never validate", client.requests[0][0])
        self.assertTrue(verify_forum(forum, self.manifest)["valid"])

    def test_failing_model_reviewer_is_recorded(self):
        client = Client(RuntimeError("down"), {"posts": "not a list"})
        forum = run_forum(self.manifest, reviewers=[("skeptic", ModelReviewer("skeptic", client))])
        self.assertEqual([c["status"] for c in forum["model_calls"]],
                         ["failed (RuntimeError)", "failed (ValueError)"])
        self.assertEqual(forum["posts"], [])
        self.assertEqual(forum["synthesis"]["verdict"], "acceptable_as_retrieved_evidence")

    def test_check_draft_rules(self):
        known = {self.node}
        thread = [{"id": "post:1", "author": "a", "kind": "support"}]
        with self.assertRaises(ValueError):
            check_draft({"kind": "support", "severity": "major", "text": "x", "refs": [self.node]}, "b", 1, known, thread)
        with self.assertRaises(ValueError):
            check_draft({"kind": "concern", "text": "x", "refs": [self.node]}, "b", 1, known, thread)
        with self.assertRaises(ValueError):
            check_draft({"kind": "support", "text": "x", "refs": [self.node], "extra": 1}, "b", 1, known, thread)
        with self.assertRaises(ValueError):
            check_draft({"kind": "support", "text": "x" * 1001, "refs": [self.node]}, "b", 1, known, thread)
        body = check_draft({"kind": "reply", "stance": "dispute", "reply_to": "post:1", "text": " x ",
                            "refs": ["post:1"]}, "b", 2, known, thread)
        self.assertEqual(body["text"], "x")


class CommandTests(unittest.TestCase):
    def test_forum_and_forum_verify_commands(self):
        manifest = build()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(manifest))
            out = Path(tmp) / "forum"
            with redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(["forum", str(path), "--out", str(out)]), 0)
            self.assertEqual(json.loads(printed.getvalue())["verdict"], "needs_revision")
            self.assertTrue((out / "forum.html").exists())
            with redirect_stdout(io.StringIO()) as printed:
                self.assertEqual(main(["forum-verify", str(out / "forum.json"), str(path)]), 0)
            self.assertTrue(json.loads(printed.getvalue())["valid"])
            with self.assertRaises(SystemExit):
                main(["forum", str(path), "--out", str(out)])


if __name__ == "__main__":
    unittest.main()

"""GenBank marker fetch and ribotype assignment tests with mocked transport.

These tests never contact NCBI or a model. Run them on a Slurm CPU node.
"""

from copy import deepcopy
import json
import os
from pathlib import Path
import random
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    Draft202012Validator = None

from sewall import ribotype
from sewall.agent import run_agent, verify_agent_manifest
from sewall.genbank import InventoryError, fetch_markers
from sewall.graph import digest
from test_agent import Client, Sources, finish, reseal, review, search
from test_evidence import Response
from test_genbank import esearch, esummary


ROOT = Path(__file__).resolve().parents[1]
TAXON = "Margalefidinium polykrikoides"


def fasta(*pairs):
    return "".join(f">{accession} test sequence\n{sequence[:60]}\n{sequence[60:]}\n" for accession, sequence in pairs)


def random_sequence(length, seed):
    generator = random.Random(seed)
    return "".join(generator.choice("ACGT") for _ in range(length))


def mutate(sequence, every, seed, stop=None):
    generator = random.Random(seed)
    return "".join(generator.choice("ACGT".replace(base, "")) if index % every == 0 and index < (stop or len(sequence))
                   else base for index, base in enumerate(sequence))


BASE = random_sequence(600, 1)


def panel(**changes):
    """Alpha and Beta differ only in the first half; Gamma has one reference covering the second half."""
    beta = mutate(BASE, 20, 2, stop=300)
    references = [("R1", "Alpha", BASE), ("R2", "Alpha", mutate(BASE, 97, 3)),
                  ("R3", "Beta", beta), ("R4", "Beta", mutate(beta, 89, 4)), ("R5", "Gamma", mutate(BASE[300:], 25, 5))]
    value = {"panel_id": "test.panel", "version": "0.0.1", "taxon": "Testus testus", "marker": "lsu", "k": 8,
             "thresholds": {"min_score": 0.9, "min_margin": 0.05, "min_cover": 0.2},
             "ribotypes": ["Alpha", "Beta", "Gamma"],
             "references": [{"accession": accession, "ribotype": label, "label_source": "Test 2026",
                             "label_status": "test", "origin": "nowhere", "sequence": sequence}
                            for accession, label, sequence in references],
             "excluded": [{"accession": "X1", "reason": "Label conflict"}],
             "label_note": "Synthetic.", "limitations": ["Synthetic panel."]}
    value.update(changes)
    return value


class RibotypeTests(unittest.TestCase):
    def setUp(self):
        self.panel = panel()

    def calls(self, *queries):
        result = ribotype.assign([{"accession": a, "sequence": s} for a, s in queries], self.panel)
        return {call["accession"]: call for call in result["calls"]}, result

    def test_nearest_reference_assignment_and_abstention_reasons(self):
        calls, result = self.calls(("Q1", mutate(BASE, 151, 9)), ("Q2", mutate(BASE, 15, 7)),
                                   ("Q3", "N" * 50), ("Q4", BASE[300:]), ("X1", BASE))
        self.assertEqual((calls["Q1"]["ribotype"], calls["Q1"]["reason"]), ("Alpha", "assigned"))
        self.assertGreater(calls["Q1"]["margin"], 0.1)
        self.assertEqual(calls["Q2"]["reason"], "below_min_score")
        self.assertEqual(calls["Q3"]["reason"], "no_valid_kmers")
        # Alpha and Beta are identical over the second half, so that region cannot separate them.
        self.assertEqual(calls["Q4"]["reason"], "low_margin")
        self.assertIsNone(calls["Q4"]["ribotype"])
        self.assertTrue(calls["X1"]["panel_excluded"])
        self.assertEqual(result["assigned"], {"Alpha": 2, "Beta": 0, "Gamma": 0})
        self.assertEqual(result["unassigned"], {"below_min_score": 1, "no_valid_kmers": 1, "low_margin": 1})

    def test_region_outside_a_ribotypes_references_is_a_gap(self):
        calls, _ = self.calls(("Q1", BASE[:250]))
        self.assertEqual(calls["Q1"]["uncovered_ribotypes"], ["Gamma"])
        self.assertEqual(calls["Q1"]["reason"], "panel_region_gap")

    def test_reference_is_scored_leave_one_out(self):
        calls, _ = self.calls(("R1", BASE), ("R5", self.panel["references"][4]["sequence"]))
        self.assertEqual(calls["R1"]["nearest_reference"], "R2")
        self.assertTrue(calls["R1"]["panel_reference"])
        self.assertLess(calls["R1"]["score"], 1.0)
        self.assertEqual(calls["R5"]["reason"], "panel_region_gap")

    def test_assignment_is_deterministic_and_bound_to_the_panel(self):
        queries = [{"accession": "Q1", "sequence": BASE.lower().replace("t", "u")}]
        first, second = ribotype.assign(queries, self.panel), ribotype.assign(deepcopy(queries), deepcopy(self.panel))
        self.assertEqual(first, second)
        self.assertEqual(first["calls"][0]["ribotype"], "Alpha")
        self.assertEqual(first["panel_digest"], digest(self.panel))
        self.assertNotIn("references", first["panel"])

    def test_invalid_panels_and_queries_are_rejected(self):
        for bad in (panel(k=4), panel(ribotypes=["Alpha"]), panel(thresholds={"min_score": 2}),
                    panel(excluded=[{"accession": "R1", "reason": "duplicate"}]),
                    panel(ribotypes=["Alpha", "Beta", "Gamma", "Delta"])):
            with self.assertRaises(ribotype.PanelError):
                ribotype.check_panel(bad)
        for sequence in ("", "ACGTX", "A" * (ribotype.MAX_QUERY_LENGTH + 1)):
            with self.assertRaises(ribotype.PanelError):
                ribotype.assign([{"accession": "Q", "sequence": sequence}], self.panel)
        with self.assertRaises(ribotype.PanelError):
            ribotype.load_panel("Alexandrium monilatum", "lsu")

    def test_shipped_panel_is_valid_and_recovers_its_labels(self):
        shipped = ribotype.load_panel(TAXON, "lsu")
        self.assertEqual(shipped["ribotypes"], ["American/Malaysian", "East Asian", "Mediterranean", "Philippine"])
        result = ribotype.assign([{"accession": item["accession"], "sequence": item["sequence"]}
                                  for item in shipped["references"]], shipped)
        for reference, call in zip(shipped["references"], result["calls"]):
            # The single Philippine reference has no other reference to cover it.
            if reference["ribotype"] == "Philippine":
                self.assertEqual(call["reason"], "panel_region_gap")
            else:
                self.assertEqual(call["ribotype"], reference["ribotype"], reference["accession"])


class MarkerTransportTests(unittest.TestCase):
    def setUp(self):
        self.builder = patch("sewall.genbank.build_opener").start()
        patch("sewall.ncbi.time.sleep").start()
        patch.dict(os.environ, {"NCBI_API_KEY": ""}).start()
        self.addCleanup(patch.stopall)

    def respond(self, *documents):
        queue = [item.encode() if isinstance(item, str) else json.dumps(item).encode() for item in documents]
        self.requests = []

        def opened(request, **kwargs):
            self.requests.append(request.full_url)
            return Response(queue.pop(0), request.full_url)
        self.builder.return_value.open.side_effect = opened

    def test_fetch_returns_sequences_with_qualifiers(self):
        one, two = random_sequence(900, 11), random_sequence(900, 12)
        self.respond(esearch(5, ["11", "12"]), esummary("11", "12"), fasta(("MN000011.1", one), ("MN000012.1", two)))
        result = fetch_markers(TAXON, "lsu")
        self.assertEqual((result["total"], result["returned"], result["truncated"]), (5, 2, True))
        self.assertEqual([item["sequence"] for item in result["records"]], [one, two])
        self.assertEqual(result["records"][0]["qualifiers"]["strain"], "CP11")
        self.assertEqual([item["utility"] for item in result["requests"]], ["esearch", "esummary", "efetch"])
        term = parse_qs(urlparse(self.requests[0]).query)["term"][0]
        self.assertIn("28S[Title]", term)
        self.assertTrue(term.endswith('NOT "tsa"[Properties]'))
        self.assertIn("rettype=fasta", self.requests[2])
        self.assertEqual(result["response_bytes"], sum(item["response_bytes"] for item in result["requests"]))

    def test_zero_hits_make_one_request(self):
        self.respond(esearch(0))
        result = fetch_markers("123456", "its")
        self.assertEqual((result["records"], len(result["requests"])), ([], 1))

    def test_unexpected_fasta_fails(self):
        sequence = random_sequence(900, 13)
        for text in (fasta(("MN000099.1", sequence)), fasta(("MN000011.1", sequence[:800])),
                     fasta(("MN000011.1", sequence), ("MN000011.1", sequence)), ">MN000011.1 x\nACGT<script>\n"):
            self.respond(esearch(1, ["11"]), esummary("11"), text)
            with self.assertRaises(InventoryError):
                fetch_markers(TAXON, "lsu")
        with self.assertRaises(ValueError):
            fetch_markers(TAXON, "rbcl")


def markers(taxon, marker):
    """A fetch result built from shipped panel references under new accessions."""
    shipped = {item["accession"]: item["sequence"] for item in ribotype.load_panel(TAXON, "lsu")["references"]}
    sources = [("AB295042.1", "Japan"), ("AB295048.1", "Malaysia: Sabah"), ("KC577587.1", "Spain")]
    records = [{"uid": str(index + 1), "accession": f"PX{index + 1:06d}.1",
                "source_url": f"https://www.ncbi.nlm.nih.gov/nuccore/PX{index + 1:06d}.1",
                "title": "LSU rRNA gene", "organism": taxon, "taxid": "2876467", "create_date": "2024/01/01",
                "qualifiers": {"country": country}, "trust": "untrusted_source_metadata",
                "length": len(shipped[accession]), "sequence": shipped[accession],
                "sequence_sha256": "0" * 64} for index, (accession, country) in enumerate(sources)]
    return {"kind": "public_marker_sequences", "mode": "live_public_records", "database": "nuccore", "taxon": taxon,
            "marker": marker, "organism_term": f'"{taxon}"[Organism:exp]', "total": 3, "returned": 3,
            "truncated": False, "classification": "title_keyword_heuristic", "records": records,
            "requests": [{"utility": "esearch"}, {"utility": "esummary"}, {"utility": "efetch", "attempts": 2}],
            "response_bytes": 9000, "retrieved_at": "2026-10-16T00:00:00+00:00", "limitations": ["Test."]}


def fetch(taxon=TAXON, marker="lsu"):
    return {"action": "fetch_markers", "taxon": taxon, "marker": marker, "reason": "Fetch LSU sequences"}


def assign(taxon=TAXON, marker="lsu"):
    return {"action": "assign_ribotype", "taxon": taxon, "marker": marker, "reason": "Assign ribotypes"}


class MarkerSkillTests(unittest.TestCase):
    def setUp(self):
        self.sources = Sources()
        self.fetched = []

    def run_agent(self, *outputs):
        def fake(taxon, marker):
            self.fetched.append((taxon, marker))
            return markers(taxon, marker)
        return run_agent("Which ribotypes of this dinoflagellate are in public LSU records?", Client(*outputs),
                         search_fn=self.sources.search, fetch_fn=self.sources.fetch, markers_fn=fake)

    def test_fetch_and_assign_record_nodes_policy_and_replay(self):
        manifest = self.run_agent(search("pubmed", "dinoflagellate ribotype"), fetch(), assign(),
                                  finish(["ncbi:pubmed:11"]), review(["ncbi:pubmed:11"]))
        self.assertEqual(manifest["status"], "completed")
        nodes = {node["id"]: node for node in manifest["graph"]["nodes"]}
        source = nodes[f"markers:nuccore:{TAXON}:lsu"]
        self.assertEqual((source["kind"], source["source_id"]), ("source", "nuccore_sequences"))
        verification = nodes[f"ribotype:{TAXON}:lsu"]
        self.assertEqual(verification["kind"], "verification")
        observation = manifest["actions"][2]["observation"]
        self.assertEqual([call["ribotype"] for call in observation["calls"]],
                         ["East Asian", "American/Malaysian", "Mediterranean"])
        self.assertNotIn("ACGT", json.dumps([action["observation"] for action in manifest["actions"]]))
        self.assertEqual(manifest["actions"][1]["observation"]["countries"], {"Japan": 1, "Malaysia": 1, "Spain": 1})
        policy = next(item for item in manifest["policies"] if item["source_id"] == "nuccore_sequences")
        self.assertEqual(policy["status"], "sequence_records")
        self.assertEqual(manifest["metrics"]["source_requests"], 2 + 4)
        self.assertEqual(self.fetched, [(TAXON, "lsu")])
        self.assertTrue(verify_agent_manifest(manifest)["valid"])

    def test_assignment_without_fetch_is_blocked(self):
        manifest = self.run_agent(assign(), finish(), review())
        self.assertEqual(manifest["actions"][0]["status"], "blocked")
        self.assertEqual(self.fetched, [])

    def test_taxon_without_panel_is_rejected_before_any_work(self):
        manifest = self.run_agent(assign("Alexandrium monilatum"), review())
        self.assertEqual(manifest["stop_reason"], "invalid_model_action")

    def test_repeated_fetch_is_blocked_without_a_request(self):
        manifest = self.run_agent(fetch(), fetch(), finish(), review())
        self.assertEqual(len(self.fetched), 1)
        self.assertEqual(manifest["actions"][1]["status"], "blocked")

    def test_resealed_assignment_or_panel_edits_fail_replay(self):
        manifest = self.run_agent(fetch(), assign(), finish(), review())
        target = f"ribotype:{TAXON}:lsu"

        def forge(edit):
            forged = deepcopy(manifest)
            for node in forged["graph"]["nodes"]:
                if node["id"] == target:
                    edit(node["details"])
            for event in forged["events"]:
                if event["type"] == "node_added" and event["details"]["id"] == target:
                    edit(event["details"]["details"])
            return reseal(forged)

        def relabel(details):
            details["assignment"]["calls"][0]["ribotype"] = "Philippine"
            details["assignment_digest"] = digest(details["assignment"])

        def shift_threshold(details):
            details["panel"]["thresholds"]["min_score"] = 0.5
            details["assignment"]["panel_digest"] = digest(details["panel"])
            details["assignment_digest"] = digest(details["assignment"])

        for edit in (relabel, shift_threshold):
            self.assertFalse(verify_agent_manifest(forge(edit))["valid"], edit.__name__)

    @unittest.skipIf(Draft202012Validator is None, "Optional jsonschema dependency is not installed")
    def test_marker_manifest_matches_schema(self):
        schema = json.loads((ROOT / "schemas/agent_manifest.schema.json").read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            self.run_agent(fetch(), assign(), finish(), review()))


if __name__ == "__main__":
    unittest.main()

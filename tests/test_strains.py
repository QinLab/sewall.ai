"""Growth strain linkage tests. These tests never contact NCBI or a model. Run them on a Slurm CPU node."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    Draft202012Validator = None

from sewall import strains
from sewall.agent import run_agent, verify_agent_manifest
from sewall.graph import digest
from test_agent import Client, Sources, finish, reseal, review, search
from test_markers import TAXON, assign, fetch, markers


ROOT = Path(__file__).resolve().parents[1]


def entry(name, aliases=(), label=None, system=None):
    return {"strain": name, "aliases": list(aliases), "origin": "Somewhere", "published_label": label,
            "label_system": system, "responses": [{"variable": "temperature", "tested": "15-30 C", "result": "optimum 25 C"}],
            "citation": {"authors": "Doe J", "year": 2020, "title": "Growth", "journal": "Test J", "doi": None, "pmid": "1"},
            "verification": "abstract"}


def table(*entries):
    return {"table_id": "test.strains", "version": "0.0.1", "taxon": TAXON, "marker": "lsu", "strains": list(entries),
            "label_note": "Synthetic.", "limitations": ["Synthetic table."]}


def assignment(*calls):
    """A minimal ribotype.assign result over (accession, ribotype) pairs."""
    return {"panel": {"ribotypes": ["Alpha", "Beta"]}, "panel_digest": "p" * 64,
            "calls": [{"accession": accession, "ribotype": label, "score": 0.99 if label else 0.5,
                       "reason": "assigned" if label else "below_min_score"} for accession, label in calls]}


def records(*pairs):
    return {"records": [{"accession": accession, "qualifiers": qualifiers} for accession, qualifiers in pairs]}


class LinkTests(unittest.TestCase):
    def link(self, *entries):
        result = strains.link(
            assignment(("A1", "Alpha"), ("A2", "Alpha"), ("B1", "Beta"), ("U1", None), ("C1", "Alpha"), ("C2", "Beta")),
            records(("A1", {"strain": "CPPV-1"}), ("A2", {"isolate": "cppv 1"}), ("B1", {"strain": "LM5D342"}),
                    ("U1", {"isolate": "HN-11"}), ("C1", {"strain": "XY-9"}), ("C2", {"isolate": "XY9"})),
            table(*entries))
        return {item["strain"]: item for item in result["links"]}, result

    def test_names_match_after_normalization_and_report_status(self):
        links, result = self.link(entry("CPPV1", label="Alpha", system="Iwataki 2008 ribotype"),
                                  entry("LM-5D-342", label="Alpha", system="Iwataki 2008 ribotype"),
                                  entry("HN11"), entry("XY-9", label="Group III", system="Group (Test 2025)"),
                                  entry("Missing-7"))
        self.assertEqual((links["CPPV1"]["status"], links["CPPV1"]["ribotype"]), ("called", "Alpha"))
        self.assertEqual(links["CPPV1"]["accessions"], ["A1", "A2"])
        self.assertEqual(links["CPPV1"]["label_check"], "agrees")
        self.assertEqual(links["LM-5D-342"]["label_check"], "disagrees")
        self.assertEqual((links["HN11"]["status"], links["HN11"]["label_check"]), ("unassigned", "no_published_label"))
        self.assertEqual((links["XY-9"]["status"], links["XY-9"]["label_check"]),
                         ("conflicting_calls", "different_label_system"))
        self.assertEqual((links["Missing-7"]["status"], links["Missing-7"]["accessions"]), ("no_sequence_record", []))
        self.assertEqual(result["strains_per_ribotype"], {"Alpha": ["CPPV1"], "Beta": ["LM-5D-342"]})
        self.assertEqual(result["statuses"], {"called": 2, "conflicting_calls": 1, "no_sequence_record": 1, "unassigned": 1})

    def test_aliases_match_and_a_label_needs_a_call_to_compare(self):
        links, result = self.link(entry("Lab strain", aliases=["HN-11"], label="Beta", system="Iwataki 2008 ribotype"))
        self.assertEqual(links["Lab strain"]["accessions"], ["U1"])
        self.assertEqual(links["Lab strain"]["label_check"], "not_comparable")
        self.assertEqual(result["ribotypes_without_growth_strains"], ["Alpha", "Beta"])

    def test_linkage_is_deterministic_and_bound_to_the_table(self):
        first, second = self.link(entry("CPPV1"))[1], self.link(entry("CPPV1"))[1]
        self.assertEqual(first, second)
        self.assertEqual(first["table_digest"], digest(table(entry("CPPV1"))))

    def test_invalid_tables_are_rejected(self):
        bad = [table(), table(entry("CP")), table(entry("CPPV1"), entry("cppv-1")),
               table(entry("CPPV1", label="Alpha")), table(dict(entry("CPPV1"), verification="memory")),
               table(dict(entry("CPPV1"), responses=[])), table(dict(entry("CPPV1"), extra=1))]
        for value in bad:
            with self.assertRaises(strains.StrainTableError):
                strains.check_table(value)
        with self.assertRaises(strains.StrainTableError):
            strains.load_table("Alexandrium monilatum", "lsu")
        with self.assertRaises(strains.StrainTableError):
            strains.link(assignment(("A1", "Alpha")), records(("A2", {})), table(entry("CPPV1")))

    def test_shipped_table_is_valid(self):
        shipped = strains.load_table(TAXON, "lsu")
        self.assertTrue(all(item["verification"] in ("abstract", "full_text") for item in shipped["strains"]))


def strain_markers(taxon, marker):
    """The marker fixture with the first shipped strain name on its first record."""
    result = markers(taxon, marker)
    result["records"][0]["qualifiers"]["strain"] = strains.load_table(TAXON, "lsu")["strains"][0]["strain"]
    return result


def link(taxon=TAXON, marker="lsu"):
    return {"action": "link_strains", "taxon": taxon, "marker": marker, "reason": "Pair strains with ribotypes"}


class StrainSkillTests(unittest.TestCase):
    def setUp(self):
        self.sources = Sources()

    def run_agent(self, *outputs):
        return run_agent("Which growth-studied strains belong to which ribotype?", Client(*outputs),
                         search_fn=self.sources.search, fetch_fn=self.sources.fetch, markers_fn=strain_markers)

    def test_link_records_a_node_and_replays(self):
        manifest = self.run_agent(search("pubmed", "dinoflagellate growth"), fetch(), assign(), link(),
                                  finish(["ncbi:pubmed:11"]), review(["ncbi:pubmed:11"]))
        self.assertEqual(manifest["status"], "completed")
        nodes = {node["id"]: node for node in manifest["graph"]["nodes"]}
        self.assertEqual(nodes[f"strains:{TAXON}:lsu"]["kind"], "verification")
        observation = manifest["actions"][3]["observation"]
        first = strains.load_table(TAXON, "lsu")["strains"][0]["strain"]
        linked = next(item for item in observation["links"] if item["strain"] == first)
        self.assertEqual((linked["status"], linked["accessions"], linked["ribotype"]),
                         ("called", ["PX000001.1"], "East Asian"))
        self.assertTrue(verify_agent_manifest(manifest)["valid"])

    def test_link_without_assignment_is_blocked(self):
        manifest = self.run_agent(fetch(), link(), finish(), review())
        self.assertEqual(manifest["actions"][1]["status"], "blocked")

    def test_taxon_without_table_is_rejected(self):
        manifest = self.run_agent(link("Alexandrium monilatum"), review())
        self.assertEqual(manifest["stop_reason"], "invalid_model_action")

    def test_resealed_link_or_table_edits_fail_replay(self):
        manifest = self.run_agent(fetch(), assign(), link(), finish(), review())
        target = f"strains:{TAXON}:lsu"

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
            details["strain_links"]["links"][0]["ribotype"] = "Philippine"
            details["strain_links_digest"] = digest(details["strain_links"])

        def relabel_table(details):
            # A table edit that changes the label check without recomputing the links.
            details["strain_table"]["strains"][0].update(published_label="Philippine", label_system="Iwataki 2008 ribotype")
            details["strain_links"]["table_digest"] = digest(details["strain_table"])
            details["strain_links_digest"] = digest(details["strain_links"])

        for edit in (relabel, relabel_table):
            self.assertFalse(verify_agent_manifest(forge(edit))["valid"], edit.__name__)

    @unittest.skipIf(Draft202012Validator is None, "Optional jsonschema dependency is not installed")
    def test_strain_manifest_matches_schema(self):
        schema = json.loads((ROOT / "schemas/agent_manifest.schema.json").read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            self.run_agent(fetch(), assign(), link(), finish(), review()))


if __name__ == "__main__":
    unittest.main()

"""GenBank inventory Skill and scripted planner tests with mocked transport.

These tests never contact NCBI or a model. Run them on a Slurm CPU node.
"""

import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse

try:
    from jsonschema import Draft202012Validator, FormatChecker
except ImportError:
    Draft202012Validator = None

from sewall.agent import run_agent, verify_agent_manifest
from sewall.genbank import InventoryError, organism_term, taxon_inventory
from sewall.scripted import ScriptError, ScriptedClient, load_script
from test_agent import Client, Sources, finish, review, search
from test_evidence import Response


ROOT = Path(__file__).resolve().parents[1]


def esearch(count, ids=(), errors=None):
    result = {"count": str(count), "retmax": str(len(ids)), "retstart": "0", "idlist": list(ids)}
    if errors:
        result["errorlist"] = errors
    return {"header": {"type": "esearch"}, "esearchresult": result}


def esummary(*uids):
    records = {uid: {"uid": uid, "accessionversion": f"MN{uid.zfill(6)}.1", "title": f"Strain {uid} 28S rRNA gene",
                     "organism": "Margalefidinium polykrikoides", "taxid": 2876467, "slen": 900, "moltype": "dna",
                     "createdate": "2020/01/02", "subtype": "strain|country|collection_date|note",
                     "subname": f"CP{uid}|USA: Virginia, Lafayette River|2019-08|ignored"} for uid in uids}
    return {"header": {"type": "esummary"}, "result": {"uids": list(uids), **records}}


def inventory(taxon, sample=3):
    return {"kind": "public_sequence_metadata_inventory", "database": "nuccore", "taxon": taxon,
            "organism_term": organism_term(taxon), "total": 12, "total_excluding_tsa": 10, "tsa_records": 2,
            "marker_counts": {"lsu": 7, "ssu": 2, "its": 3, "coi": 0}, "classification": "title_keyword_heuristic",
            "sample": [{"accession": "MN000001.1", "title": "Strain 1 28S rRNA gene", "organism": taxon,
                        "length": 900, "qualifiers": {"strain": "CP1"}}][:sample],
            "requests": [{"utility": "esearch"}] * 6 + [{"utility": "esummary"}], "response_bytes": 700}


def inventory_action(taxon="Margalefidinium polykrikoides"):
    return {"action": "taxon_inventory", "taxon": taxon, "reason": "Count public sequence records"}


class InventoryTransportTests(unittest.TestCase):
    def setUp(self):
        self.builder = patch("sewall.genbank.build_opener").start()
        self.sleep = patch("sewall.ncbi.time.sleep").start()
        self.addCleanup(patch.stopall)

    def respond(self, *documents, redirect=None):
        queue = [item if isinstance(item, HTTPError) else json.dumps(item).encode() for item in documents]
        self.requests = []

        def opened(request, **kwargs):
            self.requests.append(request.full_url)
            item = queue.pop(0)
            if isinstance(item, HTTPError):
                raise item
            return Response(item, redirect or request.full_url)
        self.builder.return_value.open.side_effect = opened

    def test_organism_terms_accept_ids_and_names_only(self):
        self.assertEqual(organism_term("2876467"), "txid2876467[Organism:exp]")
        self.assertEqual(organism_term("Alexandrium monilatum"), '"Alexandrium monilatum"[Organism:exp]')
        for bad in ("", "0", "alexandrium", 'Alexandrium" OR 1', "Alexandrium monilatum[Title]", "A b c d e"):
            with self.assertRaises(ValueError):
                organism_term(bad)

    def test_inventory_counts_markers_and_parses_allowed_qualifiers(self):
        self.respond(esearch(12), esearch(10, ["11", "12"]), esearch(7), esearch(2), esearch(3), esearch(0),
                     esummary("11", "12"))
        result = taxon_inventory("Margalefidinium polykrikoides", sample=2)
        self.assertEqual((result["total"], result["total_excluding_tsa"], result["tsa_records"]), (12, 10, 2))
        self.assertEqual(result["marker_counts"], {"lsu": 7, "ssu": 2, "its": 3, "coi": 0})
        self.assertEqual(len(result["requests"]), 7)
        self.assertEqual(result["sample"][0]["accession"], "MN000011.1")
        self.assertEqual(result["sample"][0]["qualifiers"],
                         {"strain": "CP11", "country": "USA: Virginia, Lafayette River", "collection_date": "2019-08"})
        terms = [parse_qs(urlparse(url).query)["term"][0] for url in self.requests[:6]]
        self.assertTrue(all(term.startswith('"Margalefidinium polykrikoides"[Organism:exp]') for term in terms))
        self.assertTrue(terms[1].endswith('NOT "tsa"[Properties]'))
        self.assertTrue(all("db=nuccore" in url for url in self.requests))
        self.assertNotIn("efetch", " ".join(self.requests))

    def test_zero_hits_make_one_request(self):
        self.respond(esearch(0))
        result = taxon_inventory("123456")
        self.assertEqual((result["total"], result["sample"], len(result["requests"])), (0, [], 1))
        self.assertEqual(set(result["marker_counts"].values()), {0})

    def test_rate_limit_gets_one_bounded_retry(self):
        limited = HTTPError("https://eutils.ncbi.nlm.nih.gov/", 429, "Too Many Requests", {"Retry-After": "60"}, None)
        self.respond(limited, esearch(0))
        result = taxon_inventory("123456")
        self.assertEqual(result["requests"][0]["attempts"], 2)
        self.assertIn(3.0, [call.args[0] for call in self.sleep.call_args_list])
        self.respond(limited, limited)
        with self.assertRaisesRegex(InventoryError, "HTTP 429 after 2"):
            taxon_inventory("123456")
        self.respond(HTTPError("https://eutils.ncbi.nlm.nih.gov/", 500, "Error", {}, None))
        with self.assertRaisesRegex(InventoryError, "HTTP 500 after 1"):
            taxon_inventory("123456")

    def test_inconsistent_tsa_count_fails(self):
        self.respond(esearch(5), esearch(9))
        with self.assertRaisesRegex(InventoryError, "inconsistent"):
            taxon_inventory("123456")

    def test_api_key_is_sent_but_not_recorded(self):
        self.respond(esearch(0))
        with patch.dict(os.environ, {"NCBI_API_KEY": "secret-key"}):
            result = taxon_inventory("123456")
        self.assertIn("api_key=secret-key", self.requests[0])
        self.assertNotIn("secret", json.dumps(result))

    def test_unrecognized_term_redirect_and_oversized_ids_fail(self):
        self.respond(esearch(0, errors={"phrasesnotfound": ["Nonexistent organism"]}))
        with self.assertRaisesRegex(InventoryError, "did not recognize"):
            taxon_inventory("Nonexistent organism")
        self.respond(esearch(0), redirect="https://example.org/")
        with self.assertRaises(InventoryError):
            taxon_inventory("123456")
        self.respond(esearch(9), esearch(9, ["1", "2", "3", "4"]))
        with self.assertRaises(InventoryError):
            taxon_inventory("123456", sample=3)


class InventorySkillTests(unittest.TestCase):
    def setUp(self):
        self.sources = Sources()
        self.inventories = []

    def run_agent(self, planner, **kwargs):
        def fake(taxon, sample):
            self.inventories.append((taxon, sample))
            return inventory(taxon, sample)
        return run_agent("Which public sequence records exist for this dinoflagellate?", planner,
                         search_fn=self.sources.search, fetch_fn=self.sources.fetch, inventory_fn=fake, **kwargs)

    def test_inventory_adds_source_node_policy_and_replays(self):
        planner = Client(search("pubmed", "dinoflagellate bloom"), inventory_action(), finish(["ncbi:pubmed:11"]),
                         review(["ncbi:pubmed:11"]))
        manifest = self.run_agent(planner)
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(self.inventories, [("Margalefidinium polykrikoides", 3)])
        node = next(item for item in manifest["graph"]["nodes"] if item["id"].startswith("inventory:"))
        self.assertEqual((node["kind"], node["source_id"]), ("source", "nuccore"))
        self.assertIn("nuccore", manifest["plan"]["selected_sources"])
        self.assertEqual(manifest["actions"][1]["observation"]["marker_counts"]["lsu"], 7)
        self.assertEqual(manifest["metrics"]["source_requests"], 2 + 7)
        self.assertEqual(manifest["actions"][1]["observation"]["tsa_records"], 2)
        self.assertIn("taxon_inventory", manifest["plan"]["allowed_actions"])
        self.assertTrue(verify_agent_manifest(manifest)["valid"])

    def test_repeated_inventory_is_blocked_without_a_request(self):
        planner = Client(inventory_action(), inventory_action(), finish(), review())
        manifest = self.run_agent(planner)
        self.assertEqual(len(self.inventories), 1)
        self.assertEqual(manifest["actions"][1]["status"], "blocked")

    def test_free_text_taxon_is_rejected_before_any_request(self):
        planner = Client(inventory_action("dinoflagellates in Chesapeake Bay"), review())
        manifest = self.run_agent(planner)
        self.assertEqual(manifest["stop_reason"], "invalid_model_action")
        self.assertEqual(self.inventories, [])

    @unittest.skipIf(Draft202012Validator is None, "Optional jsonschema dependency is not installed")
    def test_inventory_manifest_matches_schema(self):
        schema = json.loads((ROOT / "schemas/agent_manifest.schema.json").read_text())
        planner = Client(search("pubmed", "dinoflagellate bloom"), inventory_action(), finish(["ncbi:pubmed:11"]),
                         review(["ncbi:pubmed:11"]))
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(self.run_agent(planner))


class ScriptedPlannerTests(unittest.TestCase):
    def setUp(self):
        self.sources = Sources()

    def run_script(self, steps, **kwargs):
        client = ScriptedClient({"name": "test-script", "steps": steps, "gaps": ["Example gap"]})
        return run_agent("Scripted public metadata question", client, search_fn=self.sources.search,
                         fetch_fn=self.sources.fetch, inventory_fn=inventory, **kwargs)

    def test_script_runs_end_to_end_and_replays(self):
        manifest = self.run_script([search("gds", "coastal plants"),
                                    {"action": "citations", "record_id": "$record:gds:0", "reason": "Follow listed papers"},
                                    inventory_action("Zostera marina")])
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual([item["status"] for item in manifest["actions"]], ["completed"] * 3)
        self.assertEqual(manifest["actions"][1]["action"]["record_id"], "ncbi:gds:1")
        self.assertTrue(any(edge["relation"] == "cites" for edge in manifest["graph"]["edges"]))
        self.assertEqual(manifest["model_calls"][-1]["trace"]["provider"], "scripted")
        self.assertIn("GenBank lists 12", manifest["metadata_summary"]["summary"])
        self.assertIn("2 are transcriptome assembly contigs", manifest["metadata_summary"]["summary"])
        planned = [event["details"] for event in manifest["events"] if event["type"] == "action_planned"]
        self.assertEqual(planned[-1]["action"], "finish")
        self.assertIn("Example gap", planned[-1]["gaps"])
        self.assertTrue(verify_agent_manifest(manifest)["valid"])

    def test_host_written_plan_is_labeled_in_the_gaps(self):
        client = ScriptedClient({"name": "mcp-host-plan", "origin": "mcp_host", "steps": [search("gds", "coastal plants")]})
        manifest = run_agent("Scripted public metadata question", client, search_fn=self.sources.search,
                             fetch_fn=self.sources.fetch, inventory_fn=inventory)
        gaps = [event["details"] for event in manifest["events"] if event["type"] == "action_planned"][-1]["gaps"]
        self.assertTrue(any("MCP host model" in gap for gap in gaps))
        self.assertFalse(any("not chosen by a model" in gap for gap in gaps))
        self.assertTrue(verify_agent_manifest(manifest)["valid"])

    def test_unresolved_placeholder_step_is_skipped(self):
        manifest = self.run_script([{"action": "citations", "record_id": "$record:gds:0", "reason": "No GDS yet"},
                                    search("pubmed", "coastal plants")])
        self.assertEqual([item["action"]["action"] for item in manifest["actions"]], ["search"])

    def test_scripted_actions_get_the_same_guards(self):
        manifest = self.run_script([{"action": "search", "database": "snp", "query": "x", "reason": "Not allowed"}])
        self.assertEqual(manifest["stop_reason"], "invalid_model_action")
        self.assertEqual(self.sources.searches, [])

    def test_action_budget_ends_script_with_finish(self):
        manifest = self.run_script([search("gds", "a"), search("pubmed", "b"), search("bioproject", "c")], max_actions=2)
        self.assertEqual(len(manifest["actions"]), 2)
        self.assertEqual(manifest["stop_reason"], "planner_finished")

    def test_script_validation(self):
        for bad in ({}, {"name": "Bad Name", "steps": [search()]}, {"name": "ok", "steps": []},
                    {"name": "ok", "steps": [{"action": "finish"}]}, {"name": "ok", "steps": [{"action": "search", "limit": 3}]},
                    {"name": "ok", "steps": [search()], "extra": 1}, {"name": "ok", "steps": [search()], "origin": "model"}):
            with self.assertRaises(ScriptError):
                load_script(bad)

    def test_bundled_demo_script_is_valid_and_its_steps_pass_the_guards(self):
        script = load_script(json.loads((ROOT / "configs/scripted-bloom-demo.json").read_text()))
        sources = Sources()
        manifest = run_agent("What public evidence exists on Margalefidinium polykrikoides blooms?", ScriptedClient(script),
                             search_fn=sources.search, fetch_fn=sources.fetch, inventory_fn=inventory)
        self.assertEqual([item["status"] for item in manifest["actions"]], ["completed"] * len(script["steps"]))
        self.assertTrue(verify_agent_manifest(manifest)["valid"])


if __name__ == "__main__":
    unittest.main()

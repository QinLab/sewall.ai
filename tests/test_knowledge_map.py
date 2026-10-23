"""Catalog semantics, fail-closed requirements, replay and safe standalone export."""
import copy
import json
from pathlib import Path
import unittest

from sewall.knowledge_map import build_graph, canonical, digest, render_html, render_svg, validate_graph

ROOT = Path(__file__).resolve().parents[1]


class KnowledgeMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads((ROOT / "docs/knowledge-map-source-registry-2026-10-23.json").read_text())

    def setUp(self):
        self.graph = build_graph(self.registry)

    def rehash(self, graph):
        graph["graph_sha256"] = digest({k: v for k, v in graph.items() if k != "graph_sha256"})

    def test_rebuild_and_replay_are_identical(self):
        self.assertEqual(canonical(self.graph), canonical(build_graph(self.registry)))
        self.assertTrue(validate_graph(json.loads(json.dumps(self.graph)), self.registry))

    def test_catalog_inventory_and_federated_service_identity(self):
        kinds = self.graph["statistics"]["node_kinds"]
        self.assertEqual(kinds, {"Organization": 11, "Service": 20, "Product": 7, "Environment": 1, "DataType": 49, "PolicyReview": 28})
        edges = self.graph["edges"]
        self.assertEqual(sum(e["predicate"] == "published_by" and e["object"] == "org:ncbi" for e in edges), 7)
        self.assertEqual(sum(e["predicate"] == "published_by" and e["object"] == "org:noaa" for e in edges), 5)

    def test_products_and_environment_have_separate_reviews(self):
        reviews = [n for n in self.graph["nodes"] if n["kind"] == "PolicyReview"]
        self.assertEqual(len(reviews), 28)
        self.assertTrue(all(n["attributes"]["default_decision"] == "needs_review" for n in reviews))
        self.assertTrue(all(set(n["attributes"]["field_states"].values()) == {"unknown"} for n in reviews))
        self.assertEqual(self.graph["authorization_grant_count"], 0)
        self.assertEqual(self.graph["scientific_join_count"], 0)

    def test_registry_grant_is_rejected(self):
        r = copy.deepcopy(self.registry)
        r["services"][0]["authorization_granted"] = True
        with self.assertRaises(ValueError):
            build_graph(r)

    def test_registry_allow_default_is_rejected(self):
        r = copy.deepcopy(self.registry)
        r["services"][0]["default_data_action"] = "allow"
        with self.assertRaises(ValueError):
            build_graph(r)

    def test_signed_urls_and_credentials_are_rejected(self):
        for update in [{"token": "do-not-export"}, {"documentation_urls": ["https://example.org/file?X-Amz-Signature=redacted"]}]:
            r = copy.deepcopy(self.registry)
            r["services"][0].update(update)
            with self.assertRaises(ValueError):
                build_graph(r)

    def test_private_cloud_project_is_not_exported(self):
        r = copy.deepcopy(self.registry)
        r["environments"][0]["project_ref"] = "private-project"
        with self.assertRaises(ValueError):
            build_graph(r)

    def test_tampering_without_rehash_fails(self):
        self.graph["nodes"][0]["label"] = "changed"
        with self.assertRaisesRegex(ValueError, "digest"):
            validate_graph(self.graph)

    def test_missing_endpoint_fails_even_after_rehash(self):
        self.graph["edges"][0]["object"] = "not-present"
        self.rehash(self.graph)
        with self.assertRaisesRegex(ValueError, "endpoint"):
            validate_graph(self.graph)

    def test_scientific_edge_cannot_be_inserted(self):
        self.graph["edges"][0]["predicate"] = "same_sample_as"
        self.rehash(self.graph)
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            validate_graph(self.graph)

    def test_policy_permission_cannot_be_inserted(self):
        n = next(n for n in self.graph["nodes"] if n["kind"] == "PolicyReview")
        n["attributes"]["field_states"]["egress"] = "permitted"
        self.rehash(self.graph)
        with self.assertRaisesRegex(ValueError, "unknown"):
            validate_graph(self.graph)

    def test_missing_review_is_rejected(self):
        self.graph["edges"] = [e for e in self.graph["edges"] if not (e["subject"] == "service:neon" and e["predicate"] == "requires_policy_review")]
        self.rehash(self.graph)
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_graph(self.graph)

    def test_duplicate_nodes_are_rejected(self):
        self.graph["nodes"].append(copy.deepcopy(self.graph["nodes"][0]))
        self.rehash(self.graph)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            validate_graph(self.graph)

    def test_wrong_registry_replay_is_rejected(self):
        r = copy.deepcopy(self.registry)
        r["services"][0]["label"] = "a revised source"
        with self.assertRaisesRegex(ValueError, "replay"):
            validate_graph(self.graph, r)

    def test_html_payload_is_safe_and_self_contained(self):
        g = copy.deepcopy(self.graph)
        g["nodes"][0]["label"] = '</script><script>alert("x")</script>'
        html = render_html(g)
        self.assertNotIn(g["nodes"][0]["label"], html)
        self.assertIn('connect-src \'none\'', html)
        self.assertIn('\\u003c/script>', html)
        self.assertNotIn('src="https://', html)
        self.assertIn('Download graph JSON', html)

    def test_figure_uses_actual_graph_counts(self):
        from xml.etree import ElementTree
        svg = render_svg(self.graph)
        ElementTree.fromstring(svg)
        self.assertIn(str(self.graph["statistics"]["nodes"]) + " typed nodes", svg)
        self.assertIn("No validated scientific joins or access grants", svg)


if __name__ == "__main__":
    unittest.main()

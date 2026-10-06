"""Offline FS03 controls. No source requests or model calls."""
from copy import deepcopy
import unittest

from sewall.corroboration import TARGETS, assess_lineage, run_case
from sewall.evidence import _normalize
from sewall.safety import _digest


def fixtures():
    geo = [_normalize("gds", uid, {"uid": uid, "accession": accession,
           "title": "Synthetic fixture", "pubmedids": [26814964]})
           for uid, accession in TARGETS.items()]
    pm = [_normalize("pubmed", "26814964", {"uid": "26814964", "title": "Synthetic publication"})]
    return geo, pm


class CorroborationTests(unittest.TestCase):
    def test_counts_not_independence(self):
        result = assess_lineage(*fixtures())
        self.assertEqual(result["citation_edge_count"], 2)
        self.assertEqual(result["distinct_publication_count"], 1)
        self.assertEqual(result["sample_independence"], "unknown")
        self.assertIsNone(result["independent_replications"])

    def test_clean_and_new_corrected_run(self):
        for case in ("clean", "corrected"):
            result = run_case(*fixtures(), case)
            self.assertEqual(result["manifest"]["state"], "COMPLETED")
            self.assertEqual(result["manifest"]["claims"], [])
            self.assertIn("release", result["manifest"]["dispatch_history"])

    def test_claim_faults_quarantine_and_block_descendant(self):
        for case in ("inflated_publications", "unsupported_independence"):
            result = run_case(*fixtures(), case)
            manifest = result["manifest"]
            self.assertEqual(manifest["state"], "SAFE_STOPPED")
            states = {t["id"]: t["status"] for t in manifest["tasks"]}
            self.assertEqual(states["assessment"], "QUARANTINED")
            self.assertEqual(states["release"], "BLOCKED")
            self.assertEqual(states["source"], "VERIFIED")
            self.assertNotIn("release", manifest["dispatch_history"])
            self.assertEqual(manifest["provisional_summaries"], [])

    def test_missing_publication_stops_before_assessment(self):
        manifest = run_case(*fixtures(), "missing_publication")["manifest"]
        self.assertEqual(manifest["state"], "SAFE_STOPPED")
        self.assertNotIn("assessment", manifest["dispatch_history"])

    def test_source_inputs_unchanged(self):
        geo, pm = fixtures()
        before = deepcopy((geo, pm))
        run_case(geo, pm, "missing_publication")
        self.assertEqual((geo, pm), before)

    def test_missing_or_duplicate_publication_rejected(self):
        geo, pm = fixtures()
        for records in ([], pm + pm):
            with self.assertRaises(ValueError):
                assess_lineage(geo, records)

    def test_tampered_fields_rejected(self):
        geo, pm = fixtures()
        geo[0]["allowed_metadata"]["pubmedids[0]"] = 99
        with self.assertRaises(ValueError):
            assess_lineage(geo, pm)

    def test_unsupported_edge_rejected_even_with_field_digest(self):
        geo, pm = fixtures()
        geo[0]["study_links"][0]["uid"] = "99"
        with self.assertRaises(ValueError):
            assess_lineage(geo, pm)

    def test_truncation_rejected(self):
        geo, pm = fixtures()
        geo[0]["links_omitted"] = 1
        with self.assertRaises(ValueError):
            assess_lineage(geo, pm)

    def test_unexpected_accession_rejected(self):
        geo, pm = fixtures()
        geo[0]["accession"] = "GSE123"
        with self.assertRaises(ValueError):
            assess_lineage(geo, pm)

    def test_changed_benchmark_requires_review(self):
        geo, pm = fixtures()
        geo[0] = _normalize("gds", geo[0]["uid"], {"uid": geo[0]["uid"],
            "accession": geo[0]["accession"], "pubmedids": [99]})
        pm.append(_normalize("pubmed", "99", {"uid": "99", "title": "Other"}))
        self.assertEqual(assess_lineage(geo, pm)["distinct_publication_count"], 2)
        with self.assertRaises(ValueError):
            run_case(geo, pm, "clean")

    def test_omitted_explicit_link_rejected(self):
        geo, pm = fixtures()
        geo[0]["allowed_metadata"]["pubmedids[1]"] = 99
        geo[0]["allowed_metadata_sha256"] = _digest(geo[0]["allowed_metadata"])
        with self.assertRaises(ValueError):
            assess_lineage(geo, pm)


if __name__ == "__main__":
    unittest.main()

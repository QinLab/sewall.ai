"""Static gallery build tests. No model or source is contacted.

Run on a Slurm CPU node with the rest of the suite.
"""

import json
from pathlib import Path
import tempfile
import unittest

from sewall.gallery import GalleryError, build_gallery, scan
from sewall.graph import digest, run_research
from sewall.safety import run_safety_demo


ROOT = Path(__file__).resolve().parents[1]


class GalleryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "runs").mkdir()

    def write(self, name, manifest):
        (self.root / "runs" / name).write_text(json.dumps(manifest), encoding="utf-8")

    def catalog(self, runs, **extra):
        path = self.root / "catalog.json"
        path.write_text(json.dumps({"title": "Test <gallery>", "runs": runs, **extra}), encoding="utf-8")
        return path

    def test_builds_verified_runs_with_rerendered_reports(self):
        self.write("fixture.json", run_research("Which <b>coastal</b> records exist?"))
        self.write("safety.json", run_safety_demo("critical_critique"))
        catalog = self.catalog([{"slug": "fixture", "title": "Fixture <run>", "manifest": "runs/fixture.json", "note": "A & B"},
                                {"slug": "safety", "title": "Safety", "manifest": "runs/safety.json"}])
        result = build_gallery(catalog, self.root / "site")
        self.assertEqual(result["slugs"], ["fixture", "safety"])
        site = self.root / "site"
        for name in ("index.html", "gallery.json", ".nojekyll", "fixture/report.html", "fixture/manifest.json", "safety/report.html"):
            self.assertTrue((site / name).is_file(), name)
        self.assertEqual((site / "fixture/manifest.json").read_bytes(), (self.root / "runs/fixture.json").read_bytes())
        index = (site / "index.html").read_text()
        self.assertNotIn("<script", index)
        self.assertIn("Fixture &lt;run&gt;", index)
        self.assertIn("Which &lt;b&gt;coastal&lt;/b&gt; records exist?", index)
        self.assertIn("A &amp; B", index)
        summary = json.loads((site / "gallery.json").read_text())
        self.assertEqual([item["status"] for item in summary["runs"]], ["completed", "safe_stopped"])
        self.assertIn("Stopped, not successful", index)

    def test_tampered_manifest_is_refused_and_nothing_is_written(self):
        manifest = run_research("Which coastal records exist?")
        manifest["question"] = "Changed after recording"
        self.write("bad.json", manifest)
        with self.assertRaisesRegex(GalleryError, "verification failed"):
            build_gallery(self.catalog([{"slug": "bad", "title": "Bad", "manifest": "runs/bad.json"}]), self.root / "site")
        self.assertFalse((self.root / "site").exists())

    def test_sensitive_strings_are_refused_even_when_resealed(self):
        manifest = run_research("Notes in /home/someone/data with api_key=abc")
        self.write("leak.json", manifest)
        with self.assertRaisesRegex(GalleryError, "local file path, an API key parameter"):
            build_gallery(self.catalog([{"slug": "leak", "title": "Leak", "manifest": "runs/leak.json"}]), self.root / "site")
        self.assertEqual(scan(json.dumps({"context": {"models": {"planner": {"project": "my-gcp-project"}}}})), ["a cloud project ID"])
        self.assertEqual(scan(json.dumps({"context": {"models": {"planner": None}}})), [])

    def test_catalog_paths_slugs_and_output_are_checked(self):
        self.write("ok.json", run_research("Which coastal records exist?"))
        outside = self.root.parent / "outside.json"
        for runs in ([{"slug": "Bad Slug", "title": "x", "manifest": "runs/ok.json"}],
                     [{"slug": "a", "title": "x", "manifest": "runs/ok.json"}, {"slug": "a", "title": "y", "manifest": "runs/ok.json"}],
                     [{"slug": "a", "title": "x", "manifest": "../" + outside.name}],
                     [{"slug": "a", "title": "x", "manifest": "runs/ok.json", "extra": "1"}],
                     []):
            with self.assertRaises(GalleryError):
                build_gallery(self.catalog(runs), self.root / "site")
        site = self.root / "site"
        site.mkdir()
        (site / "old.txt").write_text("x")
        with self.assertRaisesRegex(GalleryError, "new or empty"):
            build_gallery(self.catalog([{"slug": "a", "title": "x", "manifest": "runs/ok.json"}]), site)

    def test_unsupported_mode_is_refused(self):
        manifest = {"mode": "unknown"}
        manifest["content_digest"] = digest(manifest)
        self.write("odd.json", manifest)
        with self.assertRaisesRegex(GalleryError, "Unsupported manifest mode"):
            build_gallery(self.catalog([{"slug": "odd", "title": "Odd", "manifest": "runs/odd.json"}]), self.root / "site")

    def test_repository_gallery_builds(self):
        with tempfile.TemporaryDirectory() as out:
            result = build_gallery(ROOT / "gallery/catalog.json", Path(out) / "site")
        catalog = json.loads((ROOT / "gallery/catalog.json").read_text())
        self.assertEqual(result["runs"], len(catalog["runs"]))


if __name__ == "__main__":
    unittest.main()

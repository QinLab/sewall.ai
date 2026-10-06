"""Earth Engine Skill tests with fake computations. Earth Engine is never contacted.

Run on a Slurm CPU node with the rest of the suite.
"""

from copy import deepcopy
from datetime import date
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from sewall import earthengine
from sewall.agent import default_registry, run_agent, verify_agent_manifest
from sewall.earthengine import EarthEngineError, alphaearth_context, check_window, check_year, chlorophyll_timeseries
from test_agent import Client, Sources, finish, review


class Computed:
    """Stands in for an ee.ComputedObject; getInfo returns a fixed document once."""

    def __init__(self, info):
        self.info, self.calls = info, 0

    def getInfo(self):
        self.calls += 1
        if isinstance(self.info, Exception):
            raise self.info
        return deepcopy(self.info)


def scene(index, day, ndci, pixels=500):
    return {"type": "Feature", "geometry": None, "id": str(index),
            "properties": {"image_id": f"20200{index}T155000_20200{index}T155000_T18SVF",
                           "time_start": int(day * 86_400_000), "cloudy_pixel_percentage": 12.5,
                           "ndci_mean": ndci, "water_pixels": pixels}}


S2 = {"scenes_in_window": 9, "scenes_under_cloud_limit": 4,
      "scenes": [scene(6, 18_430, 0.08), scene(7, 18_440, 0.21), scene(8, 18_450, None, 0)]}
EMBEDDING = {"images": 2, "pixels": 240_000, "dataset_version": "1", "model_version": "1",
             "means": {band: 0.1 for band in earthengine.BANDS}}


class ValidationTests(unittest.TestCase):
    def test_windows_years_and_sites(self):
        today = date(2026, 10, 5)
        self.assertEqual(check_window("2020-06-01", "2020-09-30", today), (date(2020, 6, 1), date(2020, 9, 30)))
        for start, end in (("2016-06-01", "2016-07-01"), ("2020-09-30", "2020-06-01"), ("2020-01-01", "2021-06-01"),
                           ("2020-6-1", "2020-07-01"), ("2026-10-01", "2026-10-09"), ("2020-02-30", "2020-03-01")):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                check_window(start, end, today)
        self.assertEqual(check_year("2024", today), 2024)
        for year in ("2016", "2026", "24", "2024.0", 2024):
            with self.subTest(year=year), self.assertRaises(ValueError):
                check_year(year, today)
        with self.assertRaisesRegex(ValueError, "lafayette-river"):
            chlorophyll_timeseries("somewhere-else", "2020-06-01", "2020-07-01", compute=Computed(S2))


class SummaryTests(unittest.TestCase):
    def test_s2_summary_is_bounded_and_records_provenance(self):
        computed = Computed(S2)
        result = chlorophyll_timeseries("lafayette-river", "2020-06-01", "2020-09-30", compute=computed)
        self.assertEqual(computed.calls, 1)
        self.assertEqual(result["dataset"], "COPERNICUS/S2_SR_HARMONIZED")
        self.assertEqual([item["date"] for item in result["scenes"]], ["2020-06-17", "2020-06-27", "2020-07-07"])
        self.assertEqual(result["scenes_with_water_pixels"], 2)
        self.assertEqual(result["ndci_range"], [0.08, 0.21])
        self.assertEqual(result["scenes"][2], {"image_id": S2["scenes"][2]["properties"]["image_id"], "date": "2020-07-07",
                                               "cloudy_pixel_percentage": 12.5, "water_pixels": 0, "ndci_mean": None})
        self.assertIn("Copernicus Sentinel", result["attribution"])
        self.assertEqual(len(result["requests"]), 1)
        self.assertRegex(result["requests"][0]["raw_sha256"], r"^[a-f0-9]{64}$")
        self.assertNotIn("project", str(result).lower())

    def test_alphaearth_summary_records_versions_and_attribution(self):
        result = alphaearth_context("lafayette-river", "2023", compute=Computed(EMBEDDING))
        self.assertEqual(len(result["mean_embedding"]), 64)
        self.assertEqual(result["mean_vector_norm"], 0.8)
        self.assertEqual((result["dataset_version"], result["model_version"]), ("1", "1"))
        self.assertIn("Google DeepMind", result["attribution"])
        empty = alphaearth_context("lafayette-river", "2023", compute=Computed({"images": 0, "means": {}, "pixels": 0}))
        self.assertIsNone(empty["mean_embedding"])
        self.assertEqual(empty["images"], 0)

    def test_malformed_results_fail(self):
        bad_s2 = [{**S2, "scenes": [scene(6, 18_430, 1.5)]}, {**S2, "scenes": [{"properties": {"image_id": "../x", "time_start": 1}}]},
                  {**S2, "scenes_under_cloud_limit": 1}, {**S2, "scenes": "many"}, ["not", "a", "dict"],
                  {**S2, "scenes": [scene(6, 18_430, float("nan"))]}, RuntimeError("private detail")]
        for info in bad_s2:
            with self.subTest(info=str(info)[:40]), self.assertRaises(EarthEngineError) as caught:
                chlorophyll_timeseries("lafayette-river", "2020-06-01", "2020-09-30", compute=Computed(info))
            self.assertNotIn("private", str(caught.exception))
        partial = {**EMBEDDING, "means": {"A00": 0.1}}
        for info in (partial, {**EMBEDDING, "means": {"B1": 0.1}}, {**EMBEDDING, "images": "2"}):
            with self.subTest(info=str(info)[:40]), self.assertRaises(EarthEngineError):
                alphaearth_context("lafayette-river", "2023", compute=Computed(info))

    def test_unconfigured_initialization_fails_before_any_request(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(earthengine.configured())
            with self.assertRaisesRegex(EarthEngineError, "EARTHENGINE_PROJECT"):
                chlorophyll_timeseries("lafayette-river", "2020-06-01", "2020-09-30")
        with patch.dict(os.environ, {"EARTHENGINE_PROJECT": "example-project"}), patch.dict(sys.modules, {"ee": None}):
            self.assertFalse(earthengine.configured())
            with self.assertRaisesRegex(EarthEngineError, "earthengine"):
                alphaearth_context("lafayette-river", "2023")

    def test_graph_is_built_against_fixed_collections_with_one_request(self):
        fake = MagicMock()
        fake.Dictionary.return_value = Computed(S2)
        with patch.dict(os.environ, {"EARTHENGINE_PROJECT": "example-project"}), patch.dict(sys.modules, {"ee": fake}):
            result = chlorophyll_timeseries("lafayette-river", "2020-06-01", "2020-09-30")
        fake.Initialize.assert_called_once_with(project="example-project")
        fake.ImageCollection.assert_called_once_with("COPERNICUS/S2_SR_HARMONIZED")
        fake.ImageCollection.return_value.filterBounds.return_value.filterDate.assert_called_once_with("2020-06-01", "2020-10-01")
        fake.Geometry.Rectangle.assert_called_once_with([-76.32, 36.88, -76.25, 36.915])
        self.assertEqual(fake.Dictionary.return_value.calls, 1)
        self.assertNotIn("example-project", str(result))
        fake = MagicMock()
        fake.Dictionary.return_value = Computed(EMBEDDING)
        with patch.dict(os.environ, {"EARTHENGINE_PROJECT": "example-project"}), patch.dict(sys.modules, {"ee": fake}):
            alphaearth_context("lafayette-river", "2023")
        fake.ImageCollection.assert_called_once_with("GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL")
        fake.Initialize.side_effect = RuntimeError("credential detail")
        with patch.dict(os.environ, {"EARTHENGINE_PROJECT": "example-project"}), patch.dict(sys.modules, {"ee": fake}):
            with self.assertRaises(EarthEngineError) as caught:
                alphaearth_context("lafayette-river", "2023")
        self.assertNotIn("credential detail", str(caught.exception))


def s2_action(start="2020-06-01", end="2020-09-30"):
    return {"action": "chlorophyll_timeseries", "site": "lafayette-river", "start": start, "end": end,
            "reason": "Check the chlorophyll proxy during a reported bloom season"}


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.sources = Sources()
        self.calls = []

    def earth(self, operation, arguments):
        self.calls.append((operation, arguments))
        if operation == "chlorophyll_timeseries":
            return chlorophyll_timeseries(compute=Computed(S2), **arguments)
        return alphaearth_context(compute=Computed(EMBEDDING), **arguments)

    def plan(self, *actions, **kwargs):
        return run_agent("What does a chlorophyll proxy show in the Lafayette River?", Client(*actions, finish()),
                         Client(review()), search_fn=self.sources.search, fetch_fn=self.sources.fetch,
                         earth_fn=self.earth, registry=default_registry(earth_engine=True), **kwargs)

    def test_summaries_become_source_nodes_and_replay(self):
        alpha = {"action": "alphaearth_context", "site": "lafayette-river", "year": "2023", "reason": "Annual context"}
        result = self.plan(s2_action(), alpha)
        self.assertTrue(verify_agent_manifest(result)["valid"])
        self.assertEqual([item[0] for item in self.calls], ["chlorophyll_timeseries", "alphaearth_context"])
        nodes = {node["id"]: node for node in result["graph"]["nodes"]}
        self.assertIn("earthengine:chlorophyll_timeseries:2020-09-30:lafayette-river:2020-06-01", nodes)
        self.assertEqual(result["policies"][0]["status"], "summary_only")
        self.assertEqual(result["metrics"]["source_requests"], 2)
        observation = result["actions"][1]["observation"]
        self.assertEqual(observation["mean_embedding_dimensions"], 64)
        self.assertNotIn("mean_embedding", observation)
        # No PubMed or GenBank records were retrieved, so the run cannot claim evidence.
        self.assertEqual(result["status"], "no_evidence")

    def test_invalid_and_repeated_requests_make_no_call(self):
        result = self.plan(s2_action(start="2014-01-01"))
        self.assertEqual(result["stop_reason"], "invalid_model_action")
        self.assertEqual(self.calls, [])
        result = self.plan(s2_action(), s2_action())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["actions"][1]["status"], "blocked")
        site = {**s2_action(), "site": "elsewhere"}
        self.assertEqual(self.plan(site)["stop_reason"], "invalid_model_action")

    def test_unconfigured_registry_omits_earth_skills(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("chlorophyll_timeseries", default_registry())
            result = run_agent("Chlorophyll?", Client(s2_action(), finish()), Client(review()),
                               search_fn=self.sources.search, fetch_fn=self.sources.fetch, earth_fn=self.earth)
        self.assertEqual(result["stop_reason"], "invalid_model_action")
        self.assertEqual(self.calls, [])

    def test_engine_failure_is_recorded_as_failed_action(self):
        def failing(operation, arguments):
            raise EarthEngineError("Earth Engine request failed (HttpError); no retry attempted")
        result = run_agent("Chlorophyll?", Client(s2_action(), finish()), Client(review()),
                           search_fn=self.sources.search, fetch_fn=self.sources.fetch, earth_fn=failing,
                           registry=default_registry(earth_engine=True))
        self.assertEqual(result["actions"][0]["status"], "failed")
        self.assertIn("no retry", result["actions"][0]["observation"]["error"])
        self.assertTrue(verify_agent_manifest(result)["valid"])


if __name__ == "__main__":
    unittest.main()

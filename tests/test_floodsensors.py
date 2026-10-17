"""Flood sensor Skill tests with simulated responses. No service is contacted.

Run on a Slurm CPU node with the rest of the suite.
"""

from datetime import datetime, timezone
import json
import unittest

from sewall.agent import default_registry, run_agent, verify_agent_manifest
from sewall.floodsensors import FloodSensorError, flood_sensors
from test_agent import Client, Sources, finish, review


NOW = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)


def gauge(lid, lat, lon, category, primary=2.0, valid="2026-10-15T01:30:00Z"):
    return {"lid": lid, "name": lid + " gauge", "latitude": lat, "longitude": lon,
            "status": {"observed": {"primary": primary, "primaryUnit": "ft", "floodCategory": category, "validTime": valid}}}


NWPS = {"gauges": [gauge("SWPV2", 36.9467, -76.33, "no_flooding"), gauge("EZMV2", 36.86, -76.31, "minor", 4.1),
                   gauge("CBBV2", 36.9667, -76.1133, "out_of_service", -999, "0001-01-01T00:00:00Z"),
                   gauge("FARV2", 38.0, -77.0, "major")]}
USGS = {"features": [{"geometry": {"type": "Point", "coordinates": [-76.31, 36.86]},
                      "properties": {"monitoring_location_id": "USGS-0204288831", "parameter_code": "62620",
                                     "time": "2026-10-15T01:48:00+00:00", "value": "0.61", "unit_of_measure": "ft",
                                     "approval_status": "Provisional"}}]}
LEVELS = {"8638610": "8.95", "8639348": "6.0", "8638901": None}
THRESHOLDS = {"nos_minor": 8.89, "nos_moderate": 9.85, "nos_major": 11.09, "nws_minor": 8.88, "action": 8.38}


class Services:
    """Answers the fixed service URLs from canned documents and records each request."""

    def __init__(self, fail=()):
        self.urls, self.fail = [], set(fail)

    def __call__(self, url):
        self.urls.append(url)
        for name in self.fail:
            if name in url:
                raise FloodSensorError("Service returned HTTP 503; no retry attempted")
        if "nwps" in url:
            document = NWPS
        elif "waterdata.usgs.gov" in url:
            document = USGS
        elif "floodlevels" in url:
            document = THRESHOLDS
        else:
            station = url.split("station=")[1].split("&")[0]
            value = LEVELS[station]
            document = ({"error": {"message": "No data was found."}} if value is None else
                        {"metadata": {"id": station}, "data": [{"t": "2026-10-15 01:54", "v": value, "q": "p"}]})
        return json.dumps(document).encode()


class SnapshotTests(unittest.TestCase):
    def test_area_snapshot_reports_categories_and_thresholds(self):
        services = Services()
        result = flood_sensors("norfolk", get=services, now=NOW)
        self.assertEqual([item["lid"] for item in result["nwps_gauges"]], ["CBBV2", "EZMV2", "SWPV2"])
        out = result["nwps_gauges"][0]
        self.assertEqual((out["observed"], out["valid_time"], out["stale"]), (None, None, False))
        self.assertEqual(result["nwps_categories"], {"minor": 1, "no_flooding": 1, "out_of_service": 1})
        stations = {item["station"]: item for item in result["coops_stations"]}
        self.assertEqual(sorted(stations), ["8638610", "8638901", "8639348"])
        self.assertEqual(stations["8638610"]["flood_level"], "minor")
        self.assertEqual(stations["8638610"]["feet_below_minor"], -0.06)
        self.assertEqual(stations["8639348"]["flood_level"], "below_action")
        self.assertEqual(stations["8638901"]["flood_level"], "no_recent_reading")
        self.assertEqual({(item["source"], item["id"]) for item in result["at_or_above_action"]},
                         {("nwps", "EZMV2"), ("coops", "8638610")})
        self.assertEqual(result["usgs_series"][0]["value"], 0.61)
        self.assertNotIn("flood_level", result["usgs_series"][0])
        self.assertEqual(len(result["requests"]), len(services.urls))
        self.assertEqual(len(services.urls), 2 + 2 * 3)
        self.assertEqual(result["response_bytes"], sum(item["response_bytes"] for item in result["requests"]))
        for url in services.urls:
            self.assertTrue(url.startswith(("https://api.water.noaa.gov/", "https://api.tidesandcurrents.noaa.gov/",
                                            "https://api.waterdata.usgs.gov/")))

    def test_stale_readings_and_areas_without_stations(self):
        result = flood_sensors("lafayette-river", get=Services(), now=datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc))
        self.assertEqual(result["coops_stations"], [])
        self.assertTrue(all(item["stale"] for item in result["nwps_gauges"] + result["usgs_series"]))
        with self.assertRaisesRegex(ValueError, "norfolk"):
            flood_sensors("somewhere", get=Services())

    def test_one_failed_service_is_recorded_and_all_failed_raises(self):
        result = flood_sensors("norfolk", get=Services(fail={"nwps"}), now=NOW)
        self.assertEqual(result["unavailable"][0]["service"], "nwps")
        self.assertEqual(len(result["coops_stations"]), 3)
        with self.assertRaisesRegex(FloodSensorError, "Every flood sensor service failed"):
            flood_sensors("norfolk", get=Services(fail={"nwps", "tidesandcurrents", "usgs"}), now=NOW)

    def test_malformed_documents_fail_that_service(self):
        for document in ({"gauges": "many"}, {"gauges": [{"lid": "../x"}]}, {"gauges": [gauge("A B", 36.9, -76.3, "minor")]},
                         {"gauges": [gauge("SWPV2", 36.9, -76.3, "minor", float("nan"))]}):
            def get(url, document=document):
                return json.dumps(document).encode() if "nwps" in url else Services()(url)
            with self.subTest(document=str(document)[:40]):
                result = flood_sensors("norfolk", get=get, now=NOW)
                self.assertEqual([item["service"] for item in result["unavailable"]], ["nwps"])
        result = flood_sensors("norfolk", get=lambda url: b"x" * 1_048_577 if "usgs" in url else Services()(url), now=NOW)
        self.assertIn("byte limit", result["unavailable"][0]["error"])


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.sources, self.calls = Sources(), []

    def flood(self, operation, arguments):
        self.calls.append((operation, arguments))
        return flood_sensors(get=Services(), now=NOW, **arguments)

    def plan(self, *actions):
        return run_agent("Is anything flooding in Norfolk right now?", Client(*actions, finish()), Client(review()),
                         search_fn=self.sources.search, fetch_fn=self.sources.fetch, flood_fn=self.flood,
                         registry=default_registry(earth_engine=False))

    def test_snapshot_becomes_a_source_node_and_replays(self):
        result = self.plan({"action": "flood_sensors", "area": "norfolk", "reason": "Current levels"})
        self.assertTrue(verify_agent_manifest(result)["valid"])
        self.assertIn("floodsensors:norfolk:2026-10-15T02:00:00Z", {node["id"] for node in result["graph"]["nodes"]})
        self.assertEqual(result["policies"][0]["status"], "summary_only")
        self.assertEqual(result["metrics"]["source_requests"], 8)
        self.assertNotIn("requests", result["actions"][0]["observation"])
        self.assertEqual(len(result["actions"][0]["observation"]["at_or_above_action"]), 2)

    def test_invalid_and_repeated_areas_make_no_extra_call(self):
        result = self.plan({"action": "flood_sensors", "area": "ocean-city", "reason": "x"})
        self.assertEqual(result["stop_reason"], "invalid_model_action")
        self.assertEqual(self.calls, [])
        action = {"action": "flood_sensors", "area": "norfolk", "reason": "x"}
        result = self.plan(action, action)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(result["actions"][1]["status"], "blocked")

    def test_total_failure_is_a_failed_action(self):
        def failing(operation, arguments):
            raise FloodSensorError("Every flood sensor service failed; Request failed (URLError); no retry attempted")
        result = run_agent("Flooding?", Client({"action": "flood_sensors", "area": "norfolk", "reason": "x"}, finish()),
                           Client(review()), search_fn=self.sources.search, fetch_fn=self.sources.fetch, flood_fn=failing,
                           registry=default_registry(earth_engine=False))
        self.assertEqual(result["actions"][0]["status"], "failed")
        self.assertTrue(verify_agent_manifest(result)["valid"])


if __name__ == "__main__":
    unittest.main()

"""Day 3 quick-fix tests with mocked transport: RefSeq accessions and bad GenBank summaries.

These tests never contact a live service. Run them on a Slurm CPU node.
"""

import asyncio
from contextlib import redirect_stderr
from datetime import datetime, timezone
import io
import json
import os
import unittest
from unittest.mock import patch

from sewall import genbank
from sewall.floodsensors import USGS_PAGE_LIMIT, flood_sensors
import test_floodsensors
from test_floodsensors import NOW, Services, gauge
import test_genbank
from test_genbank import esearch, esummary


class AccessionTests(unittest.TestCase):
    def test_refseq_and_wgs_accessions_are_accepted(self):
        for accession in ("MN908947.3", "NC_045512.2", "NZ_CP012345.1", "NZ_JAAXYZ010000001.1", "AB123456"):
            self.assertTrue(genbank._ACCESSION.fullmatch(accession), accession)
        for bad in ("nz_CP012345.1", "NZ__CP012345.1", "NZ_CP12.1", "NZ_CP012345.1 x", "_CP012345.1"):
            self.assertFalse(genbank._ACCESSION.fullmatch(bad), bad)

    @unittest.skipUnless(hasattr(genbank, "_FASTA_HEADER"), "marker FASTA retrieval not present")
    def test_fasta_header_accepts_refseq_accessions(self):
        match = genbank._FASTA_HEADER.match(">NZ_CP012345.1 Example strain 28S rRNA")
        self.assertEqual(match.group(1), "NZ_CP012345.1")


class SkippedRecordTests(unittest.TestCase):
    setUp = test_genbank.InventoryTransportTests.setUp
    respond = test_genbank.InventoryTransportTests.respond

    def test_a_malformed_summary_is_skipped_instead_of_failing_the_inventory(self):
        summary = esummary("11", "12", "13")
        summary["result"]["12"]["accessionversion"] = "not an accession"
        summary["result"]["13"]["accessionversion"] = "NZ_CP000013.1"
        self.respond(esearch(12), esearch(10, ["11", "12", "13"]), esearch(7), esearch(2), esearch(3),
                     esearch(0), summary)
        result = genbank.taxon_inventory("Margalefidinium polykrikoides", sample=3)
        self.assertEqual([item["accession"] for item in result["sample"]], ["MN000011.1", "NZ_CP000013.1"])
        self.assertEqual(result["requests"][-1]["skipped_records"], 1)
        self.assertEqual(result["total"], 12)



def serve(nwps=None, usgs=None):
    """Canned services with the NWPS or USGS document replaced."""
    def get(url):
        if nwps is not None and "nwps" in url:
            return json.dumps(nwps).encode()
        if usgs is not None and "waterdata.usgs.gov" in url:
            return json.dumps(usgs).encode()
        return Services()(url)
    return get


def feature(site, minute=48):
    return {"geometry": {"type": "Point", "coordinates": [-76.31, 36.86]},
            "properties": {"monitoring_location_id": site, "parameter_code": "62620",
                           "time": f"2026-10-15T01:{minute:02d}:00+00:00", "value": "0.61", "unit_of_measure": "ft"}}


class FloodTests(unittest.TestCase):
    def test_stale_flood_levels_are_not_reported_as_current(self):
        result = flood_sensors("norfolk", get=Services(), now=datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc))
        self.assertEqual(result["at_or_above_action"], [])
        self.assertEqual({(item["source"], item["id"]) for item in result["at_or_above_action_not_current"]},
                         {("nwps", "EZMV2"), ("coops", "8638610")})
        current = flood_sensors("norfolk", get=Services(), now=NOW)
        self.assertEqual(len(current["at_or_above_action"]), 2)
        self.assertEqual(current["at_or_above_action_not_current"], [])

    def test_a_flood_category_without_a_reading_is_not_current(self):
        document = {"gauges": [gauge("EZMV2", 36.86, -76.31, "minor", -999)]}
        result = flood_sensors("norfolk", get=serve(nwps=document), now=NOW)
        self.assertNotIn("nwps", {item["source"] for item in result["at_or_above_action"]})
        self.assertEqual(result["at_or_above_action_not_current"][0]["id"], "EZMV2")

    def test_one_malformed_gauge_or_series_is_skipped(self):
        nwps = {"gauges": test_floodsensors.NWPS["gauges"] + [{"lid": "../x"},
                                                              gauge("BADV2", 36.9, -76.3, "minor", 1.0, "yesterday")]}
        usgs = {"features": test_floodsensors.USGS["features"] + [{"geometry": None, "properties": {}}]}
        result = flood_sensors("norfolk", get=serve(nwps, usgs), now=NOW)
        self.assertEqual(result["unavailable"], [])
        self.assertEqual([item["lid"] for item in result["nwps_gauges"]], ["CBBV2", "EZMV2", "SWPV2"])
        self.assertEqual((result["nwps_malformed"], result["usgs_malformed"]), (2, 1))
        self.assertEqual(len(result["usgs_series"]), 1)

    def test_usgs_totals_are_not_capped_at_one_page(self):
        page = [feature(f"USGS-0204{index:06d}") for index in range(USGS_PAGE_LIMIT)]
        result = flood_sensors("norfolk", get=serve(usgs={"features": page, "numberMatched": 120}), now=NOW)
        self.assertEqual((result["usgs_series_in_area"], result["usgs_truncated"]), (120, True))
        self.assertEqual(len(result["usgs_series"]), 30)
        result = flood_sensors("norfolk", get=serve(usgs={"features": page}), now=NOW)
        self.assertEqual((result["usgs_series_in_area"], result["usgs_truncated"]), (USGS_PAGE_LIMIT, True))
        few = {"features": page[:3], "links": [{"rel": "next", "href": "https://api.waterdata.usgs.gov/x"}]}
        result = flood_sensors("norfolk", get=serve(usgs=few), now=NOW)
        self.assertTrue(result["usgs_truncated"])
        result = flood_sensors("norfolk", get=serve(usgs={"features": page[:3], "numberMatched": 3}), now=NOW)
        self.assertEqual((result["usgs_series_in_area"], result["usgs_truncated"]), (3, False))
        result = flood_sensors("norfolk", get=serve(usgs={"features": page + page[:1]}), now=NOW)
        self.assertEqual(result["unavailable"][0]["service"], "usgs")



try:
    from sewall import mcp_server
except ImportError:
    mcp_server = None

TOKEN = "t" * 40


def call(app, headers):
    """Send one HTTP request through an ASGI app and return the response status."""
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(app({"type": "http", "method": "POST", "path": "/mcp", "headers": headers}, receive, send))
    return sent[0]["status"]


@unittest.skipUnless(mcp_server, "Optional MCP SDK is not installed")
class BearerTokenTests(unittest.TestCase):
    def wrapped(self):
        async def inner(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})
        return mcp_server.BearerAuth(inner, TOKEN)

    def test_requests_need_the_exact_bearer_token(self):
        app = self.wrapped()
        self.assertEqual(call(app, [(b"authorization", b"Bearer " + TOKEN.encode())]), 200)
        self.assertEqual(call(app, [(b"Authorization", b"Bearer " + TOKEN.encode())]), 200)
        for headers in ([], [(b"authorization", b"Bearer wrong")], [(b"authorization", TOKEN.encode())],
                        [(b"authorization", b"bearer " + TOKEN.encode())]):
            with self.subTest(headers=headers):
                self.assertEqual(call(app, headers), 401)

    def test_lifespan_events_pass_through(self):
        seen = []

        async def inner(scope, receive, send):
            seen.append(scope["type"])
        asyncio.run(mcp_server.BearerAuth(inner, TOKEN)({"type": "lifespan"}, None, None))
        self.assertEqual(seen, ["lifespan"])

    def test_forwarded_hosts_require_a_strong_token(self):
        security = mcp_server.mcp.settings.transport_security
        saved = (list(security.allowed_hosts), list(security.allowed_origins), mcp_server.mcp.settings.port)
        self.addCleanup(lambda: (setattr(security, "allowed_hosts", saved[0]),
                                 setattr(security, "allowed_origins", saved[1]),
                                 setattr(mcp_server.mcp.settings, "port", saved[2])))
        with patch.dict(os.environ, {"SEWALL_MCP_TOKEN": ""}), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mcp_server.main(["--transport", "streamable-http", "--allowed-host", "example.trycloudflare.com"])
        with patch.dict(os.environ, {"SEWALL_MCP_TOKEN": "short"}), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            mcp_server.main(["--transport", "streamable-http"])
        with patch.dict(os.environ, {"SEWALL_MCP_TOKEN": TOKEN}), patch("uvicorn.run") as run:
            mcp_server.main(["--transport", "streamable-http", "--port", "8123",
                             "--allowed-host", "example.trycloudflare.com"])
        app = run.call_args.args[0]
        self.assertIsInstance(app, mcp_server.BearerAuth)
        self.assertEqual((run.call_args.kwargs["host"], run.call_args.kwargs["port"]), ("127.0.0.1", 8123))


if __name__ == "__main__":
    unittest.main()

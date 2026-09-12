"""NCBI transport and boundary tests. These tests never contact NCBI."""

import hashlib
import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

from sewall.ncbi import (
    ENDPOINT,
    MAX_RESPONSE_BYTES,
    MetadataSearchError,
    _NoRedirect,
    search_metadata,
)


class Response(io.BytesIO):
    def __init__(self, raw, url, status=200):
        super().__init__(raw)
        self.url = url
        self.status = status

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status


class NCBISearchTests(unittest.TestCase):
    def setUp(self):
        self.builder = patch("sewall.ncbi.build_opener").start()
        self.sleep = patch("sewall.ncbi.time.sleep").start()
        self.addCleanup(patch.stopall)

    def respond(self, result, *, redirect=None):
        raw = result if isinstance(result, bytes) else json.dumps(result).encode()
        self.builder.return_value.open.side_effect = lambda req, **kwargs: Response(
            raw, redirect or req.full_url
        )
        return raw

    def test_bounded_ids_query_encoding_and_provenance(self):
        raw = self.respond({"esearchresult": {
            "count": "12", "idlist": ["123", "456"], "querytranslation": "oak[All Fields]"
        }})
        query = "oak & heat https://other.example/?x=1"
        result = search_metadata("pubmed", query, limit=2, email="person@example.org")
        request = self.builder.return_value.open.call_args.args[0]
        parsed = urlparse(request.full_url)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.hostname, "eutils.ncbi.nlm.nih.gov")
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(params["term"], [query])
        self.assertEqual(params["retmax"], ["2"])
        self.assertEqual(params["email"], ["person@example.org"])
        self.assertNotIn("email", parse_qs(urlparse(result["url"]).query))
        self.assertEqual(self.builder.return_value.open.call_args.kwargs["timeout"], 15)
        self.assertEqual(result["ids"], ["123", "456"])
        self.assertEqual(result["returned"], 2)
        self.assertEqual(result["total"], 12)
        self.assertTrue(result["truncated"])
        self.assertEqual(result["raw_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["kind"], "public_metadata_identifiers_only")
        self.assertIsInstance(self.builder.call_args.args[0], _NoRedirect)

    def test_empty_result_is_valid_and_not_fabricated(self):
        self.respond({"esearchresult": {"count": "0", "idlist": []}})
        result = search_metadata("gds", "an-unmatched-query")
        self.assertEqual(result["ids"], [])
        self.assertFalse(result["truncated"])

    def test_invalid_inputs_never_open_network(self):
        cases = [
            ("dbgap", "oak", {}),
            ("pubmed", "", {}),
            ("pubmed", "x" * 2001, {}),
            ("pubmed", "oak\nheat", {}),
            ("pubmed", "oak", {"limit": 0}),
            ("pubmed", "oak", {"limit": 21}),
            ("pubmed", "oak", {"limit": True}),
            ("pubmed", "oak", {"email": "bad\n@example.org"}),
            ("pubmed", "oak", {"email": "@"}),
        ]
        for database, query, kwargs in cases:
            with self.subTest(database=database, query=query[:25], kwargs=kwargs):
                with self.assertRaises(ValueError):
                    search_metadata(database, query, **kwargs)
        self.builder.assert_not_called()

    def test_rejects_oversized_response(self):
        self.respond(b"x" * (MAX_RESPONSE_BYTES + 1))
        with self.assertRaisesRegex(MetadataSearchError, "byte limit"):
            search_metadata("pubmed", "oak")

    def test_rejects_malformed_and_inconsistent_results(self):
        cases = [
            b"<html>error</html>",
            {"error": "unavailable"},
            {"esearchresult": {"count": "3", "idlist": ["https://other.example"]}},
            {"esearchresult": {"count": "0", "idlist": ["1"]}},
            {"esearchresult": {"count": "2", "idlist": ["1", "1"]}},
            {"esearchresult": {"count": "1", "idlist": ["1"], "errorlist": {}}},
            {"esearchresult": {"count": "6", "idlist": [str(i) for i in range(6)]}},
        ]
        for document in cases:
            with self.subTest(document=document):
                self.respond(document)
                with self.assertRaises(MetadataSearchError):
                    search_metadata("pubmed", "oak")

    def test_redirects_and_changed_response_urls_fail(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))
        self.respond({"esearchresult": {"count": "0", "idlist": []}}, redirect="https://other.example/")
        with self.assertRaisesRegex(MetadataSearchError, "changed"):
            search_metadata("pubmed", "oak")

    def test_errors_are_reported_once_without_retry(self):
        for error in [URLError("offline"), HTTPError(ENDPOINT, 429, "rate limited", {}, None)]:
            with self.subTest(error=type(error).__name__):
                self.builder.return_value.open.reset_mock()
                self.builder.return_value.open.side_effect = error
                with self.assertRaisesRegex(MetadataSearchError, "no retry"):
                    search_metadata("pubmed", "oak")
                self.builder.return_value.open.assert_called_once()


if __name__ == "__main__":
    unittest.main()

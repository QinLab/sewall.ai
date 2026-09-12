"""Bounded public NCBI identifier discovery. No biological data are downloaded."""

from datetime import datetime, timezone
import hashlib
import json
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


DATABASES = frozenset({"pubmed", "gds", "snp", "bioproject"})
ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
MAX_RESPONSE_BYTES = 1_048_576
TIMEOUT_SECONDS = 15
MAX_QUERY_LENGTH = 2000
_REQUEST_LOCK = threading.Lock()
_LAST_REQUEST_AT = 0.0


class MetadataSearchError(RuntimeError):
    """Retrieval failed or the service returned an unusable response."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def search_metadata(
    database: str, query: str, *, limit: int = 5, email: str | None = None
) -> dict:
    """Return public Entrez IDs and provenance, not records or scientific claims.

    Queries go to a fixed host. Redirects and automatic retries are disabled.
    Calls in this process are spaced below three requests per second; deployments
    must coordinate the NCBI limit across processes sharing their public IP.
    Optional contact email is sent to NCBI but omitted from returned provenance.
    """
    if not isinstance(database, str) or database not in DATABASES:
        raise ValueError("database must be one of: bioproject, gds, pubmed, snp")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a nonempty string")
    if len(query) > MAX_QUERY_LENGTH:
        raise ValueError(f"query must contain at most {MAX_QUERY_LENGTH} characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in query):
        raise ValueError("query must not contain control characters")
    if type(limit) is not int or not 1 <= limit <= 20:
        raise ValueError("limit must be an integer from 1 to 20")
    if email is not None and (
        not isinstance(email, str)
        or not 3 <= len(email) <= 254
        or email.count("@") != 1
        or email.startswith("@")
        or email.endswith("@")
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in email)
    ):
        raise ValueError("email must be a valid contact address without whitespace")

    parameters = {
        "db": database,
        "term": query,
        "retmode": "json",
        "retmax": str(limit),
        "retstart": "0",
        "tool": "sewall_discover_prototype",
    }
    provenance_url = ENDPOINT + "?" + urlencode(parameters)
    if email:
        parameters["email"] = email
    request_url = ENDPOINT + "?" + urlencode(parameters)
    request = Request(
        request_url,
        headers={
            "User-Agent": "SewallDiscoverPrototype/0.1 (public metadata discovery)",
            "Accept": "application/json",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirect())
    global _LAST_REQUEST_AT
    try:
        with _REQUEST_LOCK:
            pause = 0.35 - (time.monotonic() - _LAST_REQUEST_AT)
            if pause > 0:
                time.sleep(pause)
            _LAST_REQUEST_AT = time.monotonic()
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                if response.geturl() != request_url:
                    raise MetadataSearchError("NCBI response changed the requested URL")
                if response.getcode() != 200:
                    raise MetadataSearchError("NCBI returned a non-success status")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise MetadataSearchError(f"NCBI returned HTTP {exc.code}; no retry attempted") from None
    except (URLError, OSError, TimeoutError) as exc:
        raise MetadataSearchError(
            f"NCBI request failed ({type(exc).__name__}); no retry attempted"
        ) from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise MetadataSearchError("NCBI response exceeded the byte limit")
    try:
        document = json.loads(raw)
        result = document["esearchresult"]
        count = result["count"]
        identifiers = result["idlist"]
        if (
            "error" in result
            or "errorlist" in result
            or "error" in document
            or not isinstance(count, str)
            or not count.isascii()
            or not count.isdecimal()
            or len(count) > 20
            or not isinstance(identifiers, list)
            or len(identifiers) > limit
            or any(
                not isinstance(identifier, str)
                or not identifier.isascii()
                or not identifier.isdecimal()
                or len(identifier) > 20
                for identifier in identifiers
            )
        ):
            raise ValueError("invalid ESearch result")
        total = int(count)
        if total < len(identifiers) or len(set(identifiers)) != len(identifiers):
            raise ValueError("inconsistent ESearch result")
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise MetadataSearchError("NCBI response was not a valid bounded ESearch result") from None

    return {
        "database": database,
        "query": query,
        "url": provenance_url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "response_bytes": len(raw),
        "total": total,
        "returned": len(identifiers),
        "truncated": total > len(identifiers),
        "ids": identifiers,
        "query_translation": result.get("querytranslation"),
        "warnings": result.get("warninglist", {}),
        "kind": "public_metadata_identifiers_only",
        "limitations": [
            "Entrez IDs only; no expression matrices, participant genotypes, or full text retrieved.",
            "IDs have not been linked to fixture graph nodes or validated as scientific evidence.",
            "Search totals and rankings can change; the digest describes this response only.",
            "A public record does not establish unrestricted rights to all associated content.",
            "NCBI disclaimer: https://www.ncbi.nlm.nih.gov/About/disclaimer.html",
        ],
    }

"""Bounded GenBank nucleotide record inventory for one taxon. No sequences are downloaded.

Counts come from NCBI ESearch on the nuccore database; a few record summaries
come from ESummary. Marker counts use title keywords and are approximate.
Transcriptome shotgun assembly (TSA) contigs are counted separately because a
single assembly can add over 100,000 records for one species.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from . import ncbi


BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
MAX_RESPONSE_BYTES = 1_048_576
TIMEOUT_SECONDS = 15
MAX_SAMPLE = 5
MAX_RETRY_WAIT_SECONDS = 3.0
TSA = '"tsa"[Properties]'
MARKERS = {
    "lsu": '(28S[Title] OR "large subunit"[Title] OR LSU[Title])',
    "ssu": '(18S[Title] OR "small subunit"[Title] OR SSU[Title])',
    "its": '("internal transcribed spacer"[Title] OR ITS1[Title] OR ITS2[Title])',
    "coi": '(COI[Title] OR COX1[Title] OR "cytochrome oxidase subunit I"[Title]'
           ' OR "cytochrome c oxidase subunit I"[Title])',
}
QUALIFIERS = ("strain", "isolate", "country", "geo_loc_name", "collection_date",
              "isolation_source", "host", "lat_lon")
_TAXID = re.compile(r"[1-9][0-9]{0,9}\Z", re.ASCII)
_NAME = re.compile(r"[A-Z][a-z]{1,40}(?: [a-z][a-z-]{1,40}){0,2}\Z", re.ASCII)
_UID = re.compile(r"[1-9][0-9]{0,19}\Z", re.ASCII)
_ACCESSION = re.compile(r"[A-Z]{1,6}_?[0-9]{5,12}(?:\.[0-9]{1,4})?\Z", re.ASCII)


class InventoryError(RuntimeError):
    """Retrieval failed or the service returned an unusable response."""


def organism_term(taxon: str) -> str:
    """Return an Entrez organism term for a taxonomy ID or a scientific name."""
    if not isinstance(taxon, str):
        raise ValueError("taxon must be a string")
    if _TAXID.fullmatch(taxon):
        return f"txid{taxon}[Organism:exp]"
    if _NAME.fullmatch(taxon):
        return f'"{taxon}"[Organism:exp]'
    raise ValueError("taxon must be a numeric NCBI taxonomy ID or a scientific name such as Genus species")


def _get(utility, parameters):
    parameters = {**parameters, "tool": "sewall_discover_prototype"}
    provenance_url = BASE + utility + ".fcgi?" + urlencode(parameters)
    key = os.environ.get("NCBI_API_KEY")
    request_url = BASE + utility + ".fcgi?" + urlencode({**parameters, "api_key": key}) if key else provenance_url
    request = Request(request_url, headers={
        "User-Agent": "SewallDiscoverPrototype/0.2 (public sequence metadata inventory)",
        "Accept": "application/json",
    }, method="GET")
    opener = build_opener(ncbi._NoRedirect())
    attempts, wait = 0, 0.0
    while True:
        attempts += 1
        try:
            with ncbi._REQUEST_LOCK:
                pause = max((0.12 if key else 0.4) - (ncbi.time.monotonic() - ncbi._LAST_REQUEST_AT), wait)
                if pause > 0:
                    ncbi.time.sleep(pause)
                ncbi._LAST_REQUEST_AT = ncbi.time.monotonic()
                with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                    if response.geturl() != request_url:
                        raise InventoryError("NCBI response changed the requested URL")
                    if response.getcode() != 200:
                        raise InventoryError("NCBI returned a non-success status")
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
            break
        except HTTPError as exc:
            # A shared cluster address can exceed the per-IP rate; one bounded retry on 429 only.
            if exc.code == 429 and attempts == 1:
                wait = _retry_wait(exc.headers.get("Retry-After") if exc.headers else None)
                continue
            raise InventoryError(f"NCBI returned HTTP {exc.code} after {attempts} attempt(s)") from None
        except (URLError, OSError, TimeoutError) as exc:
            raise InventoryError(f"NCBI request failed ({type(exc).__name__}); no retry attempted") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise InventoryError("NCBI response exceeded the byte limit")
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise InventoryError("NCBI response was not JSON") from None
    if not isinstance(document, dict) or "error" in document:
        raise InventoryError("NCBI returned an error document")
    return document, {"utility": utility, "url": provenance_url,
                      "raw_sha256": hashlib.sha256(raw).hexdigest(), "response_bytes": len(raw),
                      "attempts": attempts}


def _retry_wait(value):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        seconds = 1.0
    return min(max(seconds, 1.0), MAX_RETRY_WAIT_SECONDS) if seconds == seconds else 1.0


def _esearch(term, retmax):
    document, provenance = _get("esearch", {"db": "nuccore", "term": term, "retmode": "json",
                                            "retmax": str(retmax), "retstart": "0"})
    try:
        result = document["esearchresult"]
        count, ids = result["count"], result["idlist"]
        if "errorlist" in result and any(result["errorlist"].values()):
            raise InventoryError("NCBI did not recognize part of the search term; no counts reported")
        if ("error" in result
                or not isinstance(count, str) or not count.isascii() or not count.isdecimal() or len(count) > 20
                or not isinstance(ids, list) or len(ids) > retmax or len(set(ids)) != len(ids)
                or any(not isinstance(uid, str) or not _UID.fullmatch(uid) for uid in ids)
                or int(count) < len(ids)):
            raise ValueError
    except InventoryError:
        raise
    except (KeyError, TypeError, AttributeError, ValueError):
        raise InventoryError("NCBI response was not a valid bounded ESearch result") from None
    return int(count), ids, provenance


def _qualifiers(record):
    names, values = record.get("subtype"), record.get("subname")
    if not isinstance(names, str) or not isinstance(values, str):
        return {}
    names, values = names.split("|"), values.split("|")
    if len(names) != len(values):
        return {}
    return {name: value[:200] for name, value in zip(names, values) if name in QUALIFIERS and value}


def _esummary(ids):
    document, provenance = _get("esummary", {"db": "nuccore", "id": ",".join(ids), "retmode": "json",
                                             "version": "2.0"})
    try:
        result = document["result"]
        uids = result["uids"]
        if (not isinstance(uids, list) or len(uids) > len(ids) or len(set(uids)) != len(uids)
                or any(uid not in ids for uid in uids)):
            raise ValueError
        summaries = []
        for uid in ids:
            record = result.get(uid)
            if uid not in uids or not isinstance(record, dict) or "error" in record:
                continue
            accession = record.get("accessionversion")
            if not isinstance(accession, str) or not _ACCESSION.fullmatch(accession):
                raise ValueError
            length = record.get("slen")
            taxid = record.get("taxid")
            summaries.append({
                "uid": uid, "accession": accession,
                "source_url": f"https://www.ncbi.nlm.nih.gov/nuccore/{accession}",
                "title": str(record.get("title") or "")[:300],
                "organism": str(record.get("organism") or "")[:200],
                "taxid": str(taxid) if type(taxid) is int or isinstance(taxid, str) and _TAXID.fullmatch(taxid) else None,
                "length": length if type(length) is int and length >= 0 else None,
                "molecule": str(record.get("moltype") or "")[:40],
                "create_date": str(record.get("createdate") or "")[:20],
                "qualifiers": _qualifiers(record),
                "trust": "untrusted_source_metadata",
            })
    except (KeyError, TypeError, AttributeError, ValueError):
        raise InventoryError("NCBI response was not a valid bounded ESummary result") from None
    return summaries, provenance


def taxon_inventory(taxon: str, *, sample: int = 3) -> dict:
    """Count public nuccore records for a taxon by marker and summarize a few records.

    At most seven requests: ESearches for the total, the non-TSA records and
    each marker, then one ESummary of non-TSA sample records. Calls share the
    in-process NCBI rate limiter. An NCBI_API_KEY in the environment is sent to
    NCBI but omitted from returned provenance.
    """
    term = organism_term(taxon)
    if type(sample) is not int or not 0 <= sample <= MAX_SAMPLE:
        raise ValueError(f"sample must be an integer from 0 to {MAX_SAMPLE}")
    requests = []
    total, _, provenance = _esearch(term, 0)
    requests.append(provenance)
    counts, ids, excluding_tsa = dict.fromkeys(MARKERS, 0), [], 0
    if total:
        excluding_tsa, ids, provenance = _esearch(f"{term} NOT {TSA}", sample)
        requests.append(provenance)
        if excluding_tsa > total:
            raise InventoryError("NCBI counts were inconsistent; no counts reported")
        for marker, keywords in MARKERS.items():
            counts[marker], _, provenance = _esearch(f"{term} AND {keywords}", 0)
            requests.append(provenance)
    summaries = []
    if ids:
        summaries, provenance = _esummary(ids)
        requests.append(provenance)
    return {
        "kind": "public_sequence_metadata_inventory", "mode": "live_public_metadata",
        "database": "nuccore", "taxon": taxon, "organism_term": term,
        "total": total, "total_excluding_tsa": excluding_tsa, "tsa_records": total - excluding_tsa,
        "marker_counts": counts, "classification": "title_keyword_heuristic",
        "sample": summaries, "requests": requests,
        "response_bytes": sum(item["response_bytes"] for item in requests),
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "limitations": [
            "Record counts and summaries only; no sequences were downloaded.",
            "Marker counts match title keywords; records can be missed or counted under several markers.",
            "TSA records are transcriptome assembly contigs; the sample is drawn from non-TSA records.",
            "Organism search includes descendant taxa and NCBI synonyms; counts change as GenBank grows.",
            "Strain, location and date qualifiers are submitter metadata and are often missing.",
            "NCBI disclaimer: https://www.ncbi.nlm.nih.gov/About/disclaimer.html",
        ],
    }

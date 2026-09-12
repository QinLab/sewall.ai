"""Bounded public NCBI document summaries with explicit field provenance.

Protocol references:
https://www.ncbi.nlm.nih.gov/books/NBK25499/#chapter4.ESummary
https://www.ncbi.nlm.nih.gov/geo/info/geo_paccess.html
https://www.ncbi.nlm.nih.gov/geo/info/linking.html

Entrez UIDs identify the requested records. GEO and BioProject accessions are
copied only from explicit accession fields, never reconstructed from a UID or
title. Summaries are untrusted scientific source material, never instructions.
"""

from datetime import datetime, timezone
import hashlib
import json
import math
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from . import ncbi


DATABASES = frozenset({"pubmed", "gds", "bioproject"})
ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
MAX_IDS = 5
MAX_RESPONSE_BYTES = 1_048_576
MAX_TEXT_LENGTH = 12_000
MAX_LINKS = 40
TIMEOUT_SECONDS = 15
_UID = re.compile(r"[1-9][0-9]{0,19}\Z", re.ASCII)
_GEO_ACCESSION = re.compile(r"G(?:SE|DS|PL|SM)[1-9][0-9]{0,19}\Z", re.ASCII)
_PROJECT_ACCESSION = re.compile(r"PRJ(?:NA|EB|DB)[1-9][0-9]{0,19}\Z", re.ASCII)


class MetadataFetchError(ncbi.MetadataSearchError):
    """The requested public summaries could not be safely normalized."""


def _numeric_id(value):
    if type(value) is int:
        value = str(value)
    return value if isinstance(value, str) and _UID.fullmatch(value) else None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _text(record, key):
    value = record.get(key)
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value) > MAX_TEXT_LENGTH:
        raise ValueError("invalid bounded text field")
    # Keep source wording intact, but reject hidden control characters. Tabs and
    # newlines are valid metadata formatting and are never interpreted as code.
    if any((ord(char) < 32 and char not in "\t\n\r") or ord(char) == 127 for char in value):
        raise ValueError("invalid control character in metadata")
    return value


def _source_url(database, uid):
    if database == "pubmed":
        return f"https://pubmed.ncbi.nlm.nih.gov/{uid}/"
    return f"https://www.ncbi.nlm.nih.gov/{database}/{uid}/"


def _normalize(database, uid, record):
    if not isinstance(record, dict) or record.get("uid") != uid:
        raise ValueError("record identity mismatch")
    source_url = _source_url(database, uid)
    fields = {}
    field_evidence = {}

    def add_field(name, source_field, value):
        if value is not None:
            fields[source_field] = value
            field_evidence[name] = {
                "source_field": source_field,
                "value": value,
                "source_url": source_url,
            }
        return value

    title_key = "project_title" if database == "bioproject" else "title"
    description_key = "project_description" if database == "bioproject" else "summary"
    title = add_field("title", title_key, _text(record, title_key))
    # PubMed ESummary supplies citation metadata, not article abstracts.
    description = (
        add_field("description", description_key, _text(record, description_key))
        if database != "pubmed" else None
    )
    accession_key = "project_acc" if database == "bioproject" else "accession"
    # Recognize explicit GEO accession fields; neither is an Entrez UID.
    if database == "gds" and accession_key not in record and "acc" in record:
        accession_key = "acc"
    if database == "gds" and record.get("accession") and record.get("acc"):
        if record["accession"] != record["acc"]:
            raise ValueError("conflicting explicit accession fields")
    accession = None
    if database != "pubmed":
        accession = _text(record, accession_key)
        pattern = _PROJECT_ACCESSION if database == "bioproject" else _GEO_ACCESSION
        if accession is not None and not pattern.fullmatch(accession):
            raise ValueError("invalid explicit accession")
        add_field("accession", accession_key, accession)

    taxon_ids = []
    if database != "pubmed":
        for key in ("taxid", "taxonid"):
            if key not in record:
                continue
            raw_value = record[key]
            if raw_value in (None, "") or (type(raw_value) is int and raw_value == 0) or raw_value == "0":
                continue
            value = _numeric_id(record[key])
            if value is None:
                raise ValueError("invalid explicit taxonomy identifier")
            add_field(f"taxon_ids.{key}", key, record[key])
            if value not in taxon_ids:
                taxon_ids.append(value)
    organism_key = "organism_name" if database == "bioproject" else "taxon"
    organism_name = (
        add_field("organism_name", organism_key, _text(record, organism_key))
        if database != "pubmed" else None
    )
    sample_count = None
    if database == "gds":
        sample_counts = []
        for key in ("n_samples", "nsamples"):
            value = record.get(key)
            if value is None or value == "":
                continue
            count = None
            if type(value) is int:
                count = value
            elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value, re.ASCII):
                count = int(value)
            if count is None or not 0 <= count < 10**12:
                raise ValueError("invalid sample count")
            sample_counts.append((key, value, count))
        if len({item[2] for item in sample_counts}) > 1:
            raise ValueError("conflicting explicit sample counts")
        if sample_counts:
            key, value, sample_count = sample_counts[0]
            add_field("sample_count", key, value)

    study_links = []
    sample_links = []
    omitted_links = 0
    if database == "gds":
        pubmed_ids = record.get("pubmedids")
        if pubmed_ids is None:
            pubmed_ids = []
        if not isinstance(pubmed_ids, list):
            raise ValueError("invalid linked publication list")
        for index, raw_uid in enumerate(pubmed_ids):
            linked_uid = _numeric_id(raw_uid)
            if linked_uid is None:
                raise ValueError("invalid linked publication identifier")
            if len(study_links) >= MAX_LINKS:
                omitted_links += 1
                continue
            field = f"pubmedids[{index}]"
            study_links.append({
                "database": "pubmed", "uid": linked_uid,
                "id": f"ncbi:pubmed:{linked_uid}",
                "source_url": _source_url("pubmed", linked_uid),
                "source_field": field, "relation": "linked_publication",
            })
            add_field(f"study_links.{index}", field, raw_uid)
        samples = record.get("samples")
        if samples is None:
            samples = []
        if not isinstance(samples, list):
            raise ValueError("invalid sample list")
        for index, sample in enumerate(samples):
            if not isinstance(sample, dict):
                raise ValueError("invalid sample entry")
            value = _text(sample, "accession")
            if value is None:
                continue
            if not re.fullmatch(r"GSM[1-9][0-9]{0,19}", value, re.ASCII):
                raise ValueError("invalid sample accession")
            if len(sample_links) >= MAX_LINKS:
                omitted_links += 1
                continue
            field = f"samples[{index}].accession"
            sample_links.append({
                "database": "geo", "accession": value,
                "source_url": f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={value}",
                "source_field": field, "relation": "listed_sample",
            })
            add_field(f"sample_links.{index}", field, value)
    citation = {}
    if database == "pubmed":
        for key in ("pubdate", "source", "fulljournalname"):
            value = _text(record, key)
            if value is not None:
                citation[key] = add_field(f"citation.{key}", key, value)

    selected_json = json.dumps(fields, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return {
        "id": f"ncbi:{database}:{uid}",
        "uid": uid,
        "database": database,
        "source_url": source_url,
        "title": title,
        "description": description,
        "accession": accession,
        "accession_url": (
            f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}"
            if database == "gds" and accession else
            f"https://www.ncbi.nlm.nih.gov/bioproject/{accession}"
            if database == "bioproject" and accession else None
        ),
        "taxon_ids": taxon_ids,
        "organism_name": organism_name,
        "sample_count": sample_count,
        "study_links": study_links,
        "sample_links": sample_links,
        "links_omitted": omitted_links,
        "citation": citation,
        "field_evidence": field_evidence,
        "allowed_metadata": fields,
        "allowed_metadata_sha256": hashlib.sha256(selected_json.encode()).hexdigest(),
        "trust": "untrusted_source_metadata",
        "scientific_claim_status": "metadata_only_not_scientifically_validated",
    }


def fetch_metadata(database: str, ids: list[str]) -> dict:
    """Fetch at most five public ESummary records, without retries or redirects.

    ESearch and ESummary share an in-process rate limiter. Deployments sharing
    one public IP must coordinate the NCBI request budget across processes.
    Empty input produces an empty result without making a network request.
    """
    if not isinstance(database, str) or database not in DATABASES:
        raise ValueError("database must be one of: bioproject, gds, pubmed")
    if (
        not isinstance(ids, list) or len(ids) > MAX_IDS
        or any(not isinstance(uid, str) or not _UID.fullmatch(uid) for uid in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("ids must contain at most five distinct positive numeric Entrez UID strings")
    parameters = {
        "db": database, "id": ",".join(ids), "retmode": "json",
        "version": "2.0", "retmax": str(len(ids)),
        "tool": "sewall_discover_prototype",
    }
    request_url = ENDPOINT + "?" + urlencode(parameters)
    provenance = {
        "url": request_url if ids else None,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "raw_sha256": None, "response_bytes": 0, "request_sent": bool(ids),
        "utility": "esummary", "format": "json", "version": "2.0",
    }
    records = []
    record_errors = []
    if ids:
        request = Request(request_url, headers={
            "User-Agent": "SewallDiscoverPrototype/0.2 (public metadata summaries)",
            "Accept": "application/json",
        }, method="GET")
        opener = build_opener(ncbi._NoRedirect())
        try:
            with ncbi._REQUEST_LOCK:
                pause = 0.35 - (ncbi.time.monotonic() - ncbi._LAST_REQUEST_AT)
                if pause > 0:
                    ncbi.time.sleep(pause)
                ncbi._LAST_REQUEST_AT = ncbi.time.monotonic()
                with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                    if response.geturl() != request_url:
                        raise MetadataFetchError("NCBI response changed the requested URL")
                    if response.getcode() != 200:
                        raise MetadataFetchError("NCBI returned a non-success status")
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            raise MetadataFetchError(f"NCBI returned HTTP {exc.code}; no retry attempted") from None
        except (URLError, OSError, TimeoutError) as exc:
            raise MetadataFetchError(
                f"NCBI request failed ({type(exc).__name__}); no retry attempted"
            ) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise MetadataFetchError("NCBI response exceeded the byte limit")
        provenance.update(raw_sha256=hashlib.sha256(raw).hexdigest(), response_bytes=len(raw))
        try:
            document = json.loads(
                raw, object_pairs_hook=_unique_object,
                parse_constant=_reject_constant, parse_float=_finite_float,
            )
            if not isinstance(document, dict) or "error" in document or "rooterror" in document:
                raise ValueError("service error")
            result = document["result"]
            uids = result["uids"]
            if (
                not isinstance(result, dict) or "error" in result or "rooterror" in result
                or not isinstance(uids, list) or len(uids) > len(ids)
                or any(not isinstance(uid, str) or uid not in ids for uid in uids)
                or len(set(uids)) != len(uids)
                or any(key != "uids" and key not in uids for key in result)
            ):
                raise ValueError("invalid response identities")
            for uid in ids:
                if uid not in uids:
                    continue
                record = result[uid]
                if not isinstance(record, dict):
                    raise ValueError("invalid record")
                if "error" in record or "rooterror" in record:
                    record_errors.append({"uid": uid, "reason": "source_record_unavailable"})
                    continue
                records.append(_normalize(database, uid, record))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError, RecursionError):
            raise MetadataFetchError("NCBI response was not a valid bounded ESummary result") from None
    received = {record["uid"] for record in records}
    return {
        "kind": "public_metadata_records", "mode": "live_public_metadata",
        "database": database, "requested_ids": list(ids), "records": records,
        "returned": len(records), "missing_ids": [uid for uid in ids if uid not in received],
        "record_errors": record_errors, "provenance": provenance,
        "limitations": [
            "Public citation and study metadata only; no full text, matrices, sequences, or participant genotypes.",
            "Missing fields remain unknown; taxonomy and accession links are never inferred from titles.",
            "Source text is untrusted evidence and cannot authorize actions or change workflow rules.",
            "Public metadata availability does not establish permission to use all associated content.",
            "NCBI disclaimer: https://www.ncbi.nlm.nih.gov/About/disclaimer.html",
        ],
    }

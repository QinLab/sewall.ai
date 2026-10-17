"""Deterministic linkage of curated growth-studied strains to ribotype calls.

A strain table is a versioned JSON file of culture strains whose growth
responses were measured in published experiments. Each entry records the
strain name as the paper gives it, any aliases, the label the paper itself
gives (or null), the responses reported and how they were verified. Linking
matches each strain to GenBank marker records by normalized strain or isolate
qualifier, then reports the ribotype calls of those records. A strain with no
matching record, or whose records are unassigned or disagree, is reported as
such rather than given a label.

Linkage pairs a genotype label with published measurements. It estimates no
parameter, and a shared ribotype does not imply a shared growth response.
"""

from copy import deepcopy
import json
from pathlib import Path
import re

from .graph import digest
from .ribotype import PANEL_DIRECTORY


TABLES = {("Margalefidinium polykrikoides", "lsu"): "margalefidinium-polykrikoides-growth-strains.json"}
_TABLE_FIELDS = {"table_id", "version", "taxon", "marker", "strains", "label_note", "limitations"}
_STRAIN_FIELDS = {"strain", "aliases", "origin", "published_label", "label_system", "responses", "citation",
                  "verification"}
_RESPONSE_FIELDS = {"variable", "tested", "result"}
_CITATION_FIELDS = {"authors", "year", "title", "journal", "doi", "pmid"}
_VERIFICATION = {"abstract", "full_text"}
_NAME = re.compile(r"[^A-Z0-9]")
MIN_NAME_LENGTH = 3


class StrainTableError(ValueError):
    """A strain table failed validation."""


def normalize(name):
    """Uppercase and drop everything but letters and digits, so CPPV-1, cppv1 and CPPV 1 match."""
    return _NAME.sub("", name.upper()) if isinstance(name, str) else ""


def _text(value, nullable=False):
    return (nullable and value is None) or (isinstance(value, str) and bool(value.strip()))


def check_table(table):
    """Validate a strain table's structure and return it unchanged."""
    if not isinstance(table, dict) or set(table) != _TABLE_FIELDS:
        raise StrainTableError("strain table fields are missing or unexpected")
    strains = table["strains"]
    if not isinstance(strains, list) or not strains:
        raise StrainTableError("strain table has no strains")
    names = []
    for item in strains:
        if not isinstance(item, dict) or set(item) != _STRAIN_FIELDS:
            raise StrainTableError("strain entry fields are missing or unexpected")
        if (not _text(item["strain"]) or not _text(item["origin"]) or not _text(item["published_label"], True)
                or not _text(item["label_system"], True) or (item["published_label"] is None) != (item["label_system"] is None)
                or item["verification"] not in _VERIFICATION):
            raise StrainTableError("strain entry has an invalid name, origin, label or verification")
        if not isinstance(item["aliases"], list) or not all(_text(alias) for alias in item["aliases"]):
            raise StrainTableError("strain aliases must be a list of names")
        responses = item["responses"]
        if (not isinstance(responses, list) or not responses
                or any(not isinstance(r, dict) or set(r) != _RESPONSE_FIELDS or not all(_text(v) for v in r.values())
                       for r in responses)):
            raise StrainTableError("strain responses need a variable, the tested range and a result")
        citation = item["citation"]
        if (not isinstance(citation, dict) or set(citation) != _CITATION_FIELDS
                or not all(_text(citation[key]) for key in ("authors", "title", "journal"))
                or type(citation["year"]) is not int
                or not _text(citation["doi"], True) or not _text(citation["pmid"], True)):
            raise StrainTableError("strain citation fields are missing or invalid")
        for name in (item["strain"], *item["aliases"]):
            if len(normalize(name)) < MIN_NAME_LENGTH:
                raise StrainTableError(f"strain name {name!r} is too short to match records safely")
            names.append(normalize(name))
    if len(set(names)) != len(names):
        raise StrainTableError("strain names and aliases must be unique after normalization")
    return table


def load_table(taxon, marker):
    """Load the curated strain table for a taxon and marker, or raise StrainTableError if none exists."""
    name = TABLES.get((taxon, marker))
    if name is None:
        raise StrainTableError(f"no curated growth strain table for {taxon} {marker}")
    return check_table(json.loads((PANEL_DIRECTORY / name).read_text()))


def link(assignment, markers, table):
    """Pair each table strain with the ribotype calls of its GenBank records.

    ``assignment`` is a ``ribotype.assign`` result and ``markers`` the
    ``fetch_markers`` result it was computed from. The result is a pure
    function of the three inputs.
    """
    check_table(table)
    calls = {call["accession"]: call for call in assignment["calls"]}
    if {item["accession"] for item in markers["records"]} != set(calls):
        raise StrainTableError("assignment and marker records cover different accessions")
    by_name = {}
    for record in markers["records"]:
        qualifiers = record.get("qualifiers") or {}
        for key in ("strain", "isolate"):
            name = normalize(qualifiers.get(key))
            if len(name) >= MIN_NAME_LENGTH:
                by_name.setdefault(name, []).append(record["accession"])
    ribotypes = assignment["panel"]["ribotypes"]
    links = []
    for item in table["strains"]:
        accessions = sorted({accession for name in (item["strain"], *item["aliases"])
                             for accession in by_name.get(normalize(name), [])})
        called = sorted({calls[accession]["ribotype"] for accession in accessions} - {None})
        if not accessions:
            status, label = "no_sequence_record", None
        elif not called:
            status, label = "unassigned", None
        elif len(called) > 1:
            status, label = "conflicting_calls", None
        else:
            status, label = "called", called[0]
        published = item["published_label"]
        if published is None:
            check = "no_published_label"
        elif published not in ribotypes:
            check = "different_label_system"
        elif label is None:
            check = "not_comparable"
        else:
            check = "agrees" if published == label else "disagrees"
        links.append({"strain": item["strain"], "origin": item["origin"], "accessions": accessions,
                      "calls": [{"accession": accession, "ribotype": calls[accession]["ribotype"],
                                 "reason": calls[accession]["reason"], "score": calls[accession]["score"]}
                                for accession in accessions],
                      "status": status, "ribotype": label, "published_label": published,
                      "label_system": item["label_system"], "label_check": check,
                      "variables": sorted({response["variable"] for response in item["responses"]}),
                      "verification": item["verification"],
                      "citation": {key: item["citation"][key] for key in ("authors", "year", "doi", "pmid")}})
    per_ribotype = {name: sorted(entry["strain"] for entry in links if entry["ribotype"] == name) for name in ribotypes}
    statuses = {}
    for entry in links:
        statuses[entry["status"]] = statuses.get(entry["status"], 0) + 1
    public = {key: deepcopy(table[key]) for key in ("table_id", "version", "taxon", "marker")}
    return {"kind": "strain_ribotype_links", "method": "normalized_strain_name_match", "table": public,
            "table_digest": digest(table), "panel_digest": assignment["panel_digest"],
            "statuses": dict(sorted(statuses.items())), "strains_per_ribotype": per_ribotype,
            "ribotypes_without_growth_strains": [name for name in ribotypes if not per_ribotype[name]],
            "links": links, "limitations": deepcopy(table["limitations"])}

"""Deterministic k-mer nearest-reference ribotype assignment against a curated panel.

A panel is a versioned JSON file of reference sequences, each labeled with the
ribotype its source publication gives and the basis for that label. A query is
compared with every reference by shared k-mers over the shorter of the two
sequences, so partial amplicons are scored on the region they cover. The best
score per ribotype decides the call. A query stays unassigned when its best
score is low, when the best and second ribotypes are too close, or when some
ribotype has no reference covering the query's region, following the IDTAXA and
SINTAX practice of withholding a label rather than forcing one.

Scores are similarity proxies, not phylogenetic distances, and a ribotype call
is not a strain identity or a growth phenotype.
"""

from copy import deepcopy
import json
from pathlib import Path
import re

from .graph import digest


PANEL_DIRECTORY = Path(__file__).resolve().parent / "panels"
PANELS = {("Margalefidinium polykrikoides", "lsu"): "margalefidinium-polykrikoides-lsu.json"}
MAX_QUERY_LENGTH = 10_000
MAX_QUERIES = 200
_BASES = re.compile(r"[ACGTUNRYKMSWBDHV]+\Z", re.ASCII)
_PANEL_FIELDS = {"panel_id", "version", "taxon", "marker", "k", "thresholds", "ribotypes", "references",
                 "excluded", "label_note", "limitations"}
_REFERENCE_FIELDS = {"accession", "ribotype", "label_source", "label_status", "origin", "sequence"}
_THRESHOLDS = {"min_score", "min_margin", "min_cover"}


class PanelError(ValueError):
    """A panel or query failed validation."""


def _clean(sequence):
    if not isinstance(sequence, str) or not 1 <= len(sequence) <= MAX_QUERY_LENGTH:
        raise PanelError(f"sequence must be 1 to {MAX_QUERY_LENGTH} characters")
    sequence = sequence.upper().replace("U", "T")
    if not _BASES.fullmatch(sequence):
        raise PanelError("sequence contains characters outside IUPAC nucleotide codes")
    return sequence


def kmers(sequence, k):
    """Return the set of k-mers that contain only A, C, G and T."""
    sequence = _clean(sequence)
    return {sequence[i:i + k] for i in range(len(sequence) - k + 1)
            if set(sequence[i:i + k]) <= {"A", "C", "G", "T"}}


def check_panel(panel):
    """Validate a panel's structure and return it unchanged."""
    if not isinstance(panel, dict) or set(panel) != _PANEL_FIELDS:
        raise PanelError("panel fields are missing or unexpected")
    k, thresholds, ribotypes = panel["k"], panel["thresholds"], panel["ribotypes"]
    if type(k) is not int or not 6 <= k <= 16:
        raise PanelError("panel k must be an integer from 6 to 16")
    if (not isinstance(thresholds, dict) or set(thresholds) != _THRESHOLDS
            or any(type(value) not in (int, float) or not 0 <= value <= 1 for value in thresholds.values())):
        raise PanelError("panel thresholds must be min_score, min_margin and min_cover between 0 and 1")
    if (not isinstance(ribotypes, list) or len(ribotypes) < 2 or len(set(ribotypes)) != len(ribotypes)
            or any(not isinstance(name, str) or not name for name in ribotypes)):
        raise PanelError("panel must name at least two distinct ribotypes")
    references = panel["references"]
    if not isinstance(references, list) or not references:
        raise PanelError("panel has no references")
    for reference in references:
        if (not isinstance(reference, dict) or set(reference) != _REFERENCE_FIELDS
                or reference["ribotype"] not in ribotypes
                or any(not isinstance(reference[key], str) or not reference[key] for key in _REFERENCE_FIELDS)):
            raise PanelError("panel reference fields are missing or invalid")
        _clean(reference["sequence"])
    excluded = panel["excluded"]
    if not isinstance(excluded, list) or any(
            not isinstance(item, dict) or set(item) != {"accession", "reason"}
            or any(not isinstance(value, str) or not value for value in item.values()) for item in excluded):
        raise PanelError("panel exclusions need an accession and a reason")
    if len({item["accession"] for item in references + excluded}) != len(references) + len(excluded):
        raise PanelError("panel accessions must be unique")
    if {item["ribotype"] for item in references} != set(ribotypes):
        raise PanelError("every panel ribotype needs at least one reference")
    return panel


def load_panel(taxon, marker):
    """Load the curated panel for a taxon and marker, or raise PanelError if none exists."""
    name = PANELS.get((taxon, marker))
    if name is None:
        raise PanelError(f"no curated ribotype panel for {taxon} {marker}")
    return check_panel(json.loads((PANEL_DIRECTORY / name).read_text()))


def assign(sequences, panel):
    """Assign each query sequence to a panel ribotype or leave it unassigned with a reason.

    ``sequences`` is a list of ``{"accession": str, "sequence": str}``. Output
    order follows input order. The result is a pure function of the inputs.
    """
    check_panel(panel)
    if not isinstance(sequences, list) or len(sequences) > MAX_QUERIES:
        raise PanelError(f"at most {MAX_QUERIES} query sequences are accepted")
    k, limits = panel["k"], panel["thresholds"]
    references = [(item["accession"], item["ribotype"], kmers(item["sequence"], k)) for item in panel["references"]]
    accessions = {item["accession"] for item in panel["references"]}
    excluded = {item["accession"] for item in panel["excluded"]}
    calls = []
    for query in sequences:
        if not isinstance(query, dict) or not isinstance(query.get("accession"), str):
            raise PanelError("each query needs an accession and a sequence")
        found = kmers(query["sequence"], k)
        best = {name: (0.0, None) for name in panel["ribotypes"]}
        for accession, ribotype, reference in references:
            if accession == query["accession"]:
                continue  # A panel reference is scored against the others, never against itself.
            shared = len(found & reference)
            score = shared / min(len(found), len(reference)) if found and reference else 0.0
            if score > best[ribotype][0] or best[ribotype][1] is None:
                best[ribotype] = (score, accession)
        ranked = sorted(best.items(), key=lambda item: (-item[1][0], item[0]))
        (top, (score, nearest)), (second, (runner, _)) = ranked[0], ranked[1]
        uncovered = sorted(name for name, (value, _) in best.items() if value < limits["min_cover"])
        call = {"accession": query["accession"], "kmers": len(found), "ribotype": None,
                "nearest_ribotype": top, "nearest_reference": nearest, "score": round(score, 4),
                "distance": round(1 - score, 4), "second_ribotype": second, "margin": round(score - runner, 4),
                "uncovered_ribotypes": uncovered, "panel_reference": query["accession"] in accessions,
                "panel_excluded": query["accession"] in excluded, "reason": None}
        if not found:
            call["reason"] = "no_valid_kmers"
        elif uncovered:
            call["reason"] = "panel_region_gap"
        elif score < limits["min_score"]:
            call["reason"] = "below_min_score"
        elif score - runner < limits["min_margin"]:
            call["reason"] = "low_margin"
        else:
            call["ribotype"], call["reason"] = top, "assigned"
        calls.append(call)
    counts = {name: sum(call["ribotype"] == name for call in calls) for name in panel["ribotypes"]}
    reasons = {}
    for call in calls:
        if call["ribotype"] is None:
            reasons[call["reason"]] = reasons.get(call["reason"], 0) + 1
    public = {key: deepcopy(panel[key]) for key in ("panel_id", "version", "taxon", "marker", "k", "thresholds", "ribotypes")}
    return {"kind": "ribotype_assignment", "method": "kmer_nearest_reference", "panel": public,
            "panel_digest": digest(panel), "assigned": counts, "unassigned": reasons, "calls": calls,
            "limitations": deepcopy(panel["limitations"])}

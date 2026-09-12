"""Invented metadata and policies for software tests, never scientific evidence.

Provider names indicate intended adapter roles, not actual provider terms or records.
No sequences, participant data, measurements, or real accessions are included.
"""

from copy import deepcopy

SOURCES = {
    "eol": {"label": "Encyclopedia of Life: trait context", "scale": "organism"},
    "ncbi_genotype": {"label": "NCBI: genotype study metadata", "scale": "molecular"},
    "geo": {"label": "GEO: expression study metadata", "scale": "molecular"},
    "alphaearth": {"label": "AlphaEarth: annual landscape context", "scale": "landscape"},
    "pubmed": {"label": "PubMed: literature context", "scale": "literature"},
}

SCENARIOS = ("coastal", "missing-link", "policy-review", "policy-denied",
             "source-failure", "future-leakage")
FOCI = {
    "cross-scale": list(SOURCES),
    "traits": ["eol", "pubmed"],
    "molecular": ["ncbi_genotype", "geo", "pubmed"],
    "landscape": ["eol", "alphaearth", "pubmed"],
}
DEFAULT_QUESTION = (
    "Which existing coastal plant records could support linking genotype and expression "
    "evidence to organismal traits and historical habitat context?"
)


def fixture_source(source_id, scenario="coastal"):
    if source_id not in SOURCES or scenario not in SCENARIOS:
        raise ValueError("Unknown fixture source or scenario")
    record = {
        "record_id": "demo:" + source_id,
        "taxon_id": "demo:coastal-plant",
        "specimen_id": "demo:specimen-1" if source_id in ("geo", "ncbi_genotype") else None,
        "site_id": "demo:coastal-site-1" if source_id in ("geo", "alphaearth") else None,
        "period_end": "2020-12-31",
        "available_at": "2021-06-01",
        "forecast_cutoff": "2022-01-01",
        "evidence": {"source": "synthetic_fixture", "method": "explicit-field",
                     "span": "Invented coastal plant metadata for software testing only."},
        "origin": "synthetic_fixture",
        "source_uri": "fixture://" + source_id,
    }
    profile = {
        "policy_id": "demo:policy:" + source_id, "version": "0.1.0",
        "allowed_purposes": ["metadata_research"], "prohibited_purposes": [],
        "allowed_operations": ["metadata"], "compute_locations": ["source"],
        "egress": ["metadata"], "max_bytes": 10000, "max_retention_days": 30,
        "attribution": [source_id + ":synthetic_fixture"],
        "authorization_required": False, "negotiation_allowed": True,
    }
    request = {"purpose": "metadata_research", "operation": "metadata",
               "compute_location": "source", "output": "metadata",
               "estimated_bytes": 1024, "retention_days": 7,
               "attribution": [source_id + ":synthetic_fixture"]}
    if scenario == "missing-link" and source_id == "geo":
        record["specimen_id"] = None
    if scenario == "policy-review" and source_id == "ncbi_genotype":
        profile["authorization_required"] = True
    if scenario == "policy-denied" and source_id == "ncbi_genotype":
        profile["prohibited_purposes"] = ["metadata_research"]
    if scenario == "future-leakage" and source_id == "alphaearth":
        record["period_end"] = "2022-12-31"
        record["available_at"] = "2023-06-01"
    return deepcopy({"record": record, "profile": profile, "request": request,
                     "unavailable": scenario == "source-failure" and source_id == "geo"})

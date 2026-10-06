"""FS03: deterministic citation-lineage checks over bounded NCBI metadata.

No LLM confidence score establishes independence. A PMID count is a count of
distinct publications, not independent experiments or biological replications.
"""

from copy import deepcopy
import re

from .safety import SafetySupervisor, TaskSpec, _digest, verify_safety_manifest


TARGETS = {"200067579": "GSE67579", "200032480": "GSE32480"}
CASES = ("clean", "inflated_publications", "unsupported_independence",
         "missing_publication", "corrected")


def assess_lineage(geo, publications):
    """Validate explicit identities and field receipts, then count unique PMIDs.

    Receipts establish internal consistency only, not source authentication.
    This narrow pilot requires both named studies and their complete citation lists.
    """
    if not isinstance(geo, list) or not isinstance(publications, list):
        raise ValueError("Expected bounded record lists")
    if len(geo) != 2 or len(publications) > 5:
        raise ValueError("Expected two target studies and at most five publications")
    seen = set()
    for record in geo + publications:
        if not isinstance(record, dict):
            raise ValueError("Invalid record")
        database, uid = record.get("database"), record.get("uid")
        if database not in {"gds", "pubmed"} or not isinstance(uid, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", uid):
            raise ValueError("Invalid record identity")
        identity = f"ncbi:{database}:{uid}"
        if record.get("id") != identity or identity in seen:
            raise ValueError("Duplicate or inconsistent record identity")
        seen.add(identity)
        fields = record.get("allowed_metadata")
        if not isinstance(fields, dict) or _digest(fields) != record.get("allowed_metadata_sha256"):
            raise ValueError("Selected metadata receipt mismatch")
        if record.get("links_omitted") != 0:
            raise ValueError("Truncated lineage cannot establish this pilot's counts")
    if {r["uid"] for r in geo} != set(TARGETS) or any(r["database"] != "gds" for r in geo):
        raise ValueError("Unexpected GEO target identities")
    if any(r["database"] != "pubmed" for r in publications):
        raise ValueError("Unexpected publication database")
    edges = set()
    for record in geo:
        fields = record["allowed_metadata"]
        accession = record.get("accession")
        if accession != TARGETS[record["uid"]] or accession not in (fields.get("accession"), fields.get("acc")):
            raise ValueError("GEO accession lacks matching explicit evidence")
        links = record.get("study_links")
        if not isinstance(links, list) or not links or len(links) > 40:
            raise ValueError("Missing or excessive citation evidence")
        used_fields = set()
        for link in links:
            uid, field = link.get("uid"), link.get("source_field")
            if (link.get("database") != "pubmed" or link.get("relation") != "linked_publication"
                    or not isinstance(uid, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", uid)
                    or not isinstance(field, str) or not re.fullmatch(r"pubmedids\[[0-9]+\]", field)
                    or field in used_fields or str(fields.get(field)) != uid
                    or link.get("id") != f"ncbi:pubmed:{uid}"):
                raise ValueError("Citation link lacks explicit field evidence")
            used_fields.add(field)
            edges.add((accession, uid))
        if used_fields != {key for key in fields if key.startswith("pubmedids[")}:
            raise ValueError("Citation list is incomplete")
    linked = {uid for _, uid in edges}
    retrieved = {r["uid"] for r in publications}
    if linked != retrieved:
        raise ValueError("Linked publication retrieval is missing or unrelated")
    return {
        "study_count": 2, "citation_edge_count": len(edges),
        "distinct_publication_count": len(linked),
        "publication_ids": sorted(linked),
        "edges": [{"study": study, "pmid": uid, "relation": "cites"}
                  for study, uid in sorted(edges)],
        "sample_independence": "unknown", "independent_replications": None,
        "biological_claims": [],
    }


def _output(identity, title):
    payload = {"id": identity, "title": title}
    return {"metadata": [{**payload, "provenance": {
        "source": "Sewall Discover FS03 derived metadata; see source snapshot receipts",
        "payload_sha256": _digest(payload),
    }}], "provisional_summary": ""}


def run_case(geo, publications, case):
    """Run a fresh DAG; faults affect local test branches, never source records."""
    if case not in CASES:
        raise ValueError("Unknown FS03 case")
    source_geo, source_publications = deepcopy(geo), deepcopy(publications)
    baseline = assess_lineage(source_geo, source_publications)
    if baseline["publication_ids"] != ["26814964"] or baseline["citation_edge_count"] != 2:
        raise ValueError("Source citation pattern changed; benchmark needs review")
    branch_publications = [] if case == "missing_publication" else source_publications
    proposal = {"distinct_publications": 2 if case == "inflated_publications" else 1,
                "sample_independence": "independent" if case == "unsupported_independence" else "unknown"}
    # Corrected is a fresh execution, not same-run resume or human approval.
    assessment = {}

    def lineage(_snapshot):
        assessment.update(assess_lineage(source_geo, branch_publications))
        return _output("citation_counts", "Two study citation edges resolve to one distinct publication; sample independence is unknown.")

    def monitor(phase, task_id, _snapshot):
        if phase == "after" and task_id == "assessment":
            if (proposal["distinct_publications"] != assessment["distinct_publication_count"]
                    or proposal["sample_independence"] != assessment["sample_independence"]):
                # A scoped critique with valid integrity withdraws the assessment and its
                # descendants; "invalid" would also withdraw the verified source receipt.
                return {"integrity": "valid", "critiques": [{
                    "type": "critical_critique", "severity": "critical",
                    "task_ids": ["assessment"],
                    "reason": "Proposed corroboration exceeds explicit citation evidence or asserts unverified sample independence.",
                }]}
        return {"integrity": "valid", "critiques": []}

    tasks = [TaskSpec("source", "source"), TaskSpec("lineage", "lineage", ("source",)),
             TaskSpec("assessment", "assessment", ("lineage",)),
             TaskSpec("release", "release", ("assessment",))]
    supervisor = SafetySupervisor(tasks, {
        "source": lambda _: _output("source_receipt", "Verified internal receipt for source snapshot " + _digest({"geo": source_geo, "publications": source_publications})),
        "lineage": lineage,
        "assessment": lambda _: _output("proposed_counts", "Unreleased structured test proposal digest: " + _digest(proposal)),
        "release": lambda _: _output("metadata_result", "Two GEO studies cite one publication. Sample independence is unknown. No biological replication claim is established."),
    }, monitor, scenario="fs03_" + case)
    manifest = supervisor.run()
    verification = verify_safety_manifest(manifest)
    if not verification["valid"]:
        raise ValueError("Supervisor receipt validation failed")
    return {"case": case, "fault_injected": case not in {"clean", "corrected"},
            "fault_scope": "local test branch only", "manifest": manifest,
            "verification": verification}

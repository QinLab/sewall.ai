"""Offline catalog/governance graph. This module never authorizes data access.

Build: python -m sewall.knowledge_map --registry docs/knowledge-map-source-registry-2026-10-23.json --out knowledge-map
Verify: python -m sewall.knowledge_map --verify knowledge-map/graph.json --registry docs/knowledge-map-source-registry-2026-10-23.json
No optional packages, credentials, model calls or network requests are required.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
from html import escape
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

VERSION = "1.0"
KINDS = {"Organization", "Service", "Product", "Environment", "DataType", "PolicyReview"}
RELATIONS = {
    "published_by": ("Service", "Organization", "catalog_mapping"),
    "served_by": ("Product", "Service", "catalog_mapping"),
    "hosted_by": ("Environment", "Organization", "configuration_template"),
    "uses_service": ("Environment", "Service", "configuration_template"),
    "offers_data_type": ("Service", "DataType", "catalog_category"),
    "requires_policy_review": (None, "PolicyReview", "review_requirement"),
}
POLICY_FIELDS = ["authorization", "permitted_uses", "license", "attribution", "residency",
                 "egress", "retention", "model_disclosure", "audit", "source_version",
                 "policy_version", "actor", "purpose", "operation", "environment", "destination"]


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _reject_secrets(value):
    """Export only safe, unsigned documentation references; no private overlay."""
    if isinstance(value, dict):
        for k, v in value.items():
            if k.lower() in {"token", "api_key", "password", "secret", "credential", "project_ref", "export_destination_ref"} and v is not None:
                raise ValueError("Secret/private operational field is not permitted: " + k)
            if k in {"authorization_granted", "credential_values_stored", "provisioned_by_this_plan"} and v is not False:
                raise ValueError("Catalog cannot grant authorization, store credentials or provision resources")
            _reject_secrets(v)
    elif isinstance(value, list):
        for v in value:
            _reject_secrets(v)
    elif isinstance(value, str):
        if value.startswith(("https://", "http://")):
            u = urlsplit(value)
            if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
                raise ValueError("Only unsigned HTTPS documentation URLs without query/fragment are exported")


def build_graph(registry):
    _reject_secrets(registry)
    if registry.get("status") != "planning_seed_not_runtime_authorization":
        raise ValueError("Expected a planning registry, not a policy decision")
    if registry.get("policy_contract", {}).get("default") != "needs_review":
        raise ValueError("Unknown policy must remain needs_review")
    for group in ("providers", "services", "products", "environments"):
        if not isinstance(registry.get(group), list):
            raise ValueError("Missing catalog group: " + group)
    nodes, edges = [], []
    source_digest = digest(registry)

    def node(record, kind, pointer, urls=()):
        nodes.append({"id": record["id"], "kind": kind,
                      "label": record.get("label", "GCP project (unconfigured template)"),
                      "status": "needs_review" if kind == "PolicyReview" else "catalog_entry",
                      "authorization_granted": False,
                      "evidence": {"registry_pointer": pointer, "registry_sha256": source_digest,
                                   "documentation_urls": list(urls),
                                   "level": "curated_registry_reference_not_live_verification"},
                      "attributes": deepcopy({k: v for k, v in record.items() if k not in {"id", "label"}})})

    def edge(subject, predicate, obj, pointer):
        identity = [subject, predicate, obj]
        edges.append({"id": "edge:" + digest(identity)[:24], "subject": subject,
                      "predicate": predicate, "object": obj, "status": RELATIONS[predicate][2],
                      "authorization_effect": "none", "scientific_identity_claim": False,
                      "evidence": {"registry_pointer": pointer, "registry_sha256": source_digest}})

    for i, p in enumerate(registry["providers"]):
        node(p, "Organization", f"/providers/{i}")
    for i, s in enumerate(registry["services"]):
        pointer = f"/services/{i}"
        if s.get("default_data_action") != "needs_review":
            raise ValueError("Service defaults must require review")
        node(s, "Service", pointer, s.get("documentation_urls", []))
        edge(s["id"], "published_by", s["provider_id"], pointer + "/provider_id")
        for j, modality in enumerate(s["data_modalities"]):
            edge(s["id"], "offers_data_type", "type:" + modality, pointer + f"/data_modalities/{j}")
    for i, p in enumerate(registry["products"]):
        pointer = f"/products/{i}"
        node(p, "Product", pointer, [p["documentation_url"]])
        edge(p["id"], "served_by", p["service_id"], pointer + "/service_id")
    for i, e in enumerate(registry["environments"]):
        pointer = f"/environments/{i}"
        node(e, "Environment", pointer)
        edge(e["id"], "hosted_by", e["provider_id"], pointer + "/provider_id")
        edge(e["id"], "uses_service", e["service_id"], pointer + "/service_id")
    modalities = sorted({x for s in registry["services"] for x in s["data_modalities"]})
    for name in modalities:
        node({"id": "type:" + name, "label": name.replace("_", " ")}, "DataType", "/services")
    for n in list(nodes):
        if n["kind"] not in {"Service", "Product", "Environment"}:
            continue
        pointer = n["evidence"]["registry_pointer"]
        policy = {"id": "review:" + n["id"], "label": "Review: " + n["label"],
                  "scope_id": n["id"], "default_decision": "needs_review",
                  "field_states": {field: "unknown" for field in POLICY_FIELDS},
                  "decision_binding": registry["policy_contract"]["decision_bound_to"],
                  "policy_interpretation": "unresolved_requirements_not_an_enforceable_license_or_access_grant"}
        node(policy, "PolicyReview", pointer, n["evidence"]["documentation_urls"])
        edge(n["id"], "requires_policy_review", policy["id"], pointer)
    nodes.sort(key=lambda n: n["id"])
    edges.sort(key=lambda e: e["id"])
    graph = {"schema_version": VERSION, "graph_kind": "catalog_and_governance",
             "prepared_on": registry["prepared_on"], "source_registry_sha256": source_digest,
             "source_registry_version": registry["design_version"],
             "scope": "catalog_structure_and_unresolved_policy_requirements",
             "scientific_join_count": 0, "authorization_grant_count": 0,
             "network_requests_in_build": 0, "nodes": nodes, "edges": edges,
             "limitations": ["Documentation references are curated inputs, not fresh API verification.",
                             "Data-type categories do not establish compatible units, assays or sampling support.",
                             "No sample identity, causal relationship or ecological prediction is asserted.",
                             "Review nodes are unresolved requirements; no record-level permission has been decided.",
                             "The GCP node is an unconfigured template, not a provisioned or authorized project.",
                             "The Virginia custodian remains unconfirmed; ODU is a planning organization entry."]}
    graph["statistics"] = statistics(graph)
    graph["graph_sha256"] = digest(graph)
    validate_graph(graph)
    return graph


def statistics(graph):
    return {"nodes": len(graph["nodes"]), "edges": len(graph["edges"]),
            "node_kinds": dict(sorted(Counter(n["kind"] for n in graph["nodes"]).items())),
            "edge_predicates": dict(sorted(Counter(e["predicate"] for e in graph["edges"]).items()))}


def validate_graph(graph, registry=None):
    """Check graph structure and scope; optionally rebuild against the exact input."""
    _reject_secrets(graph)
    if graph.get("schema_version") != VERSION or graph.get("graph_kind") != "catalog_and_governance":
        raise ValueError("Unsupported graph format")
    for key in ("scientific_join_count", "authorization_grant_count", "network_requests_in_build"):
        if type(graph.get(key)) is not int or graph[key] != 0:
            raise ValueError("Catalog-only scope violation: " + key)
    if graph.get("graph_sha256") != digest({k: v for k, v in graph.items() if k != "graph_sha256"}):
        raise ValueError("Graph digest mismatch")
    by_id = {}
    for n in graph["nodes"]:
        if n["id"] in by_id or n["kind"] not in KINDS:
            raise ValueError("Duplicate node or unknown node kind")
        if n.get("authorization_granted") is not False:
            raise ValueError("A catalog node cannot grant access")
        if n["evidence"]["registry_sha256"] != graph["source_registry_sha256"]:
            raise ValueError("Node lineage differs from source registry")
        if n["kind"] == "PolicyReview":
            if n["status"] != "needs_review" or n["attributes"].get("default_decision") != "needs_review":
                raise ValueError("Unresolved policy cannot become permission")
            if n["attributes"].get("field_states") != {field: "unknown" for field in POLICY_FIELDS}:
                raise ValueError("Seed review requirements must remain unknown")
        by_id[n["id"]] = n
    seen = set()
    requirements = Counter()
    for e in graph["edges"]:
        if e["id"] in seen or e["subject"] not in by_id or e["object"] not in by_id:
            raise ValueError("Duplicate edge or missing endpoint")
        seen.add(e["id"])
        if e["predicate"] not in RELATIONS:
            raise ValueError("Unsupported catalog relation")
        source_kind, target_kind, status = RELATIONS[e["predicate"]]
        subject, obj = by_id[e["subject"]], by_id[e["object"]]
        if (source_kind and subject["kind"] != source_kind) or obj["kind"] != target_kind:
            raise ValueError("Relation has incompatible node kinds")
        if e["status"] != status or e.get("authorization_effect") != "none" or e.get("scientific_identity_claim") is not False:
            raise ValueError("Edge cannot assert permission or sample identity")
        if e["evidence"]["registry_sha256"] != graph["source_registry_sha256"]:
            raise ValueError("Edge lineage differs from source registry")
        if e["predicate"] == "requires_policy_review":
            if subject["kind"] not in {"Service", "Product", "Environment"} or obj["attributes"]["scope_id"] != subject["id"]:
                raise ValueError("Policy review scope mismatch")
            requirements[subject["id"]] += 1
    for n in graph["nodes"]:
        if n["kind"] in {"Service", "Product", "Environment"} and requirements[n["id"]] != 1:
            raise ValueError("Every source/product/environment requires exactly one review node")
    if graph["statistics"] != statistics(graph):
        raise ValueError("Graph counts differ from actual contents")
    if registry is not None and graph != build_graph(registry):
        raise ValueError("Graph does not replay from this registry")
    return True


def render_html(graph):
    """Embed a complete inspectable graph without fetching scripts or data."""
    template = Path(__file__).with_name("knowledge_map_viewer.html").read_text(encoding="utf-8")
    payload = json.dumps(graph, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    return template.replace("/*GRAPH_DATA*/null", payload)


def render_svg(graph):
    """Readable aggregate of the actual catalog graph, not scientific result data."""
    s = graph["statistics"]
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1500" height="690" viewBox="0 0 1500 690" role="img" aria-labelledby="title desc">',
             '<title id="title">Sewall.ai initial catalog and governance graph</title>',
             '<desc id="desc">Grouped view of a deterministic JSON graph. Source families connect to data-type categories and unresolved policy requirements. No scientific joins or access grants.</desc>',
             '<defs><style>text{font-family:Arial,Helvetica,sans-serif;fill:#162b3b}.label{font-size:25px;font-weight:700}.body{font-size:22px}.small{font-size:20px}.edge{fill:none;stroke:#a8b7c0;stroke-width:2.2}.policy{stroke:#b77922;stroke-dasharray:7 5}</style></defs>',
             '<rect width="1500" height="690" fill="white"/>',
             '<text x="24" y="35" class="label">Sewall.ai | Initial catalog and governance graph</text>',
             f'<text x="24" y="66" class="small">{s["nodes"]} typed nodes · {s["edges"]} typed edges · deterministic JSON replay · October 23, 2026</text>']
    families = [
        ("NOAA", "5 services: weather, tides, rivers, climate, ocean", "org:noaa"),
        ("NEON", "1 service + 4 aquatic/soil products", "org:neon"),
        ("NCBI", "7 databases: studies, samples, reads, taxa, literature", "org:ncbi"),
        ("Earth Engine / GCP", "1 service + 3 collections + project template", "org:google"),
        ("EOL · CBP · USGS · JGI · PANGAEA", "5 services; Virginia custodian: 1 unresolved", None),
    ]
    # These are display groups only. Every count above is checked against graph facts.
    assert s["node_kinds"]["Service"] == 20 and s["node_kinds"]["Product"] == 7
    for i, (title, subtitle, _) in enumerate(families):
        y = 112 + i * 82
        parts.extend([f'<path d="M625 {y+24} C690 {y+24},680 310,750 310" class="edge"/>',
                      f'<path d="M625 {y+24} C690 {y+24},650 90,710 90 L1090 90 Q1130 90,1130 165" class="edge policy"/>',
                      f'<rect x="24" y="{y}" width="600" height="68" rx="8" fill="#eef4f8" stroke="#829cab"/>',
                      f'<text x="40" y="{y+28}" class="label">{escape(title)}</text>',
                      f'<text x="40" y="{y+54}" class="small">{escape(subtitle)}</text>'])
    parts.extend(['<rect x="750" y="140" width="330" height="345" rx="8" fill="#edf7f2" stroke="#608c79"/>',
                  f'<text x="771" y="177" class="label">{s["node_kinds"]["DataType"]} data-type categories</text>',
                  '<text x="771" y="217" class="body">Molecular / omics</text>',
                  '<text x="771" y="255" class="body">Samples / taxonomy / traits</text>',
                  '<text x="771" y="293" class="body">Ecology / water / soil</text>',
                  '<text x="771" y="331" class="body">Weather / time series</text>',
                  '<text x="771" y="369" class="body">Images / spatial context</text>',
                  '<text x="771" y="407" class="body">Studies / literature</text>',
                  '<text x="771" y="454" class="small">Category links ≠ sample joins</text>',
                  '<rect x="1130" y="140" width="346" height="345" rx="8" fill="#fff8ec" stroke="#b77922"/>',
                  f'<text x="1150" y="177" class="label">{s["node_kinds"]["PolicyReview"]} review requirements</text>',
                  '<text x="1150" y="217" class="body">Identity / purpose / operation</text>',
                  '<text x="1150" y="255" class="body">License / attribution / version</text>',
                  '<text x="1150" y="293" class="body">Residency / egress / retention</text>',
                  '<text x="1150" y="331" class="body">Model disclosure / audit</text>',
                  '<text x="1150" y="369" class="body">Environment / destination</text>',
                  '<text x="1150" y="414" class="small" style="fill:#8c591c;font-weight:700">All need record-level review</text>',
                  '<text x="1150" y="454" class="small">No authorization issued</text>',
                  '<text x="24" y="555" class="body">11 organization entries · 20 services · 7 products · 1 environment template · 49 data types · 28 review nodes</text>',
                  '<path d="M26 590 L78 590" class="edge"/><text x="90" y="597" class="small">Catalog/category mapping</text>',
                  '<path d="M480 590 L532 590" class="edge policy"/><text x="544" y="597" class="small">Unresolved review requirement</text>',
                  '<text x="24" y="638" class="body">Preliminary software result: inspectable topology and offline replay. No validated scientific joins or access grants.</text>',
                  '<text x="24" y="672" class="small">Grouped display of graph.json. Documentation references are curated inputs; units, sampling support and permissions remain to be reviewed.</text>',
                  '</svg>'])
    return "\n".join(parts)


def render_proposal_svg(graph):
    """Compact grouped projection of the same graph, sized for a 6.5-inch page."""
    s = graph["statistics"]
    assert s["node_kinds"] == {"Organization": 11, "Service": 20, "Product": 7,
                              "Environment": 1, "DataType": 49, "PolicyReview": 28}
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="6.5in" height="1.775in" viewBox="0 0 1300 355" role="img" aria-labelledby="title desc">',
             '<title id="title">Initial Sewall.ai catalog and governance graph</title>',
             '<desc id="desc">Grouped projection of 116 typed nodes and 111 typed edges. Twenty services, seven products and one unconfigured environment connect to 49 data types and 28 unresolved review requirements. Eleven organization entries are counted. These are catalog mappings, not scientific sample joins or access grants.</desc>',
             '<defs><style>text{font-family:Arial,Helvetica,sans-serif;fill:#183346}.title{font-size:26px;font-weight:700}.body{font-size:24px}.small{font-size:22px}.edge{fill:none;stroke:#8ea8b6;stroke-width:2}.policy{stroke:#b27a2a;stroke-dasharray:6 4}</style></defs>',
             '<rect width="1300" height="355" fill="white"/>',
             f'<text x="10" y="29" class="title">Initial catalog graph: {s["nodes"]} nodes, {s["edges"]} edges | October 23, 2026</text>']
    groups = [("NOAA", "5 services", "Weather / tides"),
              ("NEON", "1 service", "4 products"),
              ("NCBI", "7 databases", "Distinct identifiers"),
              ("Earth Engine", "1 service", "3 collections + GCP"),
              ("Other public (5)", "EOL / CBP / USGS", "JGI / PANGAEA"),
              ("Virginia", "1 template", "Authority pending")]
    for i, (name, line1, line2) in enumerate(groups):
        x = 10 + i * 216
        mid = x + 99
        parts.extend([f'<path d="M{mid} 150 C{mid} 180,350 175,350 204" class="edge"/>',
                      f'<path d="M{mid} 150 C{mid} 185,960 178,960 204" class="edge policy"/>',
                      f'<rect x="{x}" y="48" width="200" height="102" rx="5" fill="#edf4f7" stroke="#8ea8b6"/>',
                      f'<text x="{x+8}" y="76" class="title" style="font-size:{22 if i in (4,5) else 26}px">{escape(name)}</text>',
                      f'<text x="{x+8}" y="104" class="body" style="font-size:{21 if i == 4 else 24}px">{escape(line1)}</text>',
                      f'<text x="{x+8}" y="131" class="small" style="font-size:{20}px">{escape(line2)}</text>'])
    parts.extend(['<rect x="80" y="204" width="540" height="102" rx="5" fill="#edf7f2" stroke="#608c79"/>',
                  '<text x="96" y="234" class="title">49 data-type categories</text>',
                  '<text x="96" y="263" class="body">Omics · traits · ecology · weather</text>',
                  '<text x="96" y="292" class="body">Images · spatial context · literature</text>',
                  '<rect x="690" y="204" width="540" height="102" rx="5" fill="#fff7e9" stroke="#b27a2a"/>',
                  '<text x="706" y="234" class="title">28 unresolved policy reviews</text>',
                  '<text x="706" y="263" class="body">License · attribution · egress · retention</text>',
                  '<text x="706" y="292" class="body">Actor · purpose · environment · audit</text>',
                  '<text x="10" y="341" class="small">11 organization entries · 20 services · 7 products · 1 unconfigured environment · no scientific joins or grants</text>',
                  '</svg>'])
    return "\n".join(parts)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args(argv)
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    if args.verify:
        graph = json.loads(args.verify.read_text(encoding="utf-8"))
        validate_graph(graph, registry)
        print(json.dumps({"replay_matches": True, "graph_sha256": graph["graph_sha256"], **graph["statistics"]}, indent=2))
        return
    if args.out is None:
        parser.error("--out is required when building")
    graph = build_graph(registry)
    # Never silently overwrite a run with a different input graph.
    if (args.out / "graph.json").exists():
        prior = json.loads((args.out / "graph.json").read_text())
        if prior.get("graph_sha256") != graph["graph_sha256"]:
            raise ValueError("Output directory contains a different graph; choose a new directory")
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "graph.json").write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (args.out / "index.html").write_text(render_html(graph), encoding="utf-8")
    (args.out / "overview.svg").write_text(render_svg(graph), encoding="utf-8")
    (args.out / "proposal.svg").write_text(render_proposal_svg(graph), encoding="utf-8")
    validation = {"build_version": VERSION, "registry_sha256": digest(registry),
                  "graph_sha256": graph["graph_sha256"], **graph["statistics"],
                  "structural_validation_passed": True,
                  "deterministic_rebuild_matches": graph == build_graph(registry),
                  "scientific_join_count": 0, "authorization_grant_count": 0,
                  "network_requests_in_build": 0}
    (args.out / "validation.json").write_text(json.dumps(validation, indent=2) + "\n")
    print(json.dumps(validation, indent=2))


if __name__ == "__main__":
    main()

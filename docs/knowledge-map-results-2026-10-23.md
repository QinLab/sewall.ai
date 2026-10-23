# Initial catalog and governance graph: preliminary software result

Built October 23, 2026 from the [dated source registry](knowledge-map-source-registry-2026-10-23.json).
The [interactive viewer](../knowledge-map/index.html) and [JSON snapshot](../knowledge-map/graph.json)
are preliminary software artifacts. This catalog snapshot is not a verified scientific cohort.

## Observed result

The deterministic builder produces **116 nodes and 111 edges**. Nodes comprise 11 organization
entries, 20 services, seven NEON/Earth Engine products or collections, one unconfigured GCP
environment, 49 data-type categories and 28 unresolved policy-review records. Edges comprise
20 `published_by`, seven `served_by`, one `hosted_by`, one `uses_service`, 54 `offers_data_type`
and 28 `requires_policy_review` relationships. These are explicit catalog/category mappings,
configuration templates and review requirements. No sample identity or scientific association
edge is present.

NOAA has five service entries, including weather. NCBI is one provider with seven database
entries. NEON has four selected aquatic/soil products. Earth Engine has Landsat, Sentinel-2
and annual Satellite Embedding collection entries; its project template is distinct from data
rights. EOL, Chesapeake Bay Program, USGS, JGI and PANGAEA supply five other public-service
entries. The Virginia holding is an unresolved custodian/service template. ODU is a planning
organization entry without asserted custodian authority. Organization entries are not counts
of committed partners.

Every service, product and environment has its own review node. Authorization, purpose,
operation, license, attribution, source/policy version, residency, egress, retention, model
disclosure, audit and destination requirements remain unknown in this catalog seed. Existing
source-governance notes and links guide review; no automated legal interpretation is claimed.

The viewer supports four graph views, search, keyboard selection, neighbor navigation,
documentation links, per-node JSON/lineage inspection and JSON download. All data and code
are embedded locally. The graph builder contains no HTTP client or model adapter and makes
zero network requests. Build output includes a full-size overview and a compact, grouped
proposal figure generated from the same snapshot. Aggregation does not add scientific edges.

## Reproduction and checks

Run the build/replay commands in the [prototype README](../README.md#initial-knowledge-map).
Canonical JSON and SHA-256 bind the complete graph to the exact registry. Replay reconstructs
the graph and requires exact equality, rather than accepting a matching summary count.
The builder refuses to replace an output directory containing a different graph digest.

Sixteen focused Python unit tests passed. They check deterministic replay, inventory,
distinct source/product/environment reviews, missing endpoints and reviews, duplicates,
tampering, unsupported scientific relations, permission promotion, signed URLs, credentials,
private cloud configuration and safe HTML embedding. The actual embedded viewer script
passed nine interaction checks in a minimal DOM harness: graph-view counts, search,
node selection, policy details, keyboard activation and reset. JSON Schema validation passed
with the optional `jsonschema` dependency. The compact and full-size SVG/PNG figures were
visually inspected. Machine-readable evidence is in
[validation.json](../knowledge-map/validation.json) and [checks.json](../knowledge-map/checks.json).

The browser-control service returned no available browsers, and native Chrome control failed
to initialize. Therefore a real-browser visual/interaction check is still outstanding. The
minimal-DOM checks establish script behavior, not browser layout or cross-browser compatibility.

## Scientific interpretation and next stage

This preliminary result establishes an executable, inspectable catalog/governance representation
with reproducible topology. It does **not** establish record-level permission, reviewed policy
profiles, compatible units/assays, validated scientific joins, ecological predictive skill,
external-custodian federation or a Flagship-scale scientific advance. Documentation URLs are
curated source references, not a claim that this build rechecked live APIs. Prior NEON API
observations remain dated evidence in their own archive.

The next stage adds pinned releases/distributions and bounded source records, then evidence-qualified
sample, taxonomic and spatiotemporal relations. Scientific reviewers must adjudicate links;
custodians/stewards must establish permissions for an actor, purpose, operation, representation,
environment and destination. The existing synthetic fixture policy evaluator remains separate.
No source-enumeration or permission change was made to the general research controller.

Figure caption for reuse: Initial Sewall.ai catalog and governance graph, October 23, 2026.
The grouped view represents 116 nodes and 111 edges. Solid lines show catalog/data-type mappings;
dashed lines show unresolved review requirements. Source inclusion implies neither institutional
commitment nor permission. No scientific sample joins are asserted.

"""Render an inspectable, self-contained report without network dependencies."""

from __future__ import annotations

import json


def render_report(manifest: dict) -> str:
    """Return an offline HTML report for a Sewall.ai run manifest.

    The renderer does not execute workflow steps, validate scientific claims, or
    grant access. All manifest content is treated as untrusted display data.
    """
    if not isinstance(manifest, dict):
        raise TypeError("manifest must be a dictionary")
    payload = json.dumps(manifest, ensure_ascii=False, allow_nan=False)
    # Script elements use HTML raw-text parsing, even with application/json.
    # Escape delimiters before embedding so a source record cannot end the tag.
    for character, escaped in (
        ("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
        ("\u2028", "\\u2028"), ("\u2029", "\\u2029"),
    ):
        payload = payload.replace(character, escaped)
    return _HTML.replace("__MANIFEST_JSON__", payload, 1)


_HTML = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>Sewall.ai | Research workflow report</title>
<style>
:root{color-scheme:light;--ink:#142c39;--muted:#4c6370;--paper:#f1f5f4;--line:#c7d4d6;--teal:#12635f;--blue:#225e94;--amber:#835308;--red:#a02931}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header,main,footer{max-width:1540px;margin:auto;padding:26px 32px}header{padding-bottom:16px}h1{font-size:30px;line-height:1.2;margin:12px 0}h2{font-size:18px;margin:0 0 12px}h3{font-size:15px;margin:18px 0 8px}p{margin:8px 0}.eyebrow{font-weight:700;letter-spacing:.1em;text-transform:uppercase;font-size:12px;color:var(--teal)}
.topline,.row,.toolbar,.legend{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.topline{justify-content:space-between}.badge{display:inline-block;border-radius:5px;padding:5px 10px;font-size:12px;font-weight:700;background:#e1ebed}.synthetic{color:#674505;background:#fff0c4;border:1px solid #ddbd68}.status{border:1px solid var(--line)}.failed,.blocked,.denied,.deny,.revoked,.abstained,.rejected{color:var(--red);background:#fff0f0}.needs_review,.review,.pending,.missing,.unsupported{color:var(--amber);background:#fff3d7}.completed,.approved,.allow,.verified,.succeeded{color:#0f5549;background:#e0f1e9}
.muted,small{color:var(--muted)}.notice{border-left:4px solid #d1a33d;background:#fff8e7;padding:12px 16px;margin:18px 0 4px}.notice[data-critical="true"]{border-color:var(--red);background:#fff0f0}.metrics{display:flex;flex-wrap:wrap;gap:12px;margin-bottom:20px}.metric{background:#fff;border:1px solid var(--line);border-radius:8px;padding:12px 16px;flex:1 1 160px}.metric-value{display:block;font-weight:700;font-size:22px}.metric-label{font-size:12px;color:var(--muted);overflow-wrap:anywhere}.workspace{display:grid;grid-template-columns:minmax(0,1fr) 355px;gap:18px;align-items:start}.panel{background:#fff;border:1px solid var(--line);border-radius:10px;padding:20px;min-width:0}.toolbar{margin:14px 0;font-size:12px;gap:10px}.toolbar label{display:flex;align-items:center;gap:6px}select,button{font:inherit;border:1px solid #99aeb4;border-radius:5px;background:#fff;color:var(--ink);padding:6px 9px}button{cursor:pointer}button:hover{background:#edf6f3}button:focus-visible,select:focus-visible,[role="button"]:focus-visible{outline:3px solid #2c70ad;outline-offset:3px}.legend{font-size:12px;color:var(--muted);margin:10px 0}.line-key{display:inline-block;width:24px;border-top:2px solid var(--teal);vertical-align:middle;margin-right:5px}.line-key.context{border-color:#718692;border-top-style:dashed}.graph-scroll{overflow:auto;min-height:240px;border:1px solid #e0e7e7;border-radius:7px;background:#f9fbfb}svg{display:block;width:100%;min-width:720px}.node rect{fill:#fff;stroke:#889ea8;stroke-width:1.5}.node.selected rect{stroke:var(--blue);stroke-width:3}.node:hover rect{fill:#f0f7f7}.node text{pointer-events:none;fill:var(--ink)}.node .kind-label{font-size:10px;fill:var(--muted);font-weight:600}.node .node-label{font-size:12px;font-weight:600}.node .state-label{font-size:10px}.node[data-alert="true"] rect{stroke:#ad6533;fill:#fff9ed}.node[data-failed="true"] rect{stroke:var(--red);fill:#fff4f4}.edge{fill:none;stroke:var(--teal);stroke-width:1.5;opacity:.75}.edge.context{stroke:#718692;stroke-dasharray:5 4}.lane-label{fill:#5b747f;font-size:11px;font-weight:700;letter-spacing:.08em}.graph-empty{padding:24px;color:var(--muted)}.details{max-height:560px;overflow:auto}pre{font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f2f5f6;padding:12px;border-radius:5px;margin:8px 0}dl{margin:8px 0}dt{font-size:12px;color:var(--muted);margin-top:10px}dd{margin:0;overflow-wrap:anywhere}.timeline{max-height:420px;overflow:auto;list-style:none;padding:0;margin:0}.timeline li{padding:0 0 12px 16px;border-left:2px solid #c4d8d6;position:relative}.timeline li::before{content:"";position:absolute;left:-5px;top:9px;width:8px;height:8px;background:var(--teal);border-radius:50%}.timeline button{display:block;text-align:left;width:100%;font-size:12px;border-color:transparent;background:#f2f7f6}.timeline button[aria-pressed="true"]{border-color:var(--blue)}.event-kind{font-weight:650;display:block}.event-sequence{font-size:11px;color:var(--muted)}.lower{display:grid;grid-template-columns:1fr 1fr;gap:18px;margin-top:18px}.record{border-top:1px solid #e0e7e7;padding:12px 0;overflow-wrap:anywhere}.record:first-child{border-top:0}.record summary{cursor:pointer;font-weight:600}.record p{font-size:13px}.limit-list{padding-left:20px}footer{font-size:12px;padding-top:0;overflow-wrap:anywhere}.sidebar{display:grid;gap:18px}.sr-only{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0,0,0,0)}
@media(max-width:1050px){.workspace{grid-template-columns:1fr}.sidebar{grid-template-columns:1fr 1fr}.lower{grid-template-columns:1fr}}@media(max-width:650px){header,main,footer{padding-left:16px;padding-right:16px}h1{font-size:24px}.sidebar{grid-template-columns:1fr}.panel{padding:15px}}@media print{.workspace,.lower,.sidebar{display:block}.panel{margin-bottom:14px}.toolbar{display:none}.details,.timeline{max-height:none}body{background:#fff}}
</style>
</head>
<body>
<header>
  <div class="topline"><span class="eyebrow">Sewall.ai / Workflow evidence</span><span id="mode" class="badge synthetic">SYNTHETIC FIXTURE SIMULATION</span></div>
  <h1 id="question">Research workflow report</h1>
  <div class="row"><span id="status" class="badge status">Status unavailable</span><span id="run-meta" class="muted"></span></div>
  <p id="run-notice" class="notice">This report displays a workflow record. Scientific findings and permissions require independent validation.</p>
</header>
<main>
  <noscript><p class="notice">Enable JavaScript to inspect the graph and evidence in this offline report. No network connection is required.</p></noscript>
  <section aria-label="Run metrics" id="metrics" class="metrics"></section>
  <section id="agent-assessment" class="panel" style="margin-bottom:18px" hidden>
    <h2>Model-assisted metadata assessment</h2>
    <p class="muted">This assessment concerns retrieved public metadata. Model interpretations and quoted text do not establish a biological finding.</p>
    <div id="metadata-summary"></div>
    <details class="record"><summary>Model calls and compute allocation</summary><div id="model-calls"></div><pre id="compute-context"></pre></details>
    <details class="record"><summary>Retrieved public source records</summary><div id="live-records"></div></details>
  </section>
  <div class="workspace">
    <section class="panel" aria-labelledby="graph-heading">
      <h2 id="graph-heading">Dynamic research graph</h2>
      <p class="muted">Select a node to inspect its recorded evidence and relationships. Events describe the workflow history; this graph shows the saved revision.</p>
      <div class="toolbar">
        <label>Node kind <select id="kind-filter" aria-label="Filter node kind"><option value="">All kinds</option></select></label>
        <label>Status <select id="status-filter" aria-label="Filter node status"><option value="">All statuses</option></select></label>
        <label>Links <select id="edge-filter" aria-label="Filter link type"><option value="all">All links</option><option value="execution">Execution dependencies</option><option value="context">Knowledge / context</option></select></label>
        <button id="reset" type="button">Reset view</button>
      </div>
      <div class="legend"><span><span class="line-key"></span>Execution dependency</span><span><span class="line-key context"></span>Knowledge / context link</span></div>
      <p id="graph-count" class="muted" aria-live="polite"></p>
      <div class="graph-scroll"><svg id="graph" role="group" aria-label="Research graph. Use Tab to select nodes and Enter to inspect their details."></svg><p id="graph-empty" class="graph-empty" hidden>No nodes match the current filters.</p></div>
    </section>
    <aside class="sidebar" aria-label="Evidence inspector and timeline">
      <section class="panel"><h2 id="inspector-heading">Evidence inspector</h2><div id="details" class="details" aria-live="polite"><p class="muted">Select a graph node or timeline event.</p></div></section>
      <section class="panel"><h2>Event timeline</h2><p class="muted">Recorded events, in sequence. Hashes are displayed as recorded; this viewer does not verify them.</p><ol id="timeline" class="timeline"></ol></section>
    </aside>
  </div>
  <div class="lower">
    <section class="panel"><h2>Policy decisions</h2><p class="muted">Recorded policy checks do not grant repository access or approve an agreement.</p><div id="policies"></div></section>
    <section class="panel"><h2>Claim records</h2><p class="muted">A completed workflow does not establish a biological finding. Inspect support, missing evidence, and review status.</p><div id="claims"></div></section>
  </div>
  <section class="panel" style="margin-top:18px"><h2>Limitations and review needs</h2><ul id="limitations" class="limit-list"></ul></section>
</main>
<footer><p>Offline inspection artifact. No external scripts, repository requests, model calls, or data transfer are performed by this page.</p><p id="digest"></p></footer>
<script id="manifest-data" type="application/json">__MANIFEST_JSON__</script>
<script>
"use strict";
(() => {
  const manifest = JSON.parse(document.getElementById("manifest-data").textContent);
  const list = value => Array.isArray(value) ? value : [];
  const record = value => value && typeof value === "object" && !Array.isArray(value) ? value : {};
  const display = value => value === null || value === undefined ? "Not recorded" : typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
  const pretty = value => display(value).replace(/_/g, " ");
  const node = (tag, text, className) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = display(text); if (className) el.className = className; return el; };
  const knownStatuses = new Set(["failed", "blocked", "denied", "deny", "revoked", "abstained", "rejected", "budget_exhausted", "no_evidence", "needs_review", "review", "pending", "missing", "unsupported", "unavailable", "completed", "approved", "allow", "verified", "succeeded"]);
  const failureStatuses = new Set(["failed", "blocked", "denied", "deny", "revoked", "abstained", "rejected", "budget_exhausted"]);
  const reviewStatuses = new Set(["needs_review", "review", "pending", "missing", "unsupported", "unavailable", "no_evidence"]);
  const badge = value => node("span", pretty(value), "badge " + (knownStatuses.has(value) ? value : "status"));
  const graph = record(manifest.graph);
  const nodes = list(graph.nodes).filter(item => item && typeof item === "object");
  const edges = list(graph.edges).filter(item => item && typeof item === "object");
  const byId = new Map(nodes.map(item => [String(item.id), item]));
  const depends = new Set(["depends_on", "requires", "precedes", "executes", "feeds", "produces", "consumes", "execution_dependency"]);
  const execution = edge => edge.kind === "execution" || edge.type === "execution" || depends.has(edge.relation);
  const synthetic = manifest.mode === "synthetic_fixture";
  const live = manifest.mode === "live_public_metadata";
  const modeBadge = document.getElementById("mode");
  modeBadge.textContent = synthetic ? "SYNTHETIC FIXTURE SIMULATION" : live ? "LIVE PUBLIC METADATA / MODEL-ASSISTED" : "UNVALIDATED WORKFLOW RECORD";
  document.getElementById("question").textContent = display(manifest.question || "Research workflow report");
  const runStatus = manifest.status || "unavailable";
  const status = document.getElementById("status");
  status.textContent = "Run status: " + pretty(runStatus);
  if (knownStatuses.has(runStatus)) status.classList.add(runStatus);
  document.getElementById("run-meta").textContent = "Run " + display(manifest.run_id) + " · Revision " + display(manifest.revision) + " · Schema " + display(manifest.schema_version);
  const notice = document.getElementById("run-notice");
  const description = synthetic ? "Synthetic records and simulated policies only. No live repository access, agreement negotiation, or scientific discovery has been demonstrated by this run." : live ? "This run records cloud model calls and bounded public metadata retrieval. Inspect the actual model responses, source records, rejected links, and remaining gaps. No controlled data access or biological discovery is established." : "This viewer displays the supplied manifest and does not independently validate source access, policies, evidence, or scientific results.";
  const outcomes = {abstained: "The workflow abstained. Review missing or insufficient evidence before proceeding.", needs_review: "The workflow requires review. Pending authorization or evidence must be resolved by an authorized person.", failed: "The workflow failed. Inspect the failure event and affected nodes before retrying.", budget_exhausted: "A configured budget stopped this run; inspect partial evidence and the stop reason.", no_evidence: "The run did not establish usable metadata evidence."};
  notice.textContent = (outcomes[runStatus] ? outcomes[runStatus] + " " : "") + description;
  notice.dataset.critical = failureStatuses.has(runStatus) ? "true" : "false";
  const metrics = record(manifest.metrics);
  const metricEntries = Object.entries(metrics);
  if (!metricEntries.length) metricEntries.push(["graph_nodes", nodes.length], ["graph_links", edges.length], ["recorded_events", list(manifest.events).length]);
  metricEntries.forEach(([key, value]) => { const tile = node("div", undefined, "metric"); tile.append(node("span", display(value), "metric-value"), node("span", pretty(key), "metric-label")); document.getElementById("metrics").append(tile); });
  const kindFilter = document.getElementById("kind-filter");
  const statusFilter = document.getElementById("status-filter");
  const edgeFilter = document.getElementById("edge-filter");
  function populate(select, key) { [...new Set(nodes.map(item => item[key]).filter(value => typeof value === "string"))].sort().forEach(value => { const option = node("option", pretty(value)); option.value = value; select.append(option); }); }
  populate(kindFilter, "kind"); populate(statusFilter, "status");
  const svg = document.getElementById("graph");
  const ns = "http://www.w3.org/2000/svg";
  const svgNode = (tag, attributes = {}, text) => { const el = document.createElementNS(ns, tag); Object.entries(attributes).forEach(([key, value]) => el.setAttribute(key, String(value))); if (text !== undefined) el.textContent = display(text); return el; };
  let selectedId = null;
  const details = document.getElementById("details");
  function addField(label, value) { const dl = node("dl"); dl.append(node("dt", label), node("dd", value)); details.append(dl); }
  function inspectNode(item) {
    selectedId = String(item.id);
    document.getElementById("inspector-heading").textContent = "Node evidence";
    details.replaceChildren(node("h3", item.label || item.id), badge(item.status || "unavailable"));
    addField("Node ID", item.id); addField("Kind", pretty(item.kind));
    if (item.source_id !== undefined) addField("Source", item.source_id);
    if (failureStatuses.has(item.status) || reviewStatuses.has(item.status)) details.append(node("p", "This node requires attention. Its status does not support treating its output as an established finding.", "notice"));
    details.append(node("h3", "Recorded details"), node("pre", JSON.stringify(record(item.details), null, 2)));
    const connected = edges.filter(edge => String(edge.source) === selectedId || String(edge.target) === selectedId);
    details.append(node("h3", "Relationships and evidence"));
    if (!connected.length) details.append(node("p", "No relationships recorded.", "muted"));
    connected.forEach(edge => {
      const section = node("div", undefined, "record");
      const source = byId.get(String(edge.source)); const target = byId.get(String(edge.target));
      section.append(node("p", (source ? source.label || source.id : edge.source) + " → " + (target ? target.label || target.id : edge.target)));
      section.append(node("p", pretty(edge.relation) + " · " + (execution(edge) ? "Execution dependency" : "Knowledge / context link"), "muted"));
      const evidence = edge.evidence;
      section.append(evidence && Object.keys(Object(evidence)).length ? node("pre", JSON.stringify(evidence, null, 2)) : node("p", "No edge evidence recorded. This link alone does not establish scientific support.", "muted"));
      details.append(section);
    });
    drawGraph();
  }
  const laneFor = item => {
    const kind = String(item.kind || "").toLowerCase();
    if (/question|intent|objective/.test(kind)) return 0;
    if (/policy|agreement|authorization|request|permission/.test(kind)) return 1;
    if (/source|dataset|taxon|context|entity|repository/.test(kind)) return 2;
    if (/claim|answer|result|finding/.test(kind)) return 4;
    return 3;
  };
  function wrapped(text, width = 23) {
    const words = display(text).split(/\s+/); const lines = []; let current = "";
    words.forEach(word => { if ((current + " " + word).trim().length > width && current) { lines.push(current); current = ""; } while (word.length > width) { if (current) { lines.push(current); current = ""; } lines.push(word.slice(0, width)); word = word.slice(width); } current = (current + " " + word).trim(); });
    if (current) lines.push(current);
    return lines.length > 2 ? [lines[0], lines[1].slice(0, width - 1) + "…"] : lines;
  }
  function drawGraph() {
    const visible = nodes.filter(item => (!kindFilter.value || item.kind === kindFilter.value) && (!statusFilter.value || item.status === statusFilter.value));
    const shownIds = new Set(visible.map(item => String(item.id)));
    const shownEdges = edges.filter(edge => shownIds.has(String(edge.source)) && shownIds.has(String(edge.target)) && (edgeFilter.value === "all" || (execution(edge) ? "execution" : "context") === edgeFilter.value));
    document.getElementById("graph-count").textContent = visible.length + " of " + nodes.length + " nodes · " + shownEdges.length + " of " + edges.length + " links";
    document.getElementById("graph-empty").hidden = visible.length > 0;
    svg.style.display = visible.length ? "block" : "none";
    svg.replaceChildren();
    const defs = svgNode("defs");
    [["execution-arrow", "#12635f"], ["context-arrow", "#718692"]].forEach(([id, color]) => { const marker = svgNode("marker", {id, viewBox:"0 0 10 10", refX:9, refY:5, markerWidth:6, markerHeight:6, orient:"auto-start-reverse"}); marker.append(svgNode("path", {d:"M 0 0 L 10 5 L 0 10 z", fill:color})); defs.append(marker); });
    svg.append(defs);
    const laneCounts = [0, 0, 0, 0, 0]; const positions = new Map();
    visible.forEach(item => { const lane = laneFor(item); positions.set(String(item.id), {x:20 + lane * 201, y:54 + laneCounts[lane]++ * 122}); });
    const height = Math.max(245, Math.max(...laneCounts) * 122 + 74);
    svg.setAttribute("viewBox", "0 0 1045 " + height);
    ["QUESTION", "POLICY", "SOURCE / CONTEXT", "WORK / EVIDENCE", "CLAIM / RESULT"].forEach((label, index) => svg.append(svgNode("text", {x:20 + index * 201, y:27, class:"lane-label"}, label)));
    shownEdges.forEach(edge => {
      const from = positions.get(String(edge.source)); const to = positions.get(String(edge.target));
      let path;
      if (from.x === to.x) { const x = from.x + 174; path = "M " + x + " " + (from.y + 45) + " C " + (x + 24) + " " + (from.y + 45) + ", " + (x + 24) + " " + (to.y + 45) + ", " + x + " " + (to.y + 45); }
      else { const forward = from.x < to.x; const x1 = from.x + (forward ? 174 : 0); const x2 = to.x + (forward ? 0 : 174); const mid = (x1 + x2) / 2; path = "M " + x1 + " " + (from.y + 45) + " C " + mid + " " + (from.y + 45) + ", " + mid + " " + (to.y + 45) + ", " + x2 + " " + (to.y + 45); }
      const el = svgNode("path", {d:path, class:execution(edge) ? "edge" : "edge context", "marker-end":execution(edge) ? "url(#execution-arrow)" : "url(#context-arrow)"});
      el.append(svgNode("title", {}, pretty(edge.relation))); svg.append(el);
    });
    visible.forEach(item => {
      const pos = positions.get(String(item.id)); const chosen = String(item.id) === selectedId;
      const group = svgNode("g", {class:"node" + (chosen ? " selected" : ""), transform:"translate(" + pos.x + " " + pos.y + ")", tabindex:0, role:"button", "aria-label":display(item.label || item.id) + ", " + pretty(item.kind) + ", " + pretty(item.status), "aria-pressed":chosen ? "true" : "false", "data-alert":reviewStatuses.has(item.status), "data-failed":failureStatuses.has(item.status)});
      group.append(svgNode("title", {}, display(item.label || item.id)), svgNode("rect", {width:174, height:93, rx:7}), svgNode("text", {x:11, y:18, class:"kind-label"}, pretty(item.kind || "node").slice(0, 26)));
      wrapped(item.label || item.id).forEach((line, index) => group.append(svgNode("text", {x:11, y:39 + index * 16, class:"node-label"}, line)));
      group.append(svgNode("text", {x:11, y:79, class:"state-label"}, pretty(item.status || "unavailable").slice(0, 26)));
      group.addEventListener("click", () => inspectNode(item));
      group.addEventListener("keydown", event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); inspectNode(item); const focused = [...svg.querySelectorAll(".node")].find(el => el.getAttribute("aria-pressed") === "true"); if (focused) focused.focus(); } });
      svg.append(group);
    });
  }
  [kindFilter, statusFilter, edgeFilter].forEach(filter => filter.addEventListener("change", drawGraph));
  document.getElementById("reset").addEventListener("click", () => { kindFilter.value = ""; statusFilter.value = ""; edgeFilter.value = "all"; selectedId = null; document.getElementById("inspector-heading").textContent = "Evidence inspector"; details.replaceChildren(node("p", "Select a graph node or timeline event.", "muted")); drawGraph(); });
  const events = list(manifest.events).filter(item => item && typeof item === "object").slice().sort((a,b) => Number(a.sequence || 0) - Number(b.sequence || 0));
  const timeline = document.getElementById("timeline");
  if (!events.length) timeline.append(node("li", "No events recorded.", "muted"));
  events.forEach(event => {
    const item = node("li"); const button = node("button"); button.type = "button"; button.setAttribute("aria-pressed", "false");
    button.append(node("span", "Event " + display(event.sequence), "event-sequence"), node("span", pretty(event.type), "event-kind"));
    button.addEventListener("click", () => { timeline.querySelectorAll("button").forEach(el => el.setAttribute("aria-pressed", "false")); button.setAttribute("aria-pressed", "true"); selectedId = null; drawGraph(); document.getElementById("inspector-heading").textContent = "Event evidence"; details.replaceChildren(node("h3", pretty(event.type)), node("pre", JSON.stringify(record(event.details), null, 2))); addField("Sequence", event.sequence); addField("Previous hash (recorded)", event.previous_hash); addField("Event hash (recorded)", event.hash); });
    item.append(button); timeline.append(item);
  });
  function records(containerId, entries, empty, claimRecords) {
    const container = document.getElementById(containerId); const rows = list(entries);
    if (!rows.length) container.append(node("p", empty, "muted"));
    rows.forEach((entry, index) => {
      const item = record(entry); const section = node("details", undefined, "record");
      const label = item.statement || item.label || item.source_id || item.id || "Record " + (index + 1);
      const summary = node("summary", label); section.append(summary);
      const state = item.status || item.decision || "status not recorded";
      section.append(badge(state));
      if (claimRecords && (failureStatuses.has(state) || reviewStatuses.has(state) || !item.evidence && !item.evidence_ids && !item.supporting_evidence)) section.append(node("p", "Review the complete record for evidence and support. This viewer does not validate scientific claims.", "notice"));
      section.append(node("pre", JSON.stringify(entry, null, 2))); container.append(section);
    });
  }
  records("policies", manifest.policies, "No policy decisions recorded. No authorization can be inferred.", false);
  records("claims", manifest.claims, "No scientific claims recorded.", true);
  if (live) {
    document.getElementById("agent-assessment").hidden = false;
    const summary = record(manifest.metadata_summary);
    const target = document.getElementById("metadata-summary");
    target.append(node("p", summary.summary || "No valid model assessment is available; inspect the stop reason and recorded events."));
    if (manifest.stop_reason) target.append(node("p", "Stop reason: " + manifest.stop_reason, "muted"));
    if (Object.keys(summary).length) { const more = node("details", undefined, "record"); more.append(node("summary", "Assessment evidence and limitations"), node("pre", JSON.stringify(summary, null, 2))); target.append(more); }
    records("model-calls", list(manifest.model_calls).map(item => ({label: item.role + " call " + item.attempt, ...item})), "No model responses recorded.", false);
    document.getElementById("compute-context").textContent = JSON.stringify(record(manifest.context), null, 2);
    const liveRecords = document.getElementById("live-records");
    list(manifest.records).forEach(item => {
      const section = node("details", undefined, "record");
      section.append(node("summary", item.title || item.id));
      if (typeof item.source_url === "string" && /^https:\/\/(pubmed\.ncbi\.nlm\.nih\.gov|www\.ncbi\.nlm\.nih\.gov)\//.test(item.source_url)) {
        const link = node("a", item.accession || item.id); link.href = item.source_url; link.target = "_blank"; link.rel = "noopener noreferrer"; section.append(link);
      }
      section.append(node("pre", JSON.stringify(item, null, 2))); liveRecords.append(section);
    });
  }
  const limitations = list(manifest.limitations).slice();
  if (synthetic) limitations.unshift("All source responses and policy decisions in this run are synthetic fixtures. Results cannot be interpreted as biological evidence or real custodian authorization.");
  if (!limitations.length) limitations.push("No limitations supplied. This does not establish completeness, permission, or scientific validity.");
  limitations.forEach(value => document.getElementById("limitations").append(node("li", value)));
  document.getElementById("digest").textContent = "Manifest content digest (recorded): " + display(manifest.content_digest);
  drawGraph();
})();
</script>
</body>
</html>
'''

"""Gated release risk assessment for a finished run, in the style of a reliability FMEA.

A run can finish cleanly and still hold outputs that should not be used as they
stand: a stale sensor reading, a monthly index from too few scenes, a proxy read
as an identification, a review concern nobody answered. This module decides,
output by output, what may be released. Trust is treated as a release decision
(Acharya, Al Amin and Qin, arXiv:2610.19507).

The gate runs in stages:

- G0 integrity: the manifest must verify, and a supplied review forum must
  verify against it. Either failure blocks every output.
- G1 run status: a safe-stopped or failed run blocks; an exhausted budget or a
  run awaiting review holds.
- G2 containment: quarantined or blocked graph nodes are blocked one by one;
  the rest of the run is still assessed.
- G3 peer review: concerns from the forum attach to the outputs they cite.
  Only concern, limitation and answer posts count as review. An output that
  no such post cited has every detection rating raised by one.
- G4 risk: each output is scored against a fixed failure-mode catalog. Each
  active mode gets severity S, occurrence O and detection D on 1 to 10 scales,
  and a risk priority number RPN = S x O x D.

An output is held for validation when any mode has RPN >= HOLD_RPN, or S >=
HIGH_SEVERITY with O >= LIKELY. It is released with limitations when any mode
has RPN >= LIMIT_RPN, and released otherwise. The run's result is assessed as
one more output that depends on all the others: it is held whenever any
output it rests on is held or blocked by containment, while the outputs that
passed can still be released on their own.

The gate is deterministic and makes no model call. It has no override input:
an agent or reviewer can lower a rating only by adding evidence the gate reads,
such as a forum concern, limitation or answer that cites the output. A forum
concern counts as resolved only when a reply cites run evidence the concern
did not already cite. The ratings and thresholds are
prototype settings chosen by hand, not calibrated, and an RPN is an ordinal
screen, not a probability that the output is invalid.
"""

from copy import deepcopy
import html
import math

from .agent import verify_agent_manifest
from .graph import digest

SCHEMA_VERSION = "0.1.0"
DECISIONS = ("release", "release_with_limitations", "hold_for_validation", "blocked")
HOLD_RPN = 150
LIMIT_RPN = 20
HIGH_SEVERITY = 8
LIKELY = 6
MIN_SCENES = 3
_RUN_HOLD = {"budget_exhausted", "needs_review"}
_RUN_BLOCK = {"safe_stopped", "failed"}
_RUN_ANCHORS = {"question", "result", "review"}

# id: (failure mode, effect if released, severity, detection with the harness's controls, mitigation)
CATALOG = {
    "FM01": ("stale sensor reading", "an old water level is read as current", 8, 2,
             "Re-query the area and release only readings newer than two hours."),
    "FM02": ("sensor out of service", "a gap is read as no flooding", 5, 3,
             "Report out-of-service and non-current gauges as gaps, never as no flooding."),
    "FM03": ("sensor service unavailable", "an area looks quieter than it is", 6, 2,
             "Retry the failed service in a new run before comparing areas."),
    "FM04": ("no flood threshold", "a level is shown without a flood status", 4, 2,
             "Release these levels as readings only, without a flood category."),
    "FM05": ("provisional data", "a preliminary value is treated as final", 3, 2,
             "State that the values are provisional and may be revised by the agency."),
    "FM06": ("too few usable scenes", "a monthly index rests on one or two images", 5, 3,
             "Widen the window or merge months before comparing values."),
    "FM07": ("cloud loss", "the scenes kept may not represent the window", 3, 2,
             "Report how many scenes were removed for cloud."),
    "FM08": ("proxy read as identification", "a chlorophyll index is read as a species or a bloom", 8, 6,
             "Pair the index with an in situ cell count or species observation before any bloom claim."),
    "FM09": ("embedding read as measurement", "a learned feature is read as a physical quantity", 6, 6,
             "Use embeddings only as covariates; report the dataset and model versions."),
    "FM10": ("empty summary", "a summary with no data is read as a zero", 6, 1,
             "Drop the output or re-run with a different window."),
    "FM11": ("empty or partial search", "no hits is read as no literature", 5, 2,
             "Revise the query with synonyms and report the query translation."),
    "FM12": ("open major review concern", "a reviewer's major objection is unanswered", 8, 3,
             "Answer the concern with new evidence in a revised run."),
    "FM13": ("open minor review concern", "a reviewer's minor objection is unanswered", 4, 3,
             "Answer or acknowledge the concern in the release notes."),
    "FM14": ("failed action", "a gap in the plan is read as a negative result", 6, 3,
             "Repeat the failed action in a new run, or state the gap."),
    "FM15": ("no literature evidence", "nothing ties the outputs to published work", 4, 2,
             "Add a literature search to the plan before drawing conclusions."),
    "FM16": ("depends on held evidence", "the result rests on outputs that did not pass", 7, 2,
             "Resolve the held outputs first, then reassess the result."),
}

LIMITATIONS = [
    "Severity, detection ratings and thresholds are prototype settings chosen by hand; they are not calibrated "
    "against observed failures.",
    "An RPN ranks outputs for attention; it is not a probability that an output is invalid.",
    "The catalog covers the failure modes this prototype knows about. An output can fail in a way no mode describes.",
    "A release decision concerns retrieved evidence. The gate validates no biological claim.",
]


def _occurrence(fraction):
    """Map the share of affected readings to a 1 to 10 occurrence rating; 0 means inactive."""
    if fraction <= 0:
        return 0
    return min(10, max(2, math.ceil(10 * fraction)))


def _mode(mode_id, occurrence, evidence, reviewed):
    name, effect, severity, detection, mitigation = CATALOG[mode_id]
    if not reviewed:
        detection = min(10, detection + 1)
    return {"id": mode_id, "mode": name, "effect": effect, "severity": severity, "occurrence": occurrence,
            "detection": detection, "rpn": severity * occurrence * detection, "evidence": evidence,
            "mitigation": mitigation}


def _decide(modes):
    if any(m["rpn"] >= HOLD_RPN or (m["severity"] >= HIGH_SEVERITY and m["occurrence"] >= LIKELY) for m in modes):
        return "hold_for_validation"
    if any(m["rpn"] >= LIMIT_RPN for m in modes):
        return "release_with_limitations"
    return "release"


def _worst(decisions):
    return max(decisions, key=DECISIONS.index, default="release")


def _flood_modes(obs, reviewed):
    modes = []
    gauges, stations, series = obs["nwps_gauges"], obs["coops_stations"], obs["usgs_series"]
    if not gauges and not stations and not series:
        return [_mode("FM10", 10, "no sensor in the area returned a record", reviewed)]
    with_value = [s for s in gauges + series if s.get("observed", s.get("value")) is not None] + \
                 [s for s in stations if s.get("value") is not None]
    stale = [s for s in with_value if s.get("stale")]
    if with_value and (o := _occurrence(len(stale) / len(with_value))):
        modes.append(_mode("FM01", o, f"{len(stale)} of {len(with_value)} readings are older than two hours",
                           reviewed))
    missing = [g for g in gauges if g["flood_category"] in ("out_of_service", "obs_not_current")] + \
              [s for s in stations if s.get("value") is None]
    if gauges or stations:
        if o := _occurrence(len(missing) / (len(gauges) + len(stations))):
            modes.append(_mode("FM02", o, f"{len(missing)} of {len(gauges) + len(stations)} NWS gauges and "
                               "NOAA stations have no current reading", reviewed))
    ok = ("nwps_gauges_in_area" in obs) + ("usgs_series_in_area" in obs) + len(stations)
    if obs["unavailable"] and (o := _occurrence(len(obs["unavailable"]) / (ok + len(obs["unavailable"])))):
        modes.append(_mode("FM03", o, "unavailable: " + ", ".join(u["service"] for u in obs["unavailable"]),
                           reviewed))
    sensors = len(gauges) + len(stations) + len(series)
    unthresholded = sum(g["flood_category"] == "not_defined" for g in gauges) + len(series) + \
        sum(s.get("flood_level") == "no_thresholds" for s in stations)
    if sensors and (o := _occurrence(unthresholded / sensors)):
        modes.append(_mode("FM04", o, f"{unthresholded} of {sensors} sensors have no flood threshold "
                           "(all USGS series lack one)", reviewed))
    labeled = [s for s in stations if s.get("value") is not None] + [s for s in series if s.get("value") is not None]
    provisional = sum(s.get("quality") == "p" or s.get("approval_status") == "Provisional" for s in labeled)
    if labeled and (o := _occurrence(provisional / len(labeled))):
        modes.append(_mode("FM05", o, f"{provisional} of {len(labeled)} NOAA and USGS values are provisional",
                           reviewed))
    return modes


def _chlorophyll_modes(obs, reviewed):
    modes, usable = [], obs["scenes_with_water_pixels"]
    if usable == 0:
        modes.append(_mode("FM10", 10, "no scene in the window has water pixels", reviewed))
    elif usable < MIN_SCENES:
        modes.append(_mode("FM06", 8 if usable == 1 else 6, f"{usable} usable water scenes "
                           f"(fewer than {MIN_SCENES})", reviewed))
    if obs["scenes_in_window"]:
        kept = obs["scenes_under_cloud_limit"] / obs["scenes_in_window"]
        if kept < 0.5:
            modes.append(_mode("FM07", _occurrence(1 - kept), f"{obs['scenes_under_cloud_limit']} of "
                               f"{obs['scenes_in_window']} scenes passed the cloud limit", reviewed))
    if usable:
        modes.append(_mode("FM08", 3, "NDCI responds to chlorophyll from any phytoplankton", reviewed))
    return modes


def _alphaearth_modes(obs, reviewed):
    if not obs.get("images"):
        return [_mode("FM10", 10, "no embedding image covers the site and year", reviewed)]
    return [_mode("FM09", 3, f"64-dimensional embedding, model version {obs.get('model_version')}", reviewed)]


def _search_modes(obs, reviewed):
    if not obs.get("total"):
        return [_mode("FM11", 10, f"query returned no hits: {str(obs.get('query'))[:120]}", reviewed)]
    missing = obs.get("missing_ids") or []
    asked = len(obs.get("search_ids") or [])
    if missing and asked and (o := _occurrence(len(missing) / asked)):
        return [_mode("FM11", o, f"{len(missing)} of {asked} search hits could not be retrieved", reviewed)]
    return []


def _items(manifest):
    """One release item per completed or failed action, with the node ids that stand for it."""
    nodes = {n["id"]: n for n in manifest["graph"]["nodes"]}
    items = []
    for action in manifest["actions"]:
        obs = action.get("observation") or {}
        name = action["action"].get("action")
        node = obs.get("summary_node") or obs.get("inventory_node") or f"action:{action['index']}"
        refs = {node, f"action:{action['index']}", *obs.get("record_ids", [])}
        status = nodes.get(node, {}).get("status", action["status"])
        items.append({"node": node, "action": action["index"], "operation": obs.get("operation", name),
                      "status": action["status"], "node_status": status, "refs": refs, "observation": obs})
    return items


def _is_review(post):
    """Concerns, limitations and answers examine an output; support, questions and agreement do not."""
    return post["kind"] in ("concern", "limitation") or (post["kind"] == "reply" and post.get("stance") == "answer")


def _concerns(forum):
    """Unresolved forum concerns, with the node ids each one cites."""
    if forum is None:
        return []
    posts = {p["id"]: p for p in forum["posts"]}
    return [{"post": c["post"], "severity": c["severity"], "status": c["status"],
             "refs": set(posts[c["post"]]["refs"]), "text": posts[c["post"]]["text"]}
            for c in forum["synthesis"]["concerns"] if c["status"] != "resolved"]


def _concern_modes(concerns, refs, reviewed):
    modes = []
    for c in concerns:
        if c["refs"] & refs:
            mode_id = "FM12" if c["severity"] == "major" else "FM13"
            modes.append(_mode(mode_id, 10 if c["status"] == "open" else 6,
                               f"{c['post']} ({c['status']}): {c['text'][:160]}", reviewed))
    return modes


def assess(manifest: dict, forum: dict = None) -> dict:
    """Return the gate record for one manifest and an optional review forum bound to it."""
    gates, items = [], []
    check = verify_agent_manifest(manifest)
    gates.append({"id": "G0-manifest", "passed": bool(check.get("valid")),
                  "detail": "manifest verifies" if check.get("valid") else str(check.get("reason", "invalid"))[:300]})
    if forum is not None:
        from .forum import verify_forum
        bound = verify_forum(forum, manifest)
        gates.append({"id": "G0-forum", "passed": bool(bound.get("valid")),
                      "detail": "forum verifies against the manifest" if bound.get("valid")
                      else str(bound.get("reason", "invalid"))[:300]})
    record = {"schema_version": SCHEMA_VERSION, "kind": "sewall_risk_gate",
              "manifest_digest": manifest.get("content_digest"),
              "forum_digest": forum.get("content_digest") if isinstance(forum, dict) else None,
              "catalog_digest": digest(CATALOG),
              "thresholds": {"hold_rpn": HOLD_RPN, "limit_rpn": LIMIT_RPN, "high_severity": HIGH_SEVERITY,
                             "likely": LIKELY, "min_scenes": MIN_SCENES}}
    if not all(g["passed"] for g in gates):
        record.update(gates=gates, items=[], decision="blocked", released=[], held=[], blocked=[],
                      limitations=LIMITATIONS)
        record["content_digest"] = digest(record)
        return record

    status = manifest["status"]
    run_gate = "blocked" if status in _RUN_BLOCK else "hold_for_validation" if status in _RUN_HOLD else "release"
    gates.append({"id": "G1-run-status", "passed": run_gate == "release",
                  "detail": f"run status {status} ({manifest['stop_reason']})"})
    concerns = _concerns(forum)
    cited = set(manifest.get("metadata_summary", {}).get("record_ids", []) or [])
    if forum is not None:
        cited |= {ref for post in forum["posts"] if _is_review(post) for ref in post["refs"]}
    contained = 0
    for item in _items(manifest):
        reviewed = bool(item["refs"] & cited)
        out = {"node": item["node"], "action": item["action"], "operation": item["operation"],
               "reviewed": reviewed, "modes": []}
        if item["status"] != "completed":
            out.update(decision="blocked", reason=f"action {item['status']}; no output to release")
        elif item["node_status"] in ("quarantined", "blocked"):
            contained += 1
            out.update(decision="blocked", reason=f"node {item['node_status']} by the integrity supervisor")
        else:
            obs, op = item["observation"], item["operation"]
            if op == "flood_sensors":
                out["modes"] = _flood_modes(obs, reviewed)
            elif op == "chlorophyll_timeseries":
                out["modes"] = _chlorophyll_modes(obs, reviewed)
            elif op == "alphaearth_context":
                out["modes"] = _alphaearth_modes(obs, reviewed)
            elif op == "search":
                out["modes"] = _search_modes(obs, reviewed)
            out["modes"] += _concern_modes(concerns, item["refs"], reviewed)
            out["decision"] = _worst([run_gate, _decide(out["modes"])])
        out["max_rpn"] = max((m["rpn"] for m in out["modes"]), default=0)
        items.append(out)
    gates.append({"id": "G2-containment", "passed": contained == 0,
                  "detail": f"{contained} outputs quarantined or blocked by the supervisor"})
    gates.append({"id": "G3-peer-review", "passed": forum is not None and not any(
                      c["severity"] == "major" for c in concerns),
                  "detail": "no forum supplied; detection ratings raised for every output" if forum is None else
                  f"forum verdict {forum['synthesis']['verdict']}; {len(concerns)} unresolved concerns"})

    # The run's result depends on every output, so it is assessed last.
    result_reviewed = "result" in cited or forum is not None
    result_modes = []
    failed = [i for i in items if i["decision"] == "blocked" and "action" in i.get("reason", "")]
    if failed:
        result_modes.append(_mode("FM14", _occurrence(len(failed) / len(items)),
                                  f"{len(failed)} of {len(items)} actions failed", result_reviewed))
    if not manifest["records"]:
        result_modes.append(_mode("FM15", 10, "the run retrieved no literature or sequence records",
                                  result_reviewed))
    weak = [i["node"] for i in items if i["decision"] in ("hold_for_validation", "blocked") and i not in failed]
    if weak:
        result_modes.append(_mode("FM16", _occurrence(len(weak) / len(items)),
                                  f"{len(weak)} of {len(items)} outputs were held or blocked", result_reviewed))
    result_modes += _concern_modes(concerns, _RUN_ANCHORS, result_reviewed)
    result = {"node": "result", "action": None, "operation": "run_result", "reviewed": result_reviewed,
              "modes": result_modes,
              "decision": _worst([run_gate, _decide(result_modes), "hold_for_validation" if weak else "release"]),
              "max_rpn": max((m["rpn"] for m in result_modes), default=0)}
    items.append(result)
    risky = sum(i["decision"] == "hold_for_validation" for i in items)
    gates.append({"id": "G4-risk", "passed": risky == 0,
                  "detail": f"{risky} outputs held for validation"})
    record.update(gates=gates, items=items, decision=result["decision"],
                  released=[i["node"] for i in items if i["decision"] in DECISIONS[:2]],
                  held=[i["node"] for i in items if i["decision"] == "hold_for_validation"],
                  blocked=[i["node"] for i in items if i["decision"] == "blocked"],
                  limitations=LIMITATIONS)
    record["content_digest"] = digest(record)
    return record


def verify_risk_gate(record: dict, manifest: dict, forum: dict = None) -> dict:
    """Recompute the gate from its inputs and compare; no model or source is called."""
    try:
        if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported risk gate record")
        if digest({k: v for k, v in record.items() if k != "content_digest"}) != record.get("content_digest"):
            raise ValueError("Risk gate content digest mismatch")
        if record["manifest_digest"] != manifest.get("content_digest"):
            raise ValueError("Risk gate is bound to a different manifest")
        if record["forum_digest"] != (forum or {}).get("content_digest"):
            raise ValueError("Risk gate is bound to a different forum")
        if record["catalog_digest"] != digest(CATALOG):
            raise ValueError("Failure-mode catalog changed since the gate ran")
        if assess(deepcopy(manifest), deepcopy(forum))["content_digest"] != record["content_digest"]:
            raise ValueError("Recomputed gate does not match the record")
        return {"valid": True, "decision": record["decision"], "content_digest": record["content_digest"]}
    except (KeyError, TypeError, ValueError) as exc:
        return {"valid": False, "reason": str(exc)[:300]}


def render_risk_html(record: dict) -> str:
    """Self-contained HTML page for a gate record."""
    e = html.escape
    gates = "".join(f"<tr><td>{e(g['id'])}</td><td class='{'ok' if g['passed'] else 'no'}'>"
                    f"{'pass' if g['passed'] else 'flag'}</td><td>{e(g['detail'])}</td></tr>" for g in record["gates"])
    rows = []
    for item in record["items"]:
        modes = "".join(f"<li><b>{e(m['id'])}</b> {e(m['mode'])}: S{m['severity']} O{m['occurrence']} "
                        f"D{m['detection']} = <b>{m['rpn']}</b>. {e(m['evidence'])}. "
                        f"<span class='muted'>{e(m['mitigation'])}</span></li>" for m in item["modes"])
        reason = f"<li>{e(item['reason'])}</li>" if item.get("reason") else ""
        rows.append(f"<tr><td><code>{e(item['node'])}</code><div class='muted'>{e(str(item['operation']))}"
                    f"{'' if item['reviewed'] else ' &middot; not reviewed'}</div></td>"
                    f"<td class='d {e(item['decision'])}'>{e(item['decision'].replace('_', ' '))}</td>"
                    f"<td>{item['max_rpn']}</td><td><ul>{reason}{modes or '<li>No active failure mode</li>'}</ul></td></tr>")
    t = record["thresholds"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sewall.ai release gate</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1d232a; --muted:#5d6670; --line:#d9d6cf; --ok:#2f7d4f; --lim:#7a6a2f;
  --hold:#b4532a; --block:#8e2a2a; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#16191d; --fg:#e6e3dd; --muted:#9aa3ad; --line:#333a42;
  --ok:#6fbf8c; --lim:#cdb969; --hold:#e08a5f; --block:#e07070; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, sans-serif; margin:0 auto;
  max-width:1000px; padding:24px 16px; }}
h1 {{ font-size:1.4rem; margin:0 0 .3rem; }} .muted {{ color:var(--muted); font-size:.85rem; }}
.wrap {{ overflow-x:auto; }} table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
td, th {{ border-bottom:1px solid var(--line); padding:.35rem; text-align:left; vertical-align:top; }}
ul {{ margin:0; padding-left:1.1rem; }} code {{ font-size:.75rem; overflow-wrap:anywhere; }}
.ok, .d.release {{ color:var(--ok); }} .d.release_with_limitations {{ color:var(--lim); }}
.no, .d.hold_for_validation {{ color:var(--hold); }} .d.blocked {{ color:var(--block); }}
.d, .decision {{ font-weight:600; }} .decision {{ font-size:1.1rem; }}
</style></head><body>
<h1>Release gate</h1>
<div class="muted">Manifest <code>{e(str(record["manifest_digest"])[:16])}</code> &middot; forum
<code>{e(str(record["forum_digest"] or "none")[:16])}</code> &middot; gate <code>{e(record["content_digest"][:16])}</code></div>
<p class="decision d {e(record["decision"])}">Run result: {e(record["decision"].replace("_", " "))}</p>
<p class="muted">{len(record["released"])} outputs released (with or without limitations), {len(record["held"])} held,
{len(record["blocked"])} blocked. Hold at RPN &ge; {t["hold_rpn"]} or severity &ge; {t["high_severity"]} with
occurrence &ge; {t["likely"]}; limitations at RPN &ge; {t["limit_rpn"]}.</p>
<h2>Gates</h2>
<div class="wrap"><table><tr><th>Gate</th><th>Status</th><th>Detail</th></tr>{gates}</table></div>
<h2>Outputs</h2>
<div class="wrap"><table><tr><th>Output</th><th>Decision</th><th>Max RPN</th><th>Failure modes</th></tr>
{"".join(rows) or "<tr><td colspan='4'>No outputs assessed: an integrity gate failed.</td></tr>"}</table></div>
<h2>Limitations</h2>
<ul>{"".join(f"<li>{e(x)}</li>" for x in record["limitations"])}</ul>
</body></html>
"""

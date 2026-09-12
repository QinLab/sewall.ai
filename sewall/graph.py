"""Deterministic fixture controller with evidence graph and auditable graph revisions.

This coordinates a small allowlist of metadata demonstrations. Production scientific
jobs belong in existing workflow systems. Repository text is never executable code.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from .fixtures import FOCI, SCENARIOS, SOURCES, fixture_source
from .policy import draft_access_request, evaluate_policy


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def plan_research(question, focus="auto", scenario="coastal", max_steps=30):
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise ValueError("Question must contain 1 to 4000 characters")
    if focus not in ("auto", *FOCI):
        raise ValueError("Unknown research focus")
    if scenario not in SCENARIOS:
        raise ValueError("Unknown fixture scenario")
    if type(max_steps) is not int or not 1 <= max_steps <= 100:
        raise ValueError("max_steps must be an integer between 1 and 100")
    if focus == "auto":
        words = question.lower()
        molecular = any(w in words for w in ("gene", "genotype", "expression", "molecular", "geo "))
        landscape = any(w in words for w in ("habitat", "landscape", "alphaearth", "spatial"))
        traits = any(w in words for w in ("trait", "eol", "organism"))
        focus = "cross-scale" if sum((molecular, landscape, traits)) > 1 else (
            "molecular" if molecular else "landscape" if landscape else "traits" if traits else "cross-scale")
    return {
        "question": question.strip(), "focus": focus, "scenario": scenario,
        "max_steps": max_steps, "selected_sources": FOCI[focus].copy(),
        "planner": "bounded_rule_based_fixture_planner",
        "mode": "synthetic_fixture", "external_actions": False,
        "purpose": "Inspect candidate metadata links; no biological inference or analysis",
    }


class _Trace:
    def __init__(self, max_steps):
        self.nodes = []
        self.edges = []
        self.events = []
        self.steps = 0
        self.max_steps = max_steps

    def event(self, kind, details):
        item = {"sequence": len(self.events) + 1, "type": kind,
                "details": deepcopy(details),
                "previous_hash": self.events[-1]["hash"] if self.events else "0" * 64}
        item["hash"] = digest(item)
        self.events.append(item)

    def node(self, node_id, kind, label, status="pending", **fields):
        if any(n["id"] == node_id for n in self.nodes):
            raise ValueError("Duplicate graph node")
        node = dict(id=node_id, kind=kind, label=label, status=status, **deepcopy(fields))
        self.nodes.append(node)
        self.event("node_added", node)
        return node

    def state(self, node, status, **details):
        before = node["status"]
        node["status"] = status
        node.setdefault("details", {}).update(deepcopy(details))
        self.event("node_status_changed", {"node_id": node["id"], "before": before,
                                          "after": status, "details": details})

    def edge(self, source, target, relation, **fields):
        edge = dict(source=source, target=target, relation=relation, **deepcopy(fields))
        self.edges.append(edge)
        self.event("edge_added", edge)

    def step(self):
        if self.steps >= self.max_steps:
            raise _BudgetStop
        self.steps += 1


class _BudgetStop(Exception):
    pass


def run_research(question, focus="auto", scenario="coastal", max_steps=30, previous=None):
    plan = plan_research(question, focus, scenario, max_steps)
    inputs = {k: plan[k] for k in ("question", "focus", "scenario", "max_steps")}
    history = []
    parent_digest = None
    if previous is not None:
        if not check_integrity(previous)["valid"]:
            raise ValueError("Cannot refine an invalid manifest")
        if len(previous["history"]) >= 9:
            raise ValueError("Fixture demo supports at most ten revisions")
        history = deepcopy(previous["history"]) + [deepcopy(previous["inputs"])]
        parent_digest = previous["content_digest"]
    trace = _Trace(max_steps)
    trace.event("plan_created", plan)
    if previous is not None:
        old_sources = set(previous["plan"]["selected_sources"])
        new_sources = set(plan["selected_sources"])
        trace.event("question_revised", {
            "parent_digest": parent_digest,
            "old_question": previous["question"], "new_question": plan["question"],
            "sources_added": sorted(new_sources - old_sources),
            "sources_removed": sorted(old_sources - new_sources),
            "invalidation": "Recompute all metadata and dependent checks for the new revision",
        })
    trace.node("question", "question", plan["question"], "completed")
    records, policy_results, failures, drafts = {}, [], [], []
    skipped = []
    try:
        for source_id in plan["selected_sources"]:
            trace.step()
            fixture = fixture_source(source_id, scenario)
            policy_node = trace.node("policy:" + source_id, "policy", "Check access: " + source_id,
                                     source_id=source_id)
            trace.edge("question", policy_node["id"], "depends_on")
            decision = evaluate_policy(fixture["profile"], fixture["request"])
            policy_results.append({"source_id": source_id, "profile": fixture["profile"],
                                   "request": fixture["request"], **decision})
            trace.state(policy_node, "completed" if decision["status"] == "allow" else "blocked",
                        decision=decision)
            if decision["status"] != "allow":
                failures.append("needs_review" if decision["status"] == "review" else "abstained")
                skipped.append(source_id)
                draft = {"source_id": source_id, **draft_access_request(fixture["profile"], fixture["request"])}
                drafts.append(draft)
                node = trace.node("request:" + source_id, "access_request", "Prepare access request",
                                  "needs_review", source_id=source_id, details=draft)
                trace.edge(policy_node["id"], node["id"], "depends_on")
                trace.event("execution_blocked", {"source_id": source_id, "decision": decision["status"]})
                continue
            node = trace.node("source:" + source_id, "source", SOURCES[source_id]["label"],
                              source_id=source_id)
            trace.edge(policy_node["id"], node["id"], "depends_on")
            if fixture["unavailable"]:
                trace.state(node, "failed", reason="Injected fixture source timeout; no result fabricated")
                trace.event("source_failed", {"source_id": source_id, "retry_count": 0})
                failures.append("failed")
                skipped.append(source_id)
                continue
            records[source_id] = fixture["record"]
            trace.state(node, "completed", record=fixture["record"],
                        record_digest=digest(fixture["record"]), skill="metadata.inspect@0.1.0")

        # Required links depend on the selected question, not merely available records.
        # Each rejected link remains in the graph as evidence of the limitation.
        checks = []
        if "geo" in plan["selected_sources"]:
            checks.append(("specimen", "ncbi_genotype", "geo", "specimen_id", "same_specimen"))
        if "eol" in plan["selected_sources"]:
            target = "geo" if "geo" in plan["selected_sources"] else "pubmed"
            checks.append(("taxon", "eol", target, "taxon_id", "taxon_context"))
        if "alphaearth" in plan["selected_sources"]:
            if "geo" in plan["selected_sources"]:
                checks.append(("site", "alphaearth", "geo", "site_id", "site_context"))
            checks.append(("time", "alphaearth", "alphaearth", "period_end", "historical_context"))
        for name, left, right, field, relation in checks:
            trace.step()
            node = trace.node("check:" + name, "verification", "Verify " + name + " linkage")
            for source_id in dict.fromkeys((left, right)):
                source_node = "source:" + source_id
                if any(n["id"] == source_node for n in trace.nodes):
                    trace.edge(source_node, node["id"], "depends_on")
            if left not in records or right not in records:
                trace.state(node, "blocked", reason="Required source unavailable or access unresolved")
                failures.append("abstained")
                continue
            a, b = records[left], records[right]
            if name == "time":
                valid = a["period_end"] < a["forecast_cutoff"] and a["available_at"] <= a["forecast_cutoff"]
                reason = "Feature interval and availability must precede the historical forecast cutoff"
            else:
                valid = bool(a.get(field)) and a.get(field) == b.get(field)
                reason = "Explicit matching " + field + " required; a taxon match cannot substitute for a specimen"
            evidence = {"method": "explicit-field", "field": field, "left_record": a["record_id"],
                        "right_record": b["record_id"], "origin": "synthetic_fixture",
                        "left_value": a.get(field), "right_value": b.get(field),
                        "scope": relation, "supports_causality": False}
            if name == "time":
                evidence.update(period_end=a["period_end"], available_at=a["available_at"],
                                forecast_cutoff=a["forecast_cutoff"])
            trace.state(node, "completed" if valid else "rejected", reason=reason, evidence=evidence,
                        skill="link.verify@0.1.0")
            if valid:
                trace.edge("source:" + left, "source:" + right, relation, evidence=evidence)
            else:
                failures.append("abstained")
                trace.event("link_rejected", {"check": name, "reason": reason})
    except _BudgetStop:
        failures.append("failed")
        trace.node("budget", "guard", "Execution budget exhausted", "failed")
        trace.event("budget_exhausted", {"max_steps": max_steps, "completed_steps": trace.steps})

    status = next((s for s in ("failed", "needs_review", "abstained") if s in failures), "completed")
    limitations = [
        "Synthetic fixtures and invented policies only. Provider labels do not certify real access or data overlap.",
        "Completion means a fixture metadata map was checked. No biological finding or validated forecast is produced.",
        "Taxon, literature, and site context do not establish paired measurements, causal effects, or ecological outcomes.",
        "No real source computation, DUA approval, signature, controlled access, or data transfer occurs in this run.",
        "Hashes detect changes relative to a retained digest; they are not signatures or independent proof of authenticity.",
        "Planning is rule based. An external model may use the MCP tools; model performance has not been evaluated.",
    ]
    if status != "completed":
        limitations.append("Required evidence or authorization is unresolved. Dependent conclusions are withheld.")
    trace.node("result", "result", "Metadata evidence map; biological claims withheld", status)
    for node in trace.nodes.copy():
        if node["kind"] in ("verification", "source", "access_request", "guard"):
            trace.edge(node["id"], "result", "depends_on")
    trace.event("run_finished", {"status": status, "scientific_claim_count": 0})
    result = {
        "schema_version": "0.1.0", "mode": "synthetic_fixture",
        "run_id": "demo-" + digest({"inputs": inputs, "parent": parent_digest})[:16],
        "question": plan["question"], "revision": len(history) + 1, "status": status,
        "inputs": inputs, "plan": plan, "parent_digest": parent_digest, "history": history,
        "graph": {"nodes": trace.nodes, "edges": trace.edges}, "events": trace.events,
        "policies": policy_results, "access_requests": drafts, "claims": [],
        "limitations": limitations,
        "metrics": {"selected_sources": len(plan["selected_sources"]), "inspected_sources": len(records),
                    "blocked_or_failed_sources": len(skipped), "steps": trace.steps,
                    "network_bytes": 0, "scientific_claims": 0,
                    "verified_links": sum("evidence" in e for e in trace.edges)},
    }
    result["content_digest"] = digest(result)
    return result


def check_integrity(manifest):
    """Check a retained manifest, event chain, and graph structure, without execution."""
    try:
        if not isinstance(manifest, dict):
            raise ValueError("Manifest must be an object")
        payload = {k: v for k, v in manifest.items() if k != "content_digest"}
        if digest(payload) != manifest.get("content_digest"):
            raise ValueError("Manifest content digest mismatch")
        if manifest["schema_version"] != "0.1.0" or manifest["mode"] != "synthetic_fixture":
            raise ValueError("Unsupported manifest version or mode")
        if not isinstance(manifest["history"], list) or len(manifest["history"]) > 9:
            raise ValueError("Invalid revision history")
        if not isinstance(manifest["events"], list) or not manifest["events"]:
            raise ValueError("Events must be a nonempty array")
        previous_hash = "0" * 64
        for i, event in enumerate(manifest["events"], 1):
            if not isinstance(event, dict):
                raise ValueError("Each event must be an object")
            payload = {k: v for k, v in event.items() if k != "hash"}
            if event["sequence"] != i or event["previous_hash"] != previous_hash or digest(payload) != event["hash"]:
                raise ValueError("Event chain mismatch")
            previous_hash = event["hash"]
        ids = [n["id"] for n in manifest["graph"]["nodes"]]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate graph nodes")
        for edge in manifest["graph"]["edges"]:
            if edge["source"] not in ids or edge["target"] not in ids:
                raise ValueError("Dangling graph edge")
        return {"valid": True, "reason": "Content and event digests match; graph references resolve"}
    except (ValueError, KeyError, TypeError, OverflowError, AttributeError) as exc:
        return {"valid": False, "reason": str(exc)}


def replay_manifest(manifest):
    integrity = check_integrity(manifest)
    if not integrity["valid"]:
        return {"valid": False, "replayed": False, "reason": integrity["reason"]}
    try:
        prior = None
        for inputs in [*manifest["history"], manifest["inputs"]]:
            if set(inputs) != {"question", "focus", "scenario", "max_steps"}:
                raise ValueError("Invalid replay inputs")
            prior = run_research(**inputs, previous=prior)
        matches = prior["content_digest"] == manifest["content_digest"]
        return {"valid": matches, "replayed": True, "expected_digest": manifest["content_digest"],
                "actual_digest": prior["content_digest"],
                "reason": "Deterministic fixture replay matches" if matches else "Replay diverged"}
    except (ValueError, KeyError, TypeError) as exc:
        return {"valid": False, "replayed": False, "reason": str(exc)}

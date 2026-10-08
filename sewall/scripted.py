"""Deterministic planner and reviewer for demonstrations without model keys.

A script is a fixed list of Skill actions. The controller checks scripted
actions exactly as it checks model proposals. The scripted reviewer reports
what was retrieved and makes no assessment of content. A value of the form
"$record:DATABASE:N" names the Nth returned record from that database. A step
whose placeholder cannot be resolved is passed on unresolved, so the controller
rejects it and the run fails rather than finishing with a step missing. Steps
left when the action budget runs out are still proposed, so the controller
records the run as budget_exhausted rather than completed.

An optional "origin" of "mcp_host" records that an MCP host model wrote the
steps before execution; the default "fixed" means a person wrote them.

An optional "critiques" list injects labeled faults for safe-stop
demonstrations: {"after_action": N, "type": T, "reason": R} makes the
scripted monitor raise a critical critique of type T against action node N.
"""

from copy import deepcopy
import re

from .graph import digest
from .safety import CRITIQUE_TYPES


MAX_STEPS = 8
_PLACEHOLDER = re.compile(r"\$record:(pubmed|gds|bioproject):([0-9])\Z", re.ASCII)
_NO_ASSESSMENT = "No model read or assessed the retrieved metadata; no biological analysis performed."
_GAPS = {"fixed": ["Scripted demonstration: actions were fixed in advance, not chosen by a model.", _NO_ASSESSMENT],
         "mcp_host": ["Actions were written in advance by the MCP host model and executed as a fixed plan; "
                      "Sewall.ai did not observe that model, its prompt or its reasoning.", _NO_ASSESSMENT]}


class ScriptError(ValueError):
    """A script failed validation."""


def load_script(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - {"name", "description", "steps", "gaps", "critiques", "origin"} or not {"name", "steps"} <= set(value):
        raise ScriptError("Script fields must be name, steps and optional description, gaps, critiques and origin")
    if value.get("origin", "fixed") not in _GAPS:
        raise ScriptError("Script origin must be fixed or mcp_host")
    if not isinstance(value["name"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value["name"]):
        raise ScriptError("Script name must be lowercase letters, digits and hyphens")
    steps = value["steps"]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise ScriptError(f"A script needs 1 to {MAX_STEPS} steps")
    for step in steps:
        if (not isinstance(step, dict) or not isinstance(step.get("action"), str) or step["action"] == "finish"
                or any(not isinstance(item, str) for item in step.values())):
            raise ScriptError("Each step must be a Skill action object with string values")
    gaps = value.get("gaps", [])
    if not isinstance(gaps, list) or len(gaps) > 8 or any(not isinstance(item, str) or not item.strip() for item in gaps):
        raise ScriptError("Script gaps must be a short list of text")
    critiques = value.get("critiques", [])
    if not isinstance(critiques, list) or len(critiques) > 4:
        raise ScriptError("Script critiques must be a list of at most 4 injected faults")
    for item in critiques:
        if (not isinstance(item, dict) or set(item) != {"after_action", "type", "reason"}
                or type(item["after_action"]) is not int or not 1 <= item["after_action"] <= MAX_STEPS
                or item["type"] not in CRITIQUE_TYPES
                or not isinstance(item["reason"], str) or not 1 <= len(item["reason"].strip()) <= 400):
            raise ScriptError("Each injected critique needs after_action, a known type and a short reason")
    return deepcopy(value)


class ScriptedClient:
    """Expose complete_json like a model client, returning fixed actions in order."""

    def __init__(self, script: dict):
        self.script = load_script(script)
        self.digest = digest(self.script)
        self._next = 0

    def _resolve(self, step, records):
        resolved = {}
        for key, value in step.items():
            match = _PLACEHOLDER.fullmatch(value)
            if match:
                ids = [record["id"] for record in records if record.get("database") == match.group(1)]
                index = int(match.group(2))
                if index < len(ids):
                    value = ids[index]
            resolved[key] = value
        return resolved

    def _plan(self, payload):
        records = payload.get("records", [])
        if self._next < len(self.script["steps"]):
            self._next += 1
            return self._resolve(self.script["steps"][self._next - 1], records)
        gaps = list(dict.fromkeys([*self.script.get("gaps", []), *_GAPS[self.script.get("origin", "fixed")]]))
        return {"action": "finish", "reason": "Scripted steps are complete", "proposed_links": [],
                "record_ids": [record["id"] for record in records][:15], "gaps": gaps[:12]}

    def _review(self, payload):
        records = payload.get("records", [])
        counts = {}
        for record in records:
            counts[record["database"]] = counts.get(record["database"], 0) + 1
        found = ", ".join(f"{count} {database}" for database, count in sorted(counts.items())) or "no"
        inventories = [item["observation"] for item in payload.get("recent_actions", [])
                       if item.get("action", {}).get("action") == "taxon_inventory" and "total" in item.get("observation", {})]
        text = f"Scripted review: retrieved {found} public metadata records."
        for item in inventories:
            text += f" GenBank lists {item['total']} nucleotide records for {item['taxon']}"
            if item.get("tsa_records"):
                text += f", of which {item['tsa_records']} are transcriptome assembly contigs"
            text += "."
        text += f" {len(payload.get('verified_links', []))} links were checked against explicit source fields."
        return {"summary": text[:2000], "record_ids": [record["id"] for record in records][:15],
                "limitations": ["This summary was produced by a fixed script, not a model.",
                                "Record retrieval does not validate any biological claim."]}

    def monitor(self, phase, node_id, snapshot):
        """Trusted integrity monitor that raises only the script's injected faults."""
        critiques = [{"type": item["type"], "severity": "critical", "task_ids": [node_id],
                      "reason": "Injected fault for demonstration: " + item["reason"]}
                     for item in self.script.get("critiques", [])
                     if phase == "after_action" and node_id == f"action:{item['after_action']}"]
        return {"integrity": "valid", "critiques": critiques}

    def complete_json(self, system_prompt, payload):
        output = self._review(payload) if "verified_links" in payload else self._plan(payload)
        return {"output": deepcopy(output), "provider": "scripted", "model": self.script["name"],
                "model_version": "script-" + self.digest[:16], "finish_reason": "STOP",
                "request_id": None, "response_sha256": digest(output), "latency_seconds": 0.0,
                "usage": {}, "attempts": 1}

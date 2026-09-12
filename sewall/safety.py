"""Deterministic safe-stop supervisor for bounded synthetic metadata workflows.

This module does not execute a model, infer biological findings, grant access,
or sandbox callbacks. Trusted, registered callbacks run in-process. Gates stop
future dispatch between callbacks, not a callback that is already running.
"""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
from html import escape
import json
import re


SCENARIOS = (
    "clean", "critical_critique", "unsupported_join", "temporal_leakage",
    "missing_provenance", "monitor_unavailable", "denied_policy", "tampered_evidence",
)
STATES = ("READY", "RUNNING", "SAFE_STOPPED", "COMPLETED")
CRITIQUE_TYPES = frozenset({
    "critical_critique", "unsupported_join", "temporal_leakage",
    "missing_provenance", "monitor_unavailable", "denied_policy", "tampered_evidence",
    "invalid_signal", "unknown_integrity", "invalid_integrity", "invalid_output",
    "dispatch_error", "audit_integrity_failure",
})
_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z", re.ASCII)
_SHA = re.compile(r"[a-f0-9]{64}\Z", re.ASCII)
_GENESIS = "0" * 64
_MAX_TASKS = 16
_MAX_OUTPUT_BYTES = 32_000

LIMITATIONS = [
    "Built-in scenarios use synthetic metadata only and call no external repositories or models.",
    "Completed means the fixture passed its configured gates, not a scientific finding or universal safety guarantee.",
    "The monitor is deterministic in this demonstration; it is not a validated live LLM research-integrity critic.",
    "Registered callbacks are trusted Python code, not sandboxed. An in-flight callback cannot be forcibly stopped here.",
    "Hashes detect changes relative to retained receipts; they are not signatures or independent source authentication.",
    "The built-in scenarios perform no biological, genotype or sequence analysis, permission grants, agreements or external publication.",
    "Custom registered callbacks are not runtime-metered. Their side effects and suitability require separate review.",
    "A stopped run cannot resume. Remediation needs a separate reviewed run; this prototype cannot grant that approval.",
]


def _digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _text(value, maximum=1200):
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            and not any(ord(c) < 32 and c not in "\n\t" for c in value))


def _identifier(value):
    return isinstance(value, str) and _ID.fullmatch(value) is not None


class _IntegrityFault(ValueError):
    def __init__(self, kind, reason):
        super().__init__(reason)
        self.kind = kind


@dataclass(frozen=True)
class TaskSpec:
    """A task uses an explicitly registered callback and earlier dependencies."""

    task_id: str
    callback: str
    dependencies: tuple = ()


def _validate_record(record):
    if not isinstance(record, dict) or set(record) != {"id", "title", "provenance"}:
        raise _IntegrityFault("missing_provenance", "Metadata lacks its exact identity and provenance fields.")
    if not _identifier(record["id"]) or not _text(record["title"], 1200):
        raise _IntegrityFault("invalid_output", "Metadata identity or title is invalid.")
    provenance = record["provenance"]
    if (not isinstance(provenance, dict)
            or set(provenance) != {"source", "payload_sha256"}
            or not _text(provenance.get("source"), 500)):
        raise _IntegrityFault("missing_provenance", "Metadata provenance is missing or malformed.")
    if (not isinstance(provenance["payload_sha256"], str)
            or not _SHA.fullmatch(provenance["payload_sha256"])
            or provenance["payload_sha256"] != _digest({"id": record["id"], "title": record["title"]})):
        raise _IntegrityFault("tampered_evidence", "Metadata differs from its supplied payload digest.")


def _validate_output(value):
    if not isinstance(value, dict) or set(value) != {"metadata", "provisional_summary"}:
        raise _IntegrityFault("invalid_output", "Callback output has missing or unrecognized fields.")
    try:
        size = len(json.dumps(value, allow_nan=False).encode("utf-8"))
    except (TypeError, ValueError, RecursionError):
        raise _IntegrityFault("invalid_output", "Callback output is not bounded JSON metadata.") from None
    if size > _MAX_OUTPUT_BYTES:
        raise _IntegrityFault("invalid_output", "Callback output exceeds the metadata byte limit.")
    records, summary = value["metadata"], value["provisional_summary"]
    if (not isinstance(records, list) or len(records) > 10
            or not isinstance(summary, str) or len(summary) > 1200
            or (summary and not _text(summary))):
        raise _IntegrityFault("invalid_output", "Callback output violates metadata or summary bounds.")
    for record in records:
        _validate_record(record)
    if len({record["id"] for record in records}) != len(records):
        raise _IntegrityFault("invalid_output", "A callback returned duplicate metadata identities.")
    return deepcopy(value)


def _validate_critique(value, task_ids):
    if not isinstance(value, dict) or set(value) != {"type", "severity", "task_ids", "reason"}:
        raise ValueError("A critique must use the exact typed schema.")
    if (not isinstance(value["type"], str) or value["type"] not in CRITIQUE_TYPES
            or value["severity"] != "critical" or not _text(value["reason"])):
        raise ValueError("Unknown critique type, severity or reason.")
    affected = value["task_ids"]
    if (not isinstance(affected, list) or not affected or len(affected) > _MAX_TASKS
            or any(not _identifier(item) or item not in task_ids for item in affected)
            or len(set(affected)) != len(affected)):
        raise ValueError("A critique requires known, unique affected task identifiers.")
    return deepcopy(value)


class SafetySupervisor:
    """Single-run state machine with deterministic pre/post dispatch gates.

    ``monitor(phase, task_id, snapshot)`` must return exactly
    ``{"integrity": "valid|invalid|unknown", "critiques": [...]}``.
    A callback receives a copy of the current snapshot and returns exactly
    ``{"metadata": [...], "provisional_summary": "..."}``.

    Construction is a trusted-code configuration step. Model/source text cannot
    supply callbacks, change the task graph, authorize dispatch or resume a run.
    """

    def __init__(self, tasks, callbacks, monitor, *, scenario="custom", fixture_mode=False):
        if (not isinstance(tasks, (list, tuple)) or not 1 <= len(tasks) <= _MAX_TASKS
                or not isinstance(callbacks, dict) or not callable(monitor)
                or not _text(scenario, 100) or not isinstance(fixture_mode, bool)):
            raise ValueError("A bounded task list, callback registry and monitor are required.")
        seen = set()
        for task in tasks:
            if (not isinstance(task, TaskSpec) or not _identifier(task.task_id)
                    or task.task_id in seen or not _identifier(task.callback)
                    or task.callback not in callbacks or not callable(callbacks[task.callback])
                    or not isinstance(task.dependencies, tuple)
                    or any(not _identifier(item) or item not in seen for item in task.dependencies)
                    or len(set(task.dependencies)) != len(task.dependencies)):
                raise ValueError("Tasks require unique IDs, registered callbacks and earlier dependencies.")
            seen.add(task.task_id)
        self._specs = tuple(tasks)
        self._callbacks = dict(callbacks)
        self._monitor = monitor
        self._scenario = scenario
        self._fixture_mode = fixture_mode
        self._state = "READY"
        self._tasks = {task.task_id: {"id": task.task_id, "dependencies": list(task.dependencies),
                                     "status": "PENDING", "output": None} for task in tasks}
        self._receipts = {}
        self._summaries = []
        self._quarantine = []
        self._critiques = []
        self._events = []
        self._dispatches = []
        self._event("created", {"task_ids": list(self._tasks), "max_dispatches": len(tasks)})

    @property
    def state(self):
        return self._state

    def _event(self, event, details):
        item = {"index": len(self._events), "event": event, "state": self._state,
                "details": deepcopy(details),
                "previous_sha256": self._events[-1]["sha256"] if self._events else _GENESIS}
        item["sha256"] = _digest(item)
        self._events.append(item)

    def _payload(self):
        stopped = self._state == "SAFE_STOPPED"
        completed = self._state == "COMPLETED"
        notice = (
            "STOPPED, NOT SUCCESSFUL. Research integrity is unresolved. All claims and provisional "
            "summaries are withheld. Only verified, unaffected metadata may be inspected as evidence. "
            "No biological findings were produced. A separate reviewed run is required."
            if stopped else
            ("SYNTHETIC CHECKS COMPLETED. " if self._fixture_mode else "REGISTERED CALLBACK CHECKS COMPLETED. ")
            + "This is a metadata-control demonstration, not a scientific success or a biological "
            "finding. Provisional text is not a validated conclusion."
            if completed else
            "INCOMPLETE PROTOTYPE RUN. No conclusion is authorized for release."
        )
        retained = []
        for task in self._tasks.values():
            if task["status"] == "VERIFIED" and task["output"] is not None:
                retained.extend({"task_id": task["id"], "use": "metadata_evidence_only", "record": deepcopy(record)}
                                for record in task["output"]["metadata"])
        return {"schema_version": "sewall-safety-1.0",
                "mode": "synthetic_safety_demo" if self._fixture_mode else "registered_callback_safety_supervisor",
                "scenario": self._scenario, "state": self._state,
                "status": "safe_stopped" if stopped else "completed" if completed else self._state.lower(),
                "safety_notice": notice, "dispatch_frozen": stopped or completed,
                "audit_events_are_historical_not_result_authority": True,
                "same_run_resume_allowed": False, "authorization_granted": False,
                "claims": [], "provisional_summaries": deepcopy(self._summaries) if not stopped else [],
                "tasks": deepcopy(list(self._tasks.values())), "retained_evidence": retained,
                "quarantined_outputs": deepcopy(self._quarantine), "critiques": deepcopy(self._critiques),
                "dispatch_history": list(self._dispatches), "limitations": list(LIMITATIONS),
                "external_calls": 0 if self._fixture_mode else None,
                "external_call_accounting": "zero_by_fixture_construction" if self._fixture_mode else "not_instrumented"}

    def snapshot(self):
        """Return an owned copy. Hashes are receipts, not an authorization token."""
        result = self._payload()
        result["audit_events"] = deepcopy(self._events)
        result["audit_head"] = self._events[-1]["sha256"]
        result["snapshot_sha256"] = _digest(result)
        return result

    def _generated_critique(self, kind, reason, task_ids=None):
        return {"type": kind, "severity": "critical",
                "task_ids": list(task_ids) if task_ids else list(self._tasks), "reason": reason}

    def _safe_stop(self, critiques):
        critiques = [item for item in critiques if item not in self._critiques]
        if self._state == "SAFE_STOPPED" and not critiques:
            return
        # A stopped run never dispatches again, but new critiques may withdraw
        # additional evidence previously believed unaffected.
        affected = {item for critique in self._critiques + critiques for item in critique["task_ids"]}
        while True:
            expanded = affected | {name for name, task in self._tasks.items()
                                   if affected.intersection(task["dependencies"])}
            if expanded == affected:
                break
            affected = expanded
        self._state = "SAFE_STOPPED"
        self._critiques.extend(deepcopy(critiques))
        for name, task in self._tasks.items():
            if name in affected:
                if task["output"] is not None:
                    # Even structurally corrupt evidence must reach a safe terminal state.
                    try:
                        observed_digest = _digest(task["output"])
                    except (TypeError, ValueError, RecursionError):
                        observed_digest = None
                    records = task["output"].get("metadata", []) if isinstance(task["output"], dict) else []
                    record_ids = [record["id"] for record in records if isinstance(record, dict)
                                  and _identifier(record.get("id"))] if isinstance(records, list) else []
                    self._quarantine.append({"task_id": name, "output_sha256": self._receipts.get(name),
                                             "observed_output_sha256": observed_digest,
                                             "record_ids": record_ids,
                                             "status": "withdrawn_not_for_use"})
                    task["output"] = None
                    task["status"] = "QUARANTINED"
                else:
                    task["status"] = "QUARANTINED" if task["status"] == "QUARANTINED" else "BLOCKED"
            elif task["status"] != "VERIFIED":
                # Freeze every outstanding dispatch, including independent work.
                task["output"] = None
                task["status"] = "BLOCKED"
        self._summaries.clear()
        self._event("safe_stop", {"affected_task_ids": sorted(affected), "critiques": deepcopy(critiques),
                                  "snapshot_sha256": _digest(self._payload()),
                                  "task_statuses": {name: task["status"] for name, task in self._tasks.items()},
                                  "claims_withheld": True, "provisional_summaries_withheld": True})

    def submit_critique(self, critique):
        """Apply one typed signal; malformed/unknown input conservatively stops all work."""
        try:
            validated = _validate_critique(critique, self._tasks)
        except (ValueError, TypeError, RecursionError):
            validated = self._generated_critique("invalid_signal", "Malformed or unknown critique; integrity cannot be established.")
        self._safe_stop([validated])
        return self.snapshot()

    def resume(self, *args, **kwargs):
        """No argument, model statement or purported approval can resume this run."""
        raise RuntimeError("Same-run resume is disabled. Remediate and establish a separate reviewed run.")

    def _check_internal_integrity(self):
        previous = _GENESIS
        for index, event in enumerate(self._events):
            payload = {key: value for key, value in event.items() if key != "sha256"}
            if (event.get("index") != index or event.get("previous_sha256") != previous
                    or event.get("sha256") != _digest(payload)):
                raise _IntegrityFault("audit_integrity_failure", "Stored audit events no longer match their receipts.")
            previous = event["sha256"]
        for name, task in self._tasks.items():
            if task["output"] is not None:
                if _digest(task["output"]) != self._receipts.get(name):
                    raise _IntegrityFault("tampered_evidence", "Stored task output no longer matches its retained receipt.")
                for record in task["output"]["metadata"]:
                    _validate_record(record)

    def _gate(self, phase, task_id):
        if self._state != "RUNNING":
            return False
        try:
            self._check_internal_integrity()
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            kind = exc.kind if isinstance(exc, _IntegrityFault) else "tampered_evidence"
            self._safe_stop([self._generated_critique(kind, "Internal integrity check failed; all outputs require review.")])
            return False
        try:
            signal = self._monitor(phase, task_id, self.snapshot())
        except Exception:
            self._safe_stop([self._generated_critique("monitor_unavailable", "The integrity monitor failed; dispatch cannot continue.")])
            return False
        if self._state != "RUNNING":
            # A trusted callback may submit a critique during monitoring.
            return False
        try:
            if (not isinstance(signal, dict) or set(signal) != {"integrity", "critiques"}
                    or not isinstance(signal["integrity"], str)
                    or signal["integrity"] not in {"valid", "invalid", "unknown"}
                    or not isinstance(signal["critiques"], list)
                    or len(signal["critiques"]) > _MAX_TASKS):
                raise ValueError("Invalid monitor schema.")
            critiques = [_validate_critique(value, self._tasks) for value in signal["critiques"]]
            if signal["integrity"] != "valid":
                # Unknown scope or a failing integrity status cannot be narrowed by model text.
                kind = "unknown_integrity" if signal["integrity"] == "unknown" else "invalid_integrity"
                critiques.append(self._generated_critique(kind, "Monitor did not establish valid integrity for this run."))
        except (ValueError, TypeError, RecursionError):
            critiques = [self._generated_critique("invalid_signal", "Malformed monitor output; integrity cannot be established.")]
        if critiques:
            self._safe_stop(critiques)
            return False
        self._event("gate_passed", {"phase": phase, "task_id": task_id, "integrity": "valid"})
        return True

    def run(self):
        """Execute at most the configured tasks once, gated before and after each call."""
        if self._state in {"SAFE_STOPPED", "COMPLETED"}:
            return self.snapshot()
        if self._state != "READY":
            raise RuntimeError("Reentrant dispatch is not allowed.")
        self._state = "RUNNING"
        self._event("started", {})
        for spec in self._specs:
            if not self._gate("before", spec.task_id):
                break
            task = self._tasks[spec.task_id]
            if any(self._tasks[name]["status"] != "VERIFIED" for name in spec.dependencies):
                self._safe_stop([self._generated_critique("invalid_integrity", "A task dependency is not verified.", [spec.task_id])])
                break
            task["status"] = "RUNNING"
            self._dispatches.append(spec.task_id)
            self._event("dispatch", {"task_id": spec.task_id, "callback": spec.callback})
            try:
                output = self._callbacks[spec.callback](self.snapshot())
                if self._state != "RUNNING":
                    # Never adopt an output returned after an externally submitted stop.
                    break
                output = _validate_output(output)
            except _IntegrityFault as exc:
                self._safe_stop([self._generated_critique(exc.kind, str(exc), [spec.task_id])])
                break
            except Exception:
                self._safe_stop([self._generated_critique("dispatch_error", "A registered callback failed; dependent work is blocked.", [spec.task_id])])
                break
            task["output"] = {"metadata": output["metadata"]}
            task["status"] = "PROVISIONAL"
            self._receipts[spec.task_id] = _digest(task["output"])
            if output["provisional_summary"]:
                self._summaries.append({"task_id": spec.task_id, "text": output["provisional_summary"],
                                        "status": "provisional_not_a_finding"})
            self._event("output_pending_review", {"task_id": spec.task_id,
                        "output_sha256": self._receipts[spec.task_id],
                        "summary_sha256": _digest(output["provisional_summary"])})
            if not self._gate("after", spec.task_id):
                break
            task["status"] = "VERIFIED"
            self._event("output_verified", {"task_id": spec.task_id, "scope": "configured_fixture_checks_only"})
        if self._state == "RUNNING":
            self._state = "COMPLETED"
            # A later critique may withdraw these outputs. Do not retain their raw
            # contents or provisional summaries inside an immutable completion event.
            self._event("completed", {"snapshot_sha256": _digest(self._payload()),
                                      "verified_tasks": len(self._tasks), "biological_claims": 0})
        return self.snapshot()


def verify_safety_manifest(manifest):
    """Check receipts and release invariants without executing saved content."""
    errors = []
    try:
        if not isinstance(manifest, dict):
            raise ValueError("Manifest is not an object.")
        payload = {key: value for key, value in manifest.items() if key != "snapshot_sha256"}
        if manifest.get("snapshot_sha256") != _digest(payload):
            errors.append("Snapshot digest mismatch.")
        mode = manifest.get("mode")
        if manifest.get("schema_version") != "sewall-safety-1.0" or mode not in {"synthetic_safety_demo", "registered_callback_safety_supervisor"}:
            errors.append("Unknown safety manifest schema or mode.")
        state = manifest.get("state")
        if state not in STATES or manifest.get("status") != state.lower():
            errors.append("State/status mismatch.")
        if (manifest.get("claims") != [] or manifest.get("same_run_resume_allowed") is not False
                or manifest.get("authorization_granted") is not False
                or manifest.get("external_calls") != (0 if mode == "synthetic_safety_demo" else None)):
            errors.append("Unauthorized claims, access, resume or external actions.")
        if not isinstance(manifest.get("audit_events"), list) or not manifest["audit_events"]:
            raise ValueError("Audit events are missing.")
        previous = _GENESIS
        for index, event in enumerate(manifest["audit_events"]):
            if not isinstance(event, dict) or set(event) != {"index", "event", "state", "details", "previous_sha256", "sha256"}:
                raise ValueError("Malformed audit event.")
            event_payload = {key: value for key, value in event.items() if key != "sha256"}
            if (event["index"] != index or event["previous_sha256"] != previous
                    or event["sha256"] != _digest(event_payload)):
                errors.append("Audit hash chain mismatch.")
            previous = event["sha256"]
        if manifest.get("audit_head") != previous:
            errors.append("Audit head mismatch.")
        if manifest["audit_events"][-1]["state"] != state:
            errors.append("Audit state mismatch.")
        tasks = manifest["tasks"]
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= _MAX_TASKS:
            raise ValueError("Invalid task collection.")
        ids = [task["id"] for task in tasks]
        if any(not _identifier(value) for value in ids) or len(set(ids)) != len(ids):
            raise ValueError("Invalid task identities.")
        seen = set()
        for task in tasks:
            dependencies = task["dependencies"]
            if (not isinstance(dependencies, list) or any(not _identifier(item) or item not in seen for item in dependencies)
                    or len(set(dependencies)) != len(dependencies)):
                raise ValueError("Invalid or cyclic task dependencies.")
            seen.add(task["id"])
        retained = []
        for task in tasks:
            if task["status"] not in {"PENDING", "RUNNING", "PROVISIONAL", "VERIFIED", "QUARANTINED", "BLOCKED"}:
                errors.append("Invalid task status.")
            if task["status"] in {"QUARANTINED", "BLOCKED"} and task["output"] is not None:
                errors.append("Blocked or quarantined output remains exposed.")
            if task["output"] is not None:
                if set(task["output"]) != {"metadata"}:
                    raise ValueError("Unexpected task output fields.")
                for record in task["output"]["metadata"]:
                    _validate_record(record)
                    if task["status"] == "VERIFIED":
                        retained.append({"task_id": task["id"], "use": "metadata_evidence_only", "record": record})
        if manifest.get("retained_evidence") != retained:
            errors.append("Retained evidence does not match verified task metadata.")
        if state == "SAFE_STOPPED":
            if (manifest.get("provisional_summaries") != [] or manifest.get("dispatch_frozen") is not True
                    or not manifest.get("critiques") or "STOPPED, NOT SUCCESSFUL" not in manifest.get("safety_notice", "")):
                errors.append("Safe-stop release invariants failed.")
            if any(task["status"] not in {"VERIFIED", "QUARANTINED", "BLOCKED"} for task in tasks):
                errors.append("Stopped run contains an executable or provisional task.")
        if state == "COMPLETED" and (any(task["status"] != "VERIFIED" for task in tasks)
                                     or manifest.get("dispatch_frozen") is not True):
            errors.append("Completion invariants failed.")
        for critique in manifest.get("critiques", []):
            _validate_critique(critique, ids)
        affected = {name for critique in manifest.get("critiques", []) for name in critique["task_ids"]}
        while True:
            expanded = affected | {task["id"] for task in tasks if affected.intersection(task["dependencies"])}
            if expanded == affected:
                break
            affected = expanded
        if any(task["status"] == "VERIFIED" and task["id"] in affected for task in tasks):
            errors.append("Critiqued tasks or their dependents remain verified.")
        task_map = {task["id"]: task for task in tasks}
        if any(task["status"] == "VERIFIED" and any(task_map[name]["status"] != "VERIFIED" for name in task["dependencies"])
               for task in tasks):
            errors.append("Verified task depends on unverified evidence.")
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        errors.append("Malformed safety manifest or invalid evidence.")
    return {"valid": not errors, "errors": errors}


def _fixture_record(name, title):
    value = {"id": name, "title": title}
    value["provenance"] = {"source": "fixture:" + name, "payload_sha256": _digest(value)}
    return value


def run_safety_demo(scenario="critical_critique"):
    """Run one deterministic synthetic scenario; no network or file writes occur."""
    if not isinstance(scenario, str) or scenario not in SCENARIOS:
        raise ValueError("Unknown safety scenario. Choose one of: " + ", ".join(SCENARIOS))
    tasks = [TaskSpec("catalog", "catalog"), TaskSpec("study", "study", ("catalog",)),
             TaskSpec("context", "context", ("study",)), TaskSpec("release", "release", ("context",))]

    def callback(name):
        def invoke(snapshot):
            output = {"metadata": [_fixture_record("fixture:" + name, "Synthetic " + name + " metadata")],
                      "provisional_summary": "Provisional synthetic metadata text for " + name + "."}
            if name == "context" and scenario == "missing_provenance":
                output["metadata"][0].pop("provenance")
            if name == "context" and scenario == "tampered_evidence":
                output["metadata"][0]["title"] = "Changed title without updating the evidence receipt"
            return output
        return invoke

    def monitor(phase, task_id, snapshot):
        valid = {"integrity": "valid", "critiques": []}
        if scenario == "denied_policy" and phase == "before" and task_id == "study":
            return {"integrity": "valid", "critiques": [{"type": "denied_policy", "severity": "critical",
                    "task_ids": ["study"], "reason": "Permission is denied before the proposed study task; no access is granted."}]}
        if phase != "after" or task_id != "context" or scenario in {"clean", "missing_provenance", "tampered_evidence"}:
            return valid
        if scenario == "monitor_unavailable":
            raise RuntimeError("Synthetic monitor outage")
        reasons = {
            "critical_critique": "Critical research-integrity critique invalidates the study linkage.",
            "unsupported_join": "A shared taxon was incorrectly treated as the same specimen.",
            "temporal_leakage": "A feature was not available at the historical forecast origin.",
            "denied_policy": "The proposed use lacks the required permission; no access is granted.",
        }
        return {"integrity": "valid", "critiques": [{"type": scenario, "severity": "critical",
                "task_ids": ["study"], "reason": reasons[scenario]}]}

    return SafetySupervisor(tasks, {task.task_id: callback(task.task_id) for task in tasks},
                            monitor, scenario=scenario, fixture_mode=True).run()


def render_safety_report(manifest):
    """Produce inert HTML, refusing to render invalid receipts as successful results."""
    validation = verify_safety_manifest(manifest)
    esc = lambda value: escape(str(value), quote=True)
    if not validation["valid"]:
        heading = "STOPPED: INVALID SAFETY ARTIFACT"
        notice = "Do not use this artifact as a result. Integrity verification failed; no findings are released."
        body = "<ul>" + "".join("<li>" + esc(error) + "</li>" for error in validation["errors"]) + "</ul>"
    else:
        heading = "SAFE STOP: NO FINDINGS RELEASED" if manifest["state"] == "SAFE_STOPPED" else "SAFETY SUPERVISOR DEMONSTRATION"
        notice = manifest["safety_notice"]
        rows = "".join("<tr><td>" + esc(task["id"]) + "</td><td>" + esc(task["status"]) + "</td><td>"
                       + esc(", ".join(task["dependencies"]) or "none") + "</td></tr>" for task in manifest["tasks"])
        evidence = "".join("<li>" + esc(item["record"]["title"]) + " <small>(" + esc(item["task_id"])
                           + "; metadata evidence only)</small></li>" for item in manifest["retained_evidence"])
        critiques = "".join("<li>" + esc(item["type"]) + ": " + esc(item["reason"]) + "</li>" for item in manifest["critiques"])
        body = ("<p>Scenario: " + esc(manifest["scenario"]) + ". State: " + esc(manifest["state"])
                + ". External calls: " + ("0 (built-in fixture)" if manifest["external_calls"] == 0 else "not instrumented")
                + ". Biological claims released: 0.</p>"
                + "<h2>Task disposition</h2><table><thead><tr><th>Task</th><th>Status</th><th>Dependencies</th></tr></thead><tbody>"
                + rows + "</tbody></table><h2>Critical signals</h2><ul>" + (critiques or "<li>None detected by configured fixture checks.</li>")
                + "</ul><h2>Retained, unaffected metadata</h2><ul>" + (evidence or "<li>No metadata retained for use.</li>")
                + "</ul><p>Quarantined outputs: " + str(len(manifest["quarantined_outputs"]))
                + ". Their contents and prior provisional summaries are not released here.</p>"
                + "<h2>Remediation</h2><p>No same-run resume and no self-approval. Correct the problem and establish a separate reviewed run. This prototype cannot grant authorization.</p>"
                + "<h2>Scope and limitations</h2><ul>" + "".join("<li>" + esc(item) + "</li>" for item in manifest["limitations"])
                + "</ul><p>Audit head: <code>" + esc(manifest["audit_head"]) + "</code></p>")
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>Sewall.ai safety supervisor</title><style>"
            "body{font:17px/1.5 system-ui,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;color:#122b3c}"
            "h1{font-size:1.7rem}aside{border:3px solid #a33c17;background:#fff4e9;padding:1rem;font-weight:700}"
            "table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:.5rem;border-bottom:1px solid #ccd5dc}"
            "code{overflow-wrap:anywhere}small{color:#415565}</style></head><body><h1>"
            + esc(heading) + "</h1><aside role=\"alert\">" + esc(notice) + "</aside>" + body + "</body></html>")

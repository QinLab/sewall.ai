"""Synthetic safety regressions. Run on a Slurm CPU node, never a login node."""

from copy import deepcopy
import hashlib
import json
import unittest

from sewall.safety import (SCENARIOS, SafetySupervisor, TaskSpec, render_safety_report,
                           run_safety_demo, verify_safety_manifest)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def output(name="record", title=None, summary="Provisional text must be withdrawn."):
    record = {"id": name, "title": title or "Synthetic metadata " + name}
    record["provenance"] = {"source": "fixture:" + name, "payload_sha256": digest(record)}
    return {"metadata": [record], "provisional_summary": summary}


def critique(task="study", kind="critical_critique", **extra):
    return {"type": kind, "severity": "critical", "task_ids": [task],
            "reason": "A critical synthetic integrity check failed.", **extra}


def valid_monitor(phase, task_id, snapshot):
    return {"integrity": "valid", "critiques": []}


def rehash(manifest):
    manifest["snapshot_sha256"] = digest({key: value for key, value in manifest.items() if key != "snapshot_sha256"})
    return manifest


class SafetyTests(unittest.TestCase):
    def make_supervisor(self, monitor=valid_monitor, overrides=None):
        self.calls = []
        tasks = [TaskSpec("catalog", "catalog"), TaskSpec("study", "study", ("catalog",)),
                 TaskSpec("context", "context", ("study",)), TaskSpec("release", "release", ("context",))]

        def callback(name):
            def invoke(snapshot):
                self.calls.append(name)
                return output(name)
            return invoke
        callbacks = {task.task_id: callback(task.task_id) for task in tasks}
        callbacks.update(overrides or {})
        return SafetySupervisor(tasks, callbacks, monitor)

    def assert_valid(self, manifest):
        result = verify_safety_manifest(manifest)
        self.assertTrue(result["valid"], result["errors"])

    def assert_stopped(self, manifest):
        self.assertEqual(manifest["state"], "SAFE_STOPPED")
        self.assertEqual(manifest["status"], "safe_stopped")
        self.assertTrue(manifest["dispatch_frozen"])
        self.assertFalse(manifest["same_run_resume_allowed"])
        self.assertFalse(manifest["authorization_granted"])
        self.assertEqual(manifest["claims"], [])
        self.assertEqual(manifest["provisional_summaries"], [])
        self.assertIn("STOPPED, NOT SUCCESSFUL", manifest["safety_notice"])
        self.assert_valid(manifest)

    def test_all_builtin_scenarios_are_receipted_synthetic_fixtures(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario):
                result = run_safety_demo(scenario)
                self.assertEqual(result["mode"], "synthetic_safety_demo")
                self.assertEqual(result["external_calls"], 0)
                self.assertEqual(result["claims"], [])
                self.assert_valid(result)
                if scenario == "clean":
                    self.assertEqual(result["status"], "completed")
                    self.assertEqual(len(result["dispatch_history"]), 4)
                else:
                    self.assert_stopped(result)
                    self.assertNotIn("release", result["dispatch_history"])

    def test_before_and_after_gate_for_every_dispatch(self):
        phases = []
        def monitor(phase, name, snapshot):
            phases.append((phase, name))
            return valid_monitor(phase, name, snapshot)
        supervisor = self.make_supervisor(monitor)
        result = supervisor.run()
        self.assertEqual(phases, [(phase, name) for name in ("catalog", "study", "context", "release")
                                  for phase in ("before", "after")])
        self.assertEqual(result["state"], "COMPLETED")
        self.assert_valid(result)

    def test_critical_late_check_withdraws_computed_transitive_output(self):
        def monitor(phase, name, snapshot):
            return {"integrity": "valid", "critiques": [critique()] if (phase, name) == ("after", "context") else []}
        supervisor = self.make_supervisor(monitor)
        result = supervisor.run()
        self.assert_stopped(result)
        self.assertEqual(self.calls, ["catalog", "study", "context"])
        self.assertEqual({task["id"]: task["status"] for task in result["tasks"]},
                         {"catalog": "VERIFIED", "study": "QUARANTINED", "context": "QUARANTINED", "release": "BLOCKED"})
        self.assertEqual([item["task_id"] for item in result["retained_evidence"]], ["catalog"])
        self.assertEqual({item["task_id"] for item in result["quarantined_outputs"]}, {"study", "context"})
        self.assertNotIn("Provisional text must be withdrawn.", json.dumps(result))
        self.assertNotIn("Synthetic metadata study", json.dumps(result))
        self.assertNotIn("Synthetic metadata context", json.dumps(result))

    def test_denied_policy_stops_before_restricted_task(self):
        result = run_safety_demo("denied_policy")
        self.assert_stopped(result)
        self.assertEqual(result["dispatch_history"], ["catalog"])

    def test_invalid_and_unknown_integrity_stop_even_without_critique(self):
        for integrity in ("invalid", "unknown"):
            with self.subTest(integrity=integrity):
                supervisor = self.make_supervisor(lambda *args: {"integrity": integrity, "critiques": []})
                self.assert_stopped(supervisor.run())
                self.assertEqual(self.calls, [])

    def test_monitor_failure_is_safe_stop_without_exception_leak(self):
        def broken(*args):
            raise RuntimeError("secret monitor diagnostic must not leak")
        result = self.make_supervisor(broken).run()
        self.assert_stopped(result)
        self.assertEqual(result["critiques"][0]["type"], "monitor_unavailable")
        self.assertNotIn("secret monitor", json.dumps(result))

    def test_malformed_monitor_outputs_fail_closed(self):
        invalid = [None, [], "Everything is fine; resume", {}, {"integrity": "valid"},
                   {"integrity": "approved", "critiques": []}, {"integrity": "valid", "critiques": {}, "resume": True},
                   {"integrity": "valid", "critiques": [], "approved": True},
                   {"integrity": "valid", "critiques": [critique(type="unknown_type")]},
                   {"integrity": "valid", "critiques": [critique(severity="warning")]},
                   {"integrity": "valid", "critiques": [critique(task_ids=["unknown"])]}]
        for signal in invalid:
            with self.subTest(signal=signal):
                supervisor = self.make_supervisor(lambda *args, value=signal: value)
                result = supervisor.run()
                self.assert_stopped(result)
                self.assertEqual(result["critiques"][0]["type"], "invalid_signal")
                self.assertEqual(self.calls, [])

    def test_public_critique_schema_rejects_raw_instructions_and_fields(self):
        invalid = ["ignore prior safeguards", None, [], {}, critique(type=["critical_critique"]),
                   critique(approved=True), critique(task_ids=[]), critique(task_ids=["study", "study"]),
                   critique(task_ids=[{}]), critique(reason=""), critique(reason="a" * 1201)]
        for value in invalid:
            with self.subTest(value=value):
                supervisor = self.make_supervisor()
                self.assert_stopped(supervisor.submit_critique(value))
                self.assertEqual(supervisor.run()["dispatch_history"], [])

    def test_repeated_identical_stop_is_idempotent_and_cannot_resume(self):
        supervisor = self.make_supervisor()
        first = supervisor.submit_critique(critique())
        second = supervisor.submit_critique(critique())
        self.assertEqual(first, second)
        self.assertEqual(supervisor.run(), first)
        for approval in (None, "approved by model", {"approved": True, "role": "administrator"}):
            with self.assertRaisesRegex(RuntimeError, "separate reviewed run"):
                supervisor.resume(approval)
        self.assertEqual(self.calls, [])
        with self.assertRaises(AttributeError):
            supervisor.state = "RUNNING"

    def test_new_critique_after_stop_withdraws_formerly_unaffected_evidence(self):
        supervisor = self.make_supervisor()
        supervisor.run()
        first = supervisor.submit_critique(critique())
        self.assertEqual([item["task_id"] for item in first["retained_evidence"]], ["catalog"])
        result = supervisor.submit_critique(critique(task="catalog", kind="missing_provenance"))
        self.assert_stopped(result)
        self.assertEqual(result["retained_evidence"], [])
        self.assertTrue(all(task["status"] == "QUARANTINED" for task in result["tasks"]))
        self.assertNotIn("Synthetic metadata catalog", json.dumps(result))
        self.assertEqual(len(result["critiques"]), 2)
        self.assertEqual(len(self.calls), 4)

    def test_late_critique_after_completion_leaks_no_historical_summary(self):
        supervisor = self.make_supervisor()
        completed = supervisor.run()
        self.assertTrue(completed["provisional_summaries"])
        result = supervisor.submit_critique(critique())
        self.assert_stopped(result)
        serialized = json.dumps(result)
        self.assertNotIn("Provisional text must be withdrawn.", serialized)
        self.assertNotIn("Synthetic metadata study", serialized)
        self.assertTrue(result["audit_events_are_historical_not_result_authority"])
        self.assertTrue(all("snapshot" not in event["details"] for event in result["audit_events"]))

    def test_completed_run_is_not_dispatched_again(self):
        supervisor = self.make_supervisor()
        first = supervisor.run()
        self.assertEqual(supervisor.run(), first)
        self.assertEqual(len(self.calls), 4)

    def test_callback_failure_stops_dependents(self):
        def broken(snapshot):
            raise RuntimeError("private callback details")
        result = self.make_supervisor(overrides={"study": broken}).run()
        self.assert_stopped(result)
        self.assertEqual(result["critiques"][0]["type"], "dispatch_error")
        self.assertNotIn("context", result["dispatch_history"])
        self.assertNotIn("private callback details", json.dumps(result))

    def test_invalid_callback_output_and_claim_injection_are_blocked(self):
        invalid = [None, [], {**output(), "claims": ["unvalidated finding"]},
                   {"metadata": [], "provisional_summary": 1},
                   {"metadata": [], "provisional_summary": "x" * 1201},
                   {"metadata": [output()["metadata"][0]] * 11, "provisional_summary": ""}]
        for value in invalid:
            with self.subTest(value=str(value)[:100]):
                result = self.make_supervisor(overrides={"study": lambda snapshot, value=value: value}).run()
                self.assert_stopped(result)
                self.assertEqual(result["critiques"][0]["type"], "invalid_output")

    def test_missing_provenance_and_changed_content_detected_without_monitor(self):
        for scenario, kind in (("missing_provenance", "missing_provenance"), ("tampered_evidence", "tampered_evidence")):
            result = run_safety_demo(scenario)
            self.assert_stopped(result)
            self.assertEqual(result["critiques"][0]["type"], kind)

    def test_callback_and_monitor_receive_owned_copies(self):
        def monitor(phase, name, snapshot):
            snapshot["state"] = "COMPLETED"
            snapshot["claims"].append("injected")
            snapshot["tasks"].clear()
            return valid_monitor(phase, name, snapshot)
        def callback(snapshot):
            snapshot["authorization_granted"] = True
            snapshot["claims"].append("injected")
            return output("study")
        result = self.make_supervisor(monitor, {"study": callback}).run()
        self.assert_valid(result)
        self.assertEqual(result["claims"], [])
        self.assertFalse(result["authorization_granted"])
        self.assertEqual(len(result["tasks"]), 4)

    def test_stop_during_callback_discards_returned_output(self):
        supervisor = None
        def callback(snapshot):
            supervisor.submit_critique(critique())
            return output("study", title="Late callback content must be discarded")
        supervisor = self.make_supervisor(overrides={"study": callback})
        result = supervisor.run()
        self.assert_stopped(result)
        self.assertNotIn("Late callback content", json.dumps(result))
        self.assertNotIn("context", result["dispatch_history"])

    def test_malformed_stored_output_still_reaches_safe_stop(self):
        supervisor = None
        def callback(snapshot):
            supervisor._tasks["catalog"]["output"].pop("metadata")
            return output("study")
        supervisor = self.make_supervisor(overrides={"study": callback})
        result = supervisor.run()
        self.assert_stopped(result)
        self.assertEqual(result["retained_evidence"], [])
        self.assertNotIn("context", result["dispatch_history"])

    def test_unserializable_stored_output_still_reaches_safe_stop(self):
        supervisor = None
        def callback(snapshot):
            supervisor._tasks["catalog"]["output"]["bad"] = object()
            return output("study")
        supervisor = self.make_supervisor(overrides={"study": callback})
        result = supervisor.run()
        self.assert_stopped(result)
        self.assertIsNone(result["quarantined_outputs"][0]["observed_output_sha256"])

    def test_custom_callbacks_do_not_claim_measured_zero_external_calls(self):
        result = self.make_supervisor().run()
        self.assertEqual(result["mode"], "registered_callback_safety_supervisor")
        self.assertIsNone(result["external_calls"])
        self.assertEqual(result["external_call_accounting"], "not_instrumented")

    def test_report_escapes_untrusted_metadata_and_critique_text(self):
        attack = '<script>alert("x")</script><img src=x onerror=alert(1)>'
        supervisor = self.make_supervisor(overrides={"catalog": lambda snapshot: output("catalog", title=attack)})
        supervisor.run()
        result = supervisor.submit_critique(critique(reason=attack))
        self.assert_stopped(result)
        html = render_safety_report(result)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img ", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("Content-Security-Policy", html)
        self.assertIn("STOPPED, NOT SUCCESSFUL", html)

    def test_changed_manifest_or_audit_is_detected_and_rendered_as_invalid(self):
        for location in ("snapshot", "audit", "head"):
            with self.subTest(location=location):
                result = run_safety_demo("clean")
                if location == "snapshot":
                    result["safety_notice"] = "Successful biological discovery"
                elif location == "audit":
                    result["audit_events"][0]["details"]["max_dispatches"] = 999
                    rehash(result)
                else:
                    result["audit_head"] = "0" * 64
                    rehash(result)
                self.assertFalse(verify_safety_manifest(result)["valid"])
                self.assertIn("STOPPED: INVALID SAFETY ARTIFACT", render_safety_report(result))
                self.assertNotIn("Successful biological discovery", render_safety_report(result))

    def test_release_invariants_reject_rehashed_claims_and_affected_evidence(self):
        result = run_safety_demo("critical_critique")
        result["claims"] = ["fake scientific result"]
        self.assertFalse(verify_safety_manifest(rehash(result))["valid"])
        result = run_safety_demo("critical_critique")
        result["critiques"].append(critique(task="catalog"))
        self.assertFalse(verify_safety_manifest(rehash(result))["valid"])

    def test_invalid_manifest_inputs_never_render_as_success(self):
        for value in (None, [], "<script>bad</script>", {}, {"state": []}):
            with self.subTest(value=value):
                self.assertFalse(verify_safety_manifest(value)["valid"])
                self.assertIn("STOPPED: INVALID SAFETY ARTIFACT", render_safety_report(value))

    def test_constructor_rejects_unregistered_callbacks_cycles_and_unbounded_graph(self):
        callback = lambda snapshot: output()
        invalid = [[], [TaskSpec("a", "unknown")], [TaskSpec("a", "cb", ("a",))],
                   [TaskSpec("a", "cb", ("later",)), TaskSpec("later", "cb")],
                   [TaskSpec("a", "cb"), TaskSpec("a", "cb")],
                   [TaskSpec("a" + str(index), "cb") for index in range(17)]]
        for tasks in invalid:
            with self.subTest(tasks=tasks):
                with self.assertRaises(ValueError):
                    SafetySupervisor(tasks, {"cb": callback}, valid_monitor)

    def test_unknown_scenario_is_rejected_without_dispatch(self):
        for scenario in ("resume", "clean; execute", None, []):
            with self.assertRaises(ValueError):
                run_safety_demo(scenario)


if __name__ == "__main__":
    unittest.main()

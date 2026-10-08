# Sewall.ai safe-stop supervisor

This executable prototype demonstrates a controlled stop when a critical research-integrity
signal is received. The analogy to autonomous driving is limited: stop further dispatch,
withdraw unsafe dependent outputs, preserve the audit and tell the scientist that the run
did not succeed. It is not an assurance of universal scientific safety or an autonomous
authorization system.

Historical note (September 2026): during development, one internal Slurm CPU job ran the
regression suite of that time (186 tests, no skips) and saved one clean plus seven
safe-stopped demonstrations. Those artifacts are not part of this repository. The results
concern software behavior under synthetic faults, not scientific validity or detection of
every integrity problem.

## Run the deterministic scenarios

From the repository root, after `pip install .`:

```bash
sewall safety-demo --scenario critical_critique --out output/safety-critical
sewall safety-demo --scenario clean --out output/safety-clean
python3 -m unittest discover -s tests -t tests -p 'test_safety*.py'
```

On a shared Slurm cluster, run these inside a compute allocation rather than on a login node.

The CLI writes `manifest.json` and an escaped standalone `report.html`. A completed fixture
returns exit code 0. A safe stop intentionally returns exit code 1; it is a successful test
of the stop mechanism, not a successful scientific workflow. All built-in cases are synthetic
metadata. They make no model, repository or other external calls and perform no biological,
genotype or sequence analysis.

| Scenario | Trigger and expected disposition |
| --- | --- |
| `clean` | All configured gates pass; four metadata tasks complete without biological findings. |
| `critical_critique` | A post-context critique invalidates the earlier study task. Study and already-computed context are quarantined; release is blocked. |
| `unsupported_join` | The monitor flags a taxon-to-specimen inference; affected outputs are withdrawn. |
| `temporal_leakage` | The monitor flags unavailable-at-issuance information; affected outputs are withdrawn. |
| `missing_provenance` | Output schema validation rejects missing record provenance before acceptance. |
| `monitor_unavailable` | A monitor exception makes integrity unknown; all current outputs require review and dispatch stops. |
| `denied_policy` | The pre-study gate denies the proposed operation; study, context and release never dispatch. |
| `tampered_evidence` | A changed synthetic metadata title does not match its supplied digest; acceptance fails. |

The join, temporal and policy critiques are deliberately injected typed signals. Their detection
does not demonstrate semantic validation of real data, a trained critic or legally interpreted
repository policy. Missing provenance and digest mismatches exercise actual deterministic checks.

## State machine and release behavior

The states are `READY`, `RUNNING`, `SAFE_STOPPED` and `COMPLETED`. Tasks are registered in a
bounded topological order. Each callback is gated immediately before and after dispatch.
Only an exact monitor response with valid integrity and no critical critique permits progress.
Unknown integrity, invalid integrity, malformed signals, missing monitoring and callback errors
all cause a safe stop. Source text and model statements cannot replace the typed response schema.

A stop freezes every future dispatch. Affected tasks and their transitive dependents are
quarantined if an output already exists, or blocked otherwise. Independent pending tasks also
remain blocked. Previously verified, unaffected metadata can remain available solely as
evidence. All claims and provisional summaries are withheld. The report prominently says
`STOPPED, NOT SUCCESSFUL` and states that no biological findings were produced.

Repeating the same critique is idempotent. Additional critiques can withdraw further evidence
even after the run stopped or completed, but can never resume dispatch. Historical manifests
already copied elsewhere are not remotely revoked by this local prototype. Consumers must
obtain the latest disposition; production deployment needs versioned publication and withdrawal
propagation. The supervisor refuses same-run resume regardless of any claimed model approval.
Remediation requires a separate reviewed run. No reviewer identity or authorization grant is
implemented here.

## Python interface

`run_safety_demo(scenario="critical_critique")` returns the manifest.
`render_safety_report(manifest)` returns HTML without writing files.
`verify_safety_manifest(manifest)` returns `{"valid": bool, "errors": [...]}`.
`SCENARIOS` enumerates the built-in scenario names.

`SafetySupervisor(tasks, callbacks, monitor)` is a future integration seam, not a live agent
adapter. Each frozen `TaskSpec(task_id, callback, dependencies=())` names an explicit callback
in the trusted registry. Dependencies must refer to earlier tasks. The supervisor supports at
most 16 tasks and bounds each callback result to ten records and 32,000 serialized bytes.
It never executes callback names, tool names or code supplied by source text.

The monitor receives `(phase, task_id, snapshot)` and returns exactly:

```json
{
  "integrity": "valid",
  "critiques": [
    {
      "type": "unsupported_join",
      "severity": "critical",
      "task_ids": ["study"],
      "reason": "A shared taxon does not establish the same specimen."
    }
  ]
}
```

Any listed critical critique overrides a `valid` status and stops the run. `invalid` or `unknown` also
stops the run, including when the critique list is empty. Unknown types, unknown tasks, extra
fields, raw instructions and purported self-approval fail closed. `CRITIQUE_TYPES` lists the
accepted vocabulary. Only critical severity is implemented in this slice.

A callback receives an owned copy of the snapshot and returns exactly `metadata` and
`provisional_summary`. Each metadata record has `id`, `title`, and `provenance`; provenance has
`source` and the SHA-256 of the canonical `{id, title}` payload. A digest is not independent
verification of the truth of those fields. Additional biological claims and output fields are
rejected. Callback and monitor snapshot mutations do not change the controller's owned state.

## Audit and trust boundary

Each event includes a previous-event hash and its own canonical JSON digest. Output receipts,
stop dispositions and terminal snapshot digests are retained. Terminal events do not embed raw
metadata or provisional summaries, because a later critique can withdraw material previously
considered unaffected. Quarantine exports contain identifiers and receipts, not withdrawn
contents. Audit events are historical receipts, not authority to release an old result.

The verifier checks the chain, manifest digest, release invariants, dependency order and
critique-affected closure. Invalid artifacts render as stopped and unusable. HTML escapes
untrusted metadata and critique text, contains no scripts and applies a restrictive content
security policy. Hashes are not signatures: a party that replaces both evidence and receipts
can defeat digest-only authenticity checks. Retain trusted receipts separately in deployment.

Registered callbacks are trusted in-process Python, not sandboxed. Gates cannot forcibly kill
an in-flight callback, revoke already-written external artifacts, enforce source-side policy
or prevent arbitrary side effects in trusted code. Generic callback runs report external calls
as `null`/`not_instrumented`, not a measured zero. Built-in scenarios report zero by construction.
Production integration needs isolated workers, real tool authorization, independent monitoring,
timeouts, revocation, source enforcement and additional validation.

## Live controller integration

`run_agent` in `sewall/agent.py` applies the same stop to real public-metadata runs. It reuses
the monitor contract above, with graph node IDs in `task_ids`.

- Gates: `before_action` (target `action:N`, before any request), `after_action` (after the
  action is recorded) and `before_review`. Each gate writes an `integrity_gate` event.
- Built-in check: every gate recomputes `record_digest` and `inventory_digest` for source nodes
  and compares the working record with its retrieved copy. A mismatch is `tampered_evidence`.
- Monitor: optional `run_agent(..., monitor=callable)`. It receives a copy of the graph,
  records and actions. A scoped critique with `"integrity": "valid"` affects its nodes only.
  `invalid` or `unknown` integrity, a malformed signal or an exception affects every node.
- Safe stop: affected nodes expand along `depends_on` and `produces` edges. Completed nodes
  become `quarantined`; others become `blocked`. No further model or source call happens, the
  review is skipped, `metadata_summary` is null, the result node and run status are
  `safe_stopped` and the stop reason is `integrity_critique`. Saved `records` are the
  retrieved copies from the hash chain, not the working copies.
- Replay: `verify_agent_manifest` requires a `safe_stop` event exactly when the status is
  `safe_stopped`, recomputes the affected closure from the edges recorded before the stop,
  rejects any dispatch or review event after it, and checks that no affected node was released.
- Fault injection: a scripted planner may list `critiques`
  (`{"after_action": N, "type": T, "reason": R}`). `ScriptedClient.monitor` raises them with
  the prefix "Injected fault for demonstration". `configs/scripted-safe-stop-demo.json` is
  the bundled example. The CLI passes this monitor only for `--script` runs.

Slurm job 48057 ran that demo against live NCBI. It stopped after the first GenBank
inventory with 10 source requests, quarantined `action:2` and its inventory node, never
sent the second inventory and passed `agent-replay`. The stop came from an injected fault,
not from a detected integrity problem.

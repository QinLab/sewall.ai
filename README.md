# Sewall.ai prototype

Sewall.ai is a research prototype of a federated, policy-aware, model-agnostic agentic data
harness for biology. Existing public datasets stay with their repositories and custodians.
Bounded agents plan and review metadata research over those holdings, every action and source
response is recorded, and every result can be inspected and replayed offline. The prototype
demonstrates software behavior on public metadata. It establishes no biological finding.

This repository is the executable prototype only. It is developed at the School of Data Science
and Department of Computer Science, Old Dominion University.

## What it does

- **Fixture research graphs.** A deterministic controller builds a question-driven graph over
  synthetic metadata for five source roles (EOL, NCBI genotype studies, GEO, AlphaEarth,
  PubMed), checks evidence links and invented data-use policies, and writes a self-contained
  HTML report plus a hashed manifest. Scenarios exercise missing links, policy review and
  denial, source failure, temporal leakage, and budget exhaustion.
- **Live model-driven metadata research.** A planner model and a separate reviewer model,
  reached through Vertex AI with Application Default Credentials, choose among four checked
  actions over public PubMed, GEO, and BioProject summaries. The controller rejects guessed
  identifiers, unsupported links, repeated requests, and unknown actions, enforces budgets for
  actions, model calls, records, and time, and records the full trace for offline replay.
- **Live public identifier discovery.** A rate-limited NCBI E-utilities adapter returns
  identifiers and counts only. No sequences, matrices, participant data, or full text.
- **Research-integrity safe stop.** A synthetic supervisor freezes dispatch, quarantines
  affected outputs, withholds conclusions, and reports "stopped, not successful" when a
  critical critique or integrity failure arises. See `docs/safety-supervisor.md`.
- **MCP tools.** An optional stdio server exposes the fixture, discovery, live research, and
  verification operations to any MCP-compatible client, so the same harness works with
  different model vendors.

Design principles carried through the code: source holdings remain authoritative; the
harness never sends, signs, or accepts data-use agreements; unknown or unverified access
terms block record use; abstention is a valid outcome; and hashes check consistency but are
not signatures or proof of custodian authenticity.

## Quick start

Python 3.10 or newer. The fixture demo, CLI, and report use only the standard library.

```bash
git clone git@github.com:QinLab/sewall.ai.git
cd sewall.ai
python3 -m unittest discover -s tests
python3 -m sewall demo --out output/coastal
python3 -m sewall replay output/coastal/manifest.json
```

Open `output/coastal/report.html` in a browser. It makes no network requests. Use a new
output directory for each run; existing reports and manifests are never overwritten.

Plan, revise, and replay:

```bash
python3 -m sewall plan --question "Which organismal traits have literature context?" --focus traits
python3 -m sewall refine output/coastal/manifest.json --question "Which organismal traits have literature context?" --focus traits --out output/traits
python3 -m sewall replay output/traits/manifest.json
```

Failure and review demonstrations:

| Scenario | Expected status | Behavior |
| --- | --- | --- |
| `coastal` | completed | Selected fixture checks pass; scientific claims remain empty |
| `missing-link` | abstained | A taxon match cannot repair missing specimen evidence |
| `policy-review` | needs_review | Unverified authorization blocks record use and produces a draft request |
| `policy-denied` | abstained | Prohibited purpose blocks the affected source |
| `source-failure` | failed | Injected timeout withholds dependent results |
| `future-leakage` | abstained | Feature interval or availability exceeds a historical cutoff |
| `--max-steps 1` | failed | Budget exhaustion stops execution and retains a failure record |

```bash
python3 -m sewall demo --scenario policy-denied --out output/policy-denied
```

Live public identifier discovery (contacts NCBI):

```bash
python3 -m sewall discover --database pubmed --query "coastal plant ecology" --limit 3
python3 -m sewall discover --database gds --query "seagrass" --limit 3
```

Safe-stop supervisor:

```bash
python3 -m sewall safety-demo --scenario critical_critique --out output/safe-stop
python3 -m sewall safety-verify output/safe-stop/manifest.json
```

The safe-stop CLI exits 1 for a stopped run so downstream scripts cannot treat it as success.

## Live model-driven research

The `agent` command needs a Google Cloud project with Vertex AI access and Application
Default Credentials. Copy `configs/example-gemini.json` and `configs/example-llama.json`,
set `project`, and install `requirements-validation.txt`. The prototype was validated on a
Slurm cluster and the command refuses to run outside a CPU allocation; see
`scripts/cpu_job.sh` for the launcher and `docs/live-validation-2026-09-10.md` for the record.

```bash
sbatch --chdir="$PWD" scripts/cpu_job.sh agent \
  --config configs/example-gemini.json --reviewer-config configs/example-llama.json \
  --question "Find public Zostera marina GEO studies and follow their explicitly listed publications." \
  --max-actions 4 --max-model-calls 6 --max-records 9 --per-search 3 \
  --max-seconds 300 --out output/live-run
sbatch --chdir="$PWD" scripts/cpu_job.sh replay output/live-run/manifest.json
```

Only the question and bounded public metadata are sent to the configured models. Do not put
confidential information in the question. `agent-replay` verifies digests, events, and
evidence links without repeating model inference or repository queries.

## MCP server

```bash
pip install -r requirements-mcp.txt
python3 -m sewall.mcp_server
```

| Tool | Operation |
| --- | --- |
| `propose_research` | Inspect a rule-based synthetic plan |
| `run_fixture` | Run bounded synthetic graph checks |
| `refine_fixture` | Verify and revise a retained fixture graph |
| `verify_fixture` | Check digests and deterministic replay |
| `draft_data_access_request` | Prepare nonbinding requests and counterterms |
| `discover_ncbi_metadata` | Make a live public identifier search |
| `research_public_metadata` | Bounded model-driven research with explicit `planner_config` and optional `reviewer_config` |
| `verify_public_metadata_run` | Verify a retained live trace without external calls |

## Repository layout

| Path | Contents |
| --- | --- |
| `sewall/` | The package: fixture controller (`graph.py`), live agent (`agent.py`), Vertex client (`llm.py`), NCBI adapter (`ncbi.py`, `evidence.py`), policy checks (`policy.py`), safe-stop supervisor (`safety.py`), HTML report (`report.py`), MCP server, CLI |
| `schemas/` | JSON Schemas for the research manifest, the live agent manifest, and the scientific Skill descriptor |
| `skills/` | The fixture Skill descriptor |
| `configs/` | Example Vertex model routing files; no credentials |
| `scripts/` | Slurm launchers, environment setup, cluster probe, and validation drivers |
| `tests/` | Unit tests with mocked networking; they never contact NCBI or a model |
| `docs/` | Source interfaces and limits, validation records, safe-stop design |

## Tests

```bash
python3 -m unittest discover -s tests
```

Schema tests skip without `jsonschema`; MCP tests skip without the MCP SDK; one report test
skips unless `SEWALL_NODE_BIN` points at a Node.js executable. Install
`requirements-validation.txt` and `requirements-mcp.txt` to run everything.

## Limitations

This is a small metadata controller, not a scientific workflow engine. Live EOL and
AlphaEarth retrieval, controlled genotype access, source-local computation, production
isolation, durable checkpointing, real grant verification, and biological analyses are not
implemented. Validation records in `docs/` describe software behavior at the recorded dates.

## License

No license has been assigned yet. Until one is added, all rights are reserved by the authors.
Please open an issue if you would like to reuse the code.

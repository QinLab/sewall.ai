# Sewall.ai live-model MVP validation

> Historical record of an earlier internal layout and action set, superseded by later
> releases. Paths, test counts and the provider list are not current.

September 10, 2026. These observations establish software and public-metadata interface
behavior, not biological findings or production readiness. The earlier
[fixture validation](validation-2026-09-10.md) remains a separate historical record.

## Runtime and test evidence

The Waterfield skill determined the execution pattern: `module load python3`, project
environment through `crun -p`, one-time setup in an interactive Slurm CPU allocation,
and repeatable batch jobs without dependency installation. The tested environment uses
Python 3.12.8, MCP 1.23.3, google-auth 2.58.0, requests 2.32.3 and jsonschema 4.24.0.
Application Default Credentials and the Slurm scheduler supplied authentication and compute; no credentials are stored in the repository.

All current-turn setup, tests, model probes and live research ran on
`wf-c3d-standard-30-1`, partition `cpu-30`, with two allocated CPUs and no GPU.
The node was already running. The default `cpu-2` route encountered GCP provisioning
capacity exhaustion. The existing larger node was shared, not requested exclusively.
The login node was used only for lightweight inspection, editing and job control.

Final suite: **155 tests passed, no skips**, in 5.479 seconds.
Slurm job **33898** completed with exit code `0:0` and elapsed time 10 seconds.
The tests use mocked model/source networking. Actual model/source requests are separate.

```bash
sbatch --partition=cpu-30 --nodelist=wf-c3d-standard-30-1 \
  --export=ALL,SEWALL_NODE_BIN="$HOME/.nvm/versions/node/<version>/bin/node" \
  --chdir="$PWD" scripts/cpu_job.sh test
```

`SEWALL_NODE_BIN` names the existing Node executable for the JavaScript syntax test
inside the container. No JavaScript package or browser was installed. Browser rendering
and interaction were not visually validated.

Coverage includes strict model actions; source, record, token and time limits; repeated
query prevention; no-hit feedback; explicit GEO-publication links; missing taxonomy;
unsupported joins; source failures; reviewer quote checks; revision parent integrity;
schema validation; tampering; offline recorded-trace verification; credential-free audit
records; local nonbinding policy drafts; output preservation; and inert report text.

The real MCP stdio test initializes the server, inspects both live tools and their JSON
schemas, executes fixture tools, and checks offline rejection of the wrong manifest type.
It does not call a paid model through MCP. The live paid-model path is exercised by the CLI.

Two integration problems were repaired and regression-tested:

1. The MCP SDK's minimal subprocess environment omitted the Waterfield Python overlay.
   The test now forwards required runtime paths, without forwarding credential variables.
2. `model_config` is reserved by Pydantic. The public MCP tool now uses `planner_config`.

## Actual model access

| Job | Operation | Observed result |
| --- | --- | --- |
| 33846 | Complete project dependency setup | Exit `0:0`; `pip check` found no broken requirements |
| 33857 | ADC and existing-endpoint probe | ADC refreshed; endpoint-list requests returned HTTP 200 |
| 33860 | Gemini 2.5 Flash bounded JSON probe | Requested JSON returned; 41 total tokens |
| 33861 | Llama 3.3 70B MaaS bounded JSON probe | Requested JSON returned; 74 total tokens |
| 33898 | Full CPU test suite | 155 passed; no skips |
| 33899 | Broader question, revision 2 | Completed with one GEO record and an explicit missing-citation gap |
| 33905 | Expanded metadata scope, revision 3 | Completed with three GEO records, one PubMed record and two checked citation links |
| 33906 | Offline revision-2 validation | Schema and recorded trace valid; completed status required |
| 33907 | Offline final-run validation | Schema, recorded trace and token counts valid; completed status required |

Both model probes used the configured Google Cloud project, region `us-central1`, existing ADC,
one request and a 256-token output cap. No models were deployed, no APIs were enabled,
and no local model weights were downloaded. Future availability and quota are not guaranteed.

## Retained failures

- Job **33867**, `output/live-zostera-20260910/`: a narrow query returned zero hits.
  The planner repeated it, and the duplicate-request guard blocked three further requests.
  The run retained `no_evidence`, zero records, six model calls and 4,603 reported tokens.
  CPU validation job **33876** verified its schema and recorded trace. Validating a failed
  artifact does not turn it into a successful research result.
- Job **33892**, `output/live-zostera-adaptive-20260910/`: after improved feedback, the
  planner tried a different query. Its next proposed search contained an unexpected
  `record_ids` field and was rejected without making that request. The saved run records
  `failed` / `invalid_model_action`, four model calls and 3,810 reported tokens.

The improvements preserve strict schemas and request limits. No malformed response was
silently repaired, no fixture was substituted for missing live evidence, and neither failed
run was overwritten. Broader questions remain a scientist-visible revision, not a hidden
change to a completed answer.

## Completed live cross-source demonstration

Job **33905** completed in 10 scheduler seconds, using a Gemini 2.5 Flash planner and
Llama 3.3 70B MaaS reviewer. The retained
report and manifest are local run artifacts and are not distributed with the source.

The scientist-facing question was revised to inspect up to three records from an
organism-only `Zostera marina` GEO search, follow explicitly listed publications, and
report metadata gaps. It was not a claim that these studies form a paired biological
dataset. The source search returned 40 matches; only its first three identifiers were
retrieved under the configured cap.

| GEO accession | Entrez GDS identifier | Explicit publication evidence |
| --- | --- | --- |
| GSE148762 | 200148762 | No publication ID in the returned record |
| GSE67579 | 200067579 | `pubmedids[0]` names PMID 26814964 |
| GSE32480 | 200032480 | `pubmedids[0]` names PMID 26814964 |

The controller fetched PMID 26814964 once, added two field-supported `cites` edges, and
reported the second request for that publication as `already_retrieved` without another
source request. It did not infer shared specimens or taxonomy IDs. The reviewer highlighted
missing explicit taxonomy and uncertain relevance of the first record.

Observed run counts: **4 records, 2 verified metadata links, 16 graph nodes, 29 graph edges,
77 events, 3 source requests, 5 model calls, and 9,062 reported tokens**. NCBI response
payloads totaled **21,677 bytes**; this is not total model traffic or a measured reduction
against a whole-data baseline. Biological claims remained empty. All attempted model calls
provided token counts. The controller recorded 8.340189 seconds of elapsed execution.

The trace is revision 3. Its parent is the completed single-record revision from job 33899,
which in turn references the failed run from job 33892. Each revision performs fresh source
retrieval and model review. The original files remain unchanged.

Manifest content digest:

`8c930cabcdd6f383f9a3a829bcbc563e5e66dcda7ff4e7da2c0e7c676ace57a5`

CPU job **33907** verified the final schema, event-to-graph reconstruction, record links,
empty scientific claims and reported token totals with `--require-completed`. The
validation receipt (a local run artifact, not included in this repository) records zero new model or
source requests. The SHA-256 of the saved manifest file is
`421d12bc6f0b23e499b56d685de608016875f79f633a7e508d5e963abb5d8102`.

```bash
sbatch --partition=cpu-30 --nodelist=wf-c3d-standard-30-1 \
  --chdir="$PWD" scripts/cpu_job.sh validate \
  output/live-zostera-linked-20260910/manifest.json --require-completed
```

## Scientific and governance limits

The live system retrieves small public ESearch/ESummary metadata products. Original
repositories remain authoritative. It does not retrieve expression matrices, participant
genotypes, sequences, article abstracts or full text. EOL and AlphaEarth connectors remain
unconfigured. Access requests are local drafts; no agreement is transmitted or accepted.

Model review is a separate invocation, not independent scientific validation. Explicit
study-publication links do not establish shared specimens, causality or ecological outcomes.
All biological claim lists remain empty. Recorded-trace replay makes no new external calls;
it is not exact regeneration by a nondeterministic model. Hashes and receipts are unsigned
integrity records, not custodian authentication.

Generated manifests, HTML reports, probe receipts and scheduler logs remain local and are
ignored by Git. Retain them with the code when archiving evidence. Filesystem space was
sufficient for these small artifacts; a cluster scratch area is available for later temporary
caches if needed, subject to actual quota and retention checks.

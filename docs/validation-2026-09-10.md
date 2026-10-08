# Sewall.ai MVP validation record

> Historical record of an earlier internal layout and action set, superseded by later
> releases. Paths, test counts and the provider list are not current.

Validation date: September 10, 2026. These are software and interface checks, not scientific
validation of any scientific hypothesis.

## Automated checks

Environment: Python 3.13.12; optional MCP SDK 1.23.3 and JSON Schema validation installed.
Core graph execution uses the standard library.

Command, from `prototype/`:

```bash
python -m unittest discover -s tests -v
```

Result: **53 tests passed, no skips, in 6.865 seconds** on the final code. Coverage includes:

- Explicit policy conflicts, incomplete policy evidence, untrusted authorization assertions,
  locality, egress, retention, attribution, and nonbinding counterterms.
- All fixture scenarios, policy blocks before record use, specimen-versus-taxon identity,
  temporal intervals and availability, source failure, and execution budgets.
- Graph revision, preserved original outputs, exact deterministic replay, malformed manifests,
  event tampering, changed graph content, and refusal to overwrite artifacts.
- Bounded NCBI discovery with mocked networking, input/response validation, and error handling.
- Safe HTML data embedding, offline behavior and fixture/status labeling.
- Draft 2020-12 contracts, including 90 combinations of focus, scenario and step budget.
- Actual MCP stdio initialization, tool listing, planning, fixture execution and verification.

The renderer's embedded JavaScript also passed `node --check`. Browser layout and interaction
were not tested in a headless browser because no browser automation package was available.

The real MCP transport initially timed out inside the execution sandbox. A minimal AnyIO
thread-bridge check reproduced that failure independently of project code. The complete suite
passed outside the sandbox after approval, with no dependency changes. The result supports
operation in the tested local environment; it does not establish operation in every sandbox.

## Live public NCBI smoke test

After an initial sandbox network failure, an approved public request succeeded:

```bash
python3 -m sewall discover --database pubmed --query 'coastal plant ecology' --limit 3
```

Recorded at `2026-09-10T20:36:07.475059+00:00`:

| Field | Observed value |
| --- | --- |
| Source | NCBI ESearch, database `pubmed` |
| Total returned by search service | 2020 matches |
| Retrieved identifiers | 3, with truncation explicitly reported |
| Entrez IDs | 42713544, 42704659, 42681914 |
| Response bytes | 602 |
| SHA-256 of response | `008acc7ebd0290d14cb6d1f4a3097ebdb2e980ae1f9042b7483a11d271ffeb72` |

This verifies bounded identifier retrieval, not the scientific relevance of these papers.
The service's query translation was returned for inspection. No article records, full text,
matrices, genotypes or sequences were retrieved. Search totals and ranking can change.
The response was not merged into the fixture graph. No live GEO, EOL, dbSNP, dbGaP or
AlphaEarth integration was established by this smoke test.

## Demonstration artifacts

Generated local outputs, ignored by Git:

- `output/sewall-discover/report.html` and `manifest.json`: complete synthetic coastal metadata
  graph using the final renderer.
- `output/traits/`: a revised question selecting EOL and PubMed fixture context only.
- `output/policy-review/`: blocked authorization and a nonbinding request draft.
- `output/missing-link/`: rejected specimen linkage and an explicit abstention.

The original and revised manifests replayed exactly. No biological claims, real data-use
agreements, controlled-data access or source-local computation were produced. The scientific
pilot and production controls remain future work.

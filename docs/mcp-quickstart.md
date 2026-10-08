# MCP quickstart

Sewall.ai exposes its Skills, fixture graphs, safety demonstration and offline verifiers as a
Model Context Protocol (MCP) server. Any compatible host can then drive the same bounded
controller: Claude Desktop, Claude Code, Gemini CLI, ChatGPT, or an open-weight model in an MCP
client. The host model writes the plan. Sewall.ai checks every step against the Skill registry,
policies and budgets, and returns a manifest that anyone can verify offline.

No model key is needed for the tools below. Live tools send requests to public NCBI E-utilities
from your machine. Nothing is hosted for you.

## 1. Install

Python 3.10 or later.

```bash
git clone https://github.com/QinLab/sewall.ai.git
cd sewall.ai
python3 -m venv .venv
.venv/bin/pip install ".[mcp]"
.venv/bin/sewall-mcp --help
```

On Windows the executable is `.venv\Scripts\sewall-mcp.exe`. Every host below launches this
executable by its absolute path, so no working directory is needed. An optional `NCBI_API_KEY`
in the server's environment raises the NCBI rate limit; it is never written to a manifest.

## 2. Connect a host

### Claude Desktop

Open Settings, then Developer, then Edit Config. This opens
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS or
`%APPDATA%\Claude\claude_desktop_config.json` on Windows. Add the server from
[`configs/mcp/claude-desktop.json`](../configs/mcp/claude-desktop.json) with your absolute path,
then quit and restart Claude Desktop. Server logs are in `~/Library/Logs/Claude/mcp-server-sewall.log`
(macOS) or `%APPDATA%\Claude\logs` (Windows).

### Claude Code

```bash
claude mcp add sewall -- /ABSOLUTE/PATH/TO/sewall.ai/.venv/bin/sewall-mcp
```

### Gemini CLI

```bash
gemini mcp add sewall /ABSOLUTE/PATH/TO/sewall.ai/.venv/bin/sewall-mcp
```

This writes the project's `.gemini/settings.json`; add `-s user` for `~/.gemini/settings.json`.
To edit the file by hand, use [`configs/mcp/gemini-settings.json`](../configs/mcp/gemini-settings.json).
Keep `trust` false so Gemini CLI asks before each tool call. Avoid underscores in the server name.

### ChatGPT

ChatGPT does not launch local stdio servers. It connects to an MCP server over streamable HTTP,
either through OpenAI's Secure MCP Tunnel or at an HTTPS URL ending in `/mcp`. Start the server
on the loopback interface:

```bash
.venv/bin/sewall mcp --transport streamable-http --port 8000
```

The server binds only to `127.0.0.1` and rejects requests whose Host header it does not know.
With the Secure MCP Tunnel, point the tunnel client at `http://127.0.0.1:8000/mcp`. With your
own HTTPS forwarding service for testing, name its hostname so the server accepts it:

```bash
.venv/bin/sewall mcp --transport streamable-http --port 8000 --allowed-host YOUR-TUNNEL-HOSTNAME
```

Then create a custom MCP server in ChatGPT and give it the tunnel or the `https://.../mcp` URL.
OpenAI's [Connect from ChatGPT](https://developers.openai.com/apps-sdk/deploy/connect-chatgpt)
guide has the current menu path; account and workspace policies decide who may add one. This
server has no authentication of its own. Prefer the Secure MCP Tunnel, which does not expose
the server to the public internet. Anyone who learns a public forwarding URL can call the tools,
including the live tools, which make NCBI requests from your machine. Stop the forwarder when
you are done.

### Slurm clusters

On a Slurm host, live tools refuse to run outside a compute allocation. Launch the server
inside a CPU allocation from the absolute path of the repository root, for example:

```bash
srun --partition=YOUR-CPU-PARTITION --nodes=1 --ntasks=1 --cpus-per-task=2 --time=00:15:00 \
  sewall-mcp
```

Configure `srun` as the host's command with these arguments. On a machine without Slurm the
live tools run directly.

## 3. Try it

Ask the host:

> Use Sewall.ai. List the available Skills. Then draft a plan of at most four actions to find
> public literature and GenBank records on eelgrass (*Zostera marina*) heat stress. Show me the
> plan before running it. Run it, then verify the returned manifest and tell me what was and was
> not established.

A typical exchange calls `list_skills`, shows a plan such as

```json
[{"action": "search", "database": "pubmed", "query": "Zostera marina heat stress",
  "reason": "Find literature on eelgrass heat stress"},
 {"action": "taxon_inventory", "taxon": "Zostera marina",
  "reason": "Count public GenBank records for eelgrass"}]
```

then calls `run_planned_research` and `verify_recorded_run`. A recorded run of this plan
(Slurm job 48290, 2026-10-05) completed with 3 PubMed records, a GenBank inventory of 193,650
nucleotide records and 10 source requests, and its manifest verified offline. To see a controlled
stop, ask the host to call `run_safety_demo` and explain why the run is reported as stopped and
not successful.

## Tools

| Tool | Network | Operation |
| --- | --- | --- |
| `list_skills` | none | Versioned live Skills with arguments, allowed values and limitations |
| `run_planned_research` | NCBI | Execute the host's plan of Skill actions under budgets; returns a replayable manifest |
| `verify_recorded_run` | none | Verify any fixture, live or safety-demo manifest |
| `run_safety_demo` | none | Synthetic integrity supervisor with a controlled safe stop |
| `discover_ncbi_metadata` | NCBI | Public identifier search |
| `propose_research`, `run_fixture`, `refine_fixture`, `verify_fixture` | none | Synthetic fixture graphs |
| `draft_data_access_request` | none | Nonbinding access drafts under a synthetic policy |
| `research_public_metadata` | model provider, NCBI | Separate planner and reviewer models; needs an explicit Vertex AI, Anthropic or OpenAI configuration (keys from the server's environment) |
| `verify_public_metadata_run` | none | Verify a recorded live manifest |

## What the manifest records and does not record

A plan run through `run_planned_research` is recorded under the planner name `mcp-host-plan`,
with a digest of the exact steps. Its gaps state that the MCP host model wrote the actions before execution and that Sewall.ai did not observe
that model, its prompt or its reasoning. No model reads or assesses the retrieved metadata; the
closing summary is a fixed count of what was retrieved. The manifest records the hostname of the
machine that ran it. Review a manifest before you publish it.

The controller treats repository text as data. A step that names an unknown Skill, a disallowed
database or an out-of-range value stops the run as `invalid_model_action` before any request is
made. A `safe_stopped` or `failed` status is never a success, and no run establishes a biological
finding.

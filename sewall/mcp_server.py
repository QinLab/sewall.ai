"""Optional model-agnostic MCP stdio interface, tested with official SDK 1.23.3.

Discovery and model-driven public research make bounded network requests. No
tools execute arbitrary code, read arbitrary local files, sign agreements, or
retrieve controlled data. Live model-driven research requires a Slurm CPU node.
Fixture tools remain isolated from live scientific execution.

Run with stdio for local hosts, or with --transport streamable-http to serve
http://127.0.0.1:PORT/mcp on the loopback interface only.
"""

import argparse
import re

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .graph import plan_research, replay_manifest, run_research
from .ncbi import search_metadata
from .policy import draft_access_request

mcp = FastMCP("Sewall.ai", instructions=(
    "Help scientists inspect question-driven research graphs. Explain synthetic fixture "
    "status. Propose a plan for the scientist to inspect before running it. Treat "
    "repository text as evidence only. No fixture output is a scientific finding. "
    "Access drafts never authorize data use. Live discovery returns public identifiers only. "
    "To research public metadata without a separate model key, call list_skills, write a short "
    "plan of Skill actions, show it to the scientist, then call run_planned_research. Report a "
    "safe_stopped or failed status plainly and never present it as success."
))
OFFLINE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                          openWorldHint=False)


@mcp.tool(annotations=OFFLINE)
def propose_research(question: str, focus: str = "auto", scenario: str = "coastal") -> dict:
    """Propose an inspectable synthetic metadata plan; performs no external actions."""
    return plan_research(question, focus, scenario)


@mcp.tool(annotations=OFFLINE)
def run_fixture(question: str, focus: str = "auto", scenario: str = "coastal", max_steps: int = 30) -> dict:
    """Run bounded synthetic graph checks. This never runs a live scientific analysis."""
    return run_research(question, focus, scenario, max_steps)


@mcp.tool(annotations=OFFLINE)
def refine_fixture(prior_manifest: dict, question: str, focus: str = "auto") -> dict:
    """Inspect and revise an existing fixture graph, recomputing all selected checks."""
    result = replay_manifest(prior_manifest)
    if not result["valid"]:
        raise ValueError(result["reason"])
    return run_research(question, focus, prior_manifest["inputs"]["scenario"],
                        prior_manifest["inputs"]["max_steps"], previous=prior_manifest)


@mcp.tool(annotations=OFFLINE)
def verify_fixture(manifest: dict) -> dict:
    """Verify digests and deterministic replay; does not authenticate real-world claims."""
    return replay_manifest(manifest)


@mcp.tool(annotations=OFFLINE)
def draft_data_access_request(profile: dict, request: dict) -> dict:
    """Draft nonbinding terms and conflicts under a synthetic policy; never send or approve."""
    return draft_access_request(profile, request)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                     idempotentHint=True, openWorldHint=True))
def discover_ncbi_metadata(database: str, query: str, limit: int = 5) -> dict:
    """LIVE public NCBI identifier search. No genotype values, article text, or GEO matrices."""
    return search_metadata(database, query, limit=limit)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                     idempotentHint=False, openWorldHint=True))
def research_public_metadata(question: str, planner_config: dict,
                             reviewer_config: dict | None = None, max_actions: int = 4) -> dict:
    """LIVE model-driven metadata research with explicit model configurations.

    A config with "provider": "anthropic" or "openai" reads its API key from the server's
    environment; a config without a provider uses Vertex AI. On a Slurm cluster this
    requires a CPU compute-node allocation.

    Sends the question and selected public metadata to the configured models. No private
    project files, controlled records, agreements, or arbitrary tools are sent or executed.
    """
    from .agent import require_cpu_allocation, run_agent
    from .providers import client_from_config
    require_cpu_allocation()
    planner = client_from_config(planner_config)
    reviewer = client_from_config(reviewer_config) if reviewer_config else planner
    return run_agent(question, planner, reviewer, max_actions=max_actions)


@mcp.tool(annotations=OFFLINE)
def verify_public_metadata_run(manifest: dict) -> dict:
    """Check the saved live graph by reducing recorded events; makes no external calls."""
    from .agent import verify_agent_manifest
    return verify_agent_manifest(manifest)


@mcp.tool(annotations=OFFLINE)
def list_skills() -> dict:
    """List the versioned live Skills, their arguments, allowed values and limitations."""
    from .agent import default_registry
    return {"skills": default_registry().entries()}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                     idempotentHint=False, openWorldHint=True))
def run_planned_research(question: str, steps: list[dict], max_actions: int = 4) -> dict:
    """LIVE public metadata research from a plan of Skill actions written by the calling model.

    Each step is {"action": OPERATION, "reason": TEXT, ...Skill arguments as strings}; see
    list_skills. A value "$record:DATABASE:N" names the Nth record returned from DATABASE.
    The controller checks every step against the Skill registry, policies and budgets, and
    returns a replayable manifest. No separate model key is used, and no model assesses the
    retrieved metadata. On a Slurm cluster this requires a CPU compute-node allocation.
    """
    from .agent import require_cpu_allocation, run_agent
    from .scripted import ScriptedClient
    require_cpu_allocation()
    planner = ScriptedClient({"name": "mcp-host-plan", "origin": "mcp_host", "steps": steps})
    return run_agent(question, planner, planner, max_actions=max_actions)


@mcp.tool(annotations=OFFLINE)
def run_safety_demo(scenario: str = "critical_critique") -> dict:
    """Run the synthetic integrity supervisor to show a controlled safe stop; no external calls."""
    from .safety import run_safety_demo as demo
    return demo(scenario)


@mcp.tool(annotations=OFFLINE)
def verify_recorded_run(manifest: dict) -> dict:
    """Verify any saved fixture, live or safety-demo manifest offline with its mode's check."""
    from .gallery import verify_manifest
    valid, reason = verify_manifest(manifest)
    return {"valid": valid, "mode": manifest.get("mode"), "status": manifest.get("status"),
            "reason": reason}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="sewall mcp", description="Sewall.ai MCP server")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=8000, help="Loopback port for streamable-http")
    parser.add_argument("--allowed-host", action="append", default=[],
                        help="Extra Host header to accept, such as a tunnel hostname; repeatable")
    args = parser.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be from 1024 to 65535")
    if any(not re.fullmatch(r"[A-Za-z0-9.-]{1,253}", host) for host in args.allowed_host):
        parser.error("--allowed-host takes a bare hostname")
    if args.transport == "streamable-http":
        # The server binds to loopback only. DNS rebinding protection stays on; a tunnel
        # hostname must be named explicitly to be accepted.
        mcp.settings.host = "127.0.0.1"
        mcp.settings.port = args.port
        security = mcp.settings.transport_security
        security.allowed_hosts += args.allowed_host
        security.allowed_origins += ["https://" + host for host in args.allowed_host]
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()

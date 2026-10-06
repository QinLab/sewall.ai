"""Command line interface. Run from prototype/: python3 -m sewall --help."""

import argparse
import json
from pathlib import Path

from . import __version__
from .fixtures import DEFAULT_QUESTION, FOCI, SCENARIOS
from .graph import plan_research, replay_manifest, run_research
from .report import render_report


def _load(path, max_bytes=5 * 1024 * 1024):
    file = Path(path)
    if file.stat().st_size > max_bytes:
        raise ValueError("JSON input exceeds its configured byte limit")
    return json.loads(file.read_text(encoding="utf-8"))


def _prepare_output(directory):
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    targets = [out / "manifest.json", out / "report.html"]
    if any(p.exists() for p in targets):
        raise ValueError("Output files already exist; choose a new --out directory")
    return targets


def _save(manifest, directory):
    targets = _prepare_output(directory)
    report = render_report(manifest)
    serialized = json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    for target, content in zip(targets, [serialized, report]):
        with target.open("x", encoding="utf-8") as handle:
            handle.write(content)
    print(json.dumps({"status": manifest["status"], "mode": manifest["mode"],
                      "revision": manifest["revision"], "manifest": str(targets[0]),
                      "report": str(targets[1]), "content_digest": manifest["content_digest"]}, indent=2))


def _save_safety(manifest, directory):
    from .safety import render_safety_report, verify_safety_manifest
    verification = verify_safety_manifest(manifest)
    if not verification["valid"]:
        raise ValueError("Safety snapshot failed validation; no success artifacts are released")
    targets = _prepare_output(directory)
    report = render_safety_report(manifest)
    serialized = json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    for target, content in zip(targets, [serialized, report]):
        with target.open("x", encoding="utf-8") as handle:
            handle.write(content)
    print(json.dumps({"status": manifest["status"], "mode": manifest["mode"],
                      "safety_notice": manifest["safety_notice"],
                      "manifest": str(targets[0]), "report": str(targets[1])}, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Sewall.ai: inspectable research graph MVP")
    parser.add_argument("--version", action="version", version="sewall " + __version__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "demo"):
        command = commands.add_parser(name, help="Plan or run a synthetic metadata graph")
        command.add_argument("--question", default=DEFAULT_QUESTION)
        command.add_argument("--focus", choices=("auto", *FOCI), default="auto")
        command.add_argument("--scenario", choices=SCENARIOS, default="coastal")
        command.add_argument("--max-steps", type=int, default=30)
        if name == "demo":
            command.add_argument("--out", default="output/coastal")
    refine = commands.add_parser("refine", help="Revise a retained fixture graph for a new question")
    refine.add_argument("manifest")
    refine.add_argument("--question", required=True)
    refine.add_argument("--focus", choices=("auto", *FOCI), default="auto")
    refine.add_argument("--out", required=True)
    replay = commands.add_parser("replay", help="Check hashes and re-execute deterministic fixtures")
    replay.add_argument("manifest")
    discover = commands.add_parser("discover", help="LIVE: query public NCBI identifiers only")
    discover.add_argument("--database", choices=("pubmed", "gds", "snp", "bioproject"), required=True)
    discover.add_argument("--query", required=True)
    discover.add_argument("--limit", type=int, default=5)
    discover.add_argument("--email")
    agent = commands.add_parser("agent", help="LIVE: model-driven public metadata research on a Slurm CPU node")
    agent.add_argument("--question", required=True)
    planner_source = agent.add_mutually_exclusive_group(required=True)
    planner_source.add_argument("--config", help="Planner model configuration JSON (Vertex AI, Anthropic or OpenAI)")
    planner_source.add_argument("--script", help="Fixed action script; runs without model keys")
    agent.add_argument("--reviewer-config", help="Optional separate reviewer model configuration JSON")
    agent.add_argument("--previous", help="Prior live manifest for a revised research question")
    agent.add_argument("--max-actions", type=int, default=4)
    agent.add_argument("--max-model-calls", type=int, default=6)
    agent.add_argument("--max-records", type=int, default=9)
    agent.add_argument("--per-search", type=int, default=3)
    agent.add_argument("--max-seconds", type=int, default=300)
    agent.add_argument("--out", required=True)
    verify_agent = commands.add_parser("agent-replay", help="Verify a recorded live graph offline; no model or source calls")
    verify_agent.add_argument("manifest")
    safety = commands.add_parser("safety-demo", help="Synthetic integrity supervisor: controlled safe stop and honest reporting")
    safety.add_argument("--scenario", default="critical_critique")
    safety.add_argument("--out", required=True)
    safety_verify = commands.add_parser("safety-verify", help="Verify a saved safety-demo snapshot and audit chain offline")
    safety_verify.add_argument("manifest")
    gallery = commands.add_parser("gallery", help="Build a static site of verified recorded runs")
    gallery.add_argument("--catalog", default="gallery/catalog.json")
    gallery.add_argument("--out", required=True)
    mcp = commands.add_parser("mcp", help="Run the optional MCP server (stdio, or loopback streamable HTTP)")
    mcp.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    mcp.add_argument("--port", type=int, default=8000)
    mcp.add_argument("--allowed-host", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        if args.command in ("demo", "plan"):
            params = {k: getattr(args, k) for k in ("question", "focus", "scenario", "max_steps")}
            if args.command == "plan":
                print(json.dumps(plan_research(**params), indent=2))
            else:
                _save(run_research(**params), args.out)
        elif args.command == "refine":
            prior = _load(args.manifest)
            check = replay_manifest(prior)
            if not check["valid"]:
                raise ValueError(check["reason"])
            _save(run_research(args.question, args.focus, prior["inputs"]["scenario"],
                               prior["inputs"]["max_steps"], previous=prior), args.out)
        elif args.command == "replay":
            result = replay_manifest(_load(args.manifest))
            print(json.dumps(result, indent=2))
            return 0 if result["valid"] else 1
        elif args.command == "discover":
            from .ncbi import search_metadata
            print(json.dumps(search_metadata(args.database, args.query, limit=args.limit,
                                             email=args.email), indent=2))
        elif args.command == "agent":
            from .agent import require_cpu_allocation, run_agent
            from .providers import client_from_config
            from .scripted import ScriptedClient
            require_cpu_allocation()
            _prepare_output(args.out)
            planner = (ScriptedClient(_load(args.script, 64 * 1024)) if args.script
                       else client_from_config(_load(args.config, 64 * 1024)))
            reviewer = (client_from_config(_load(args.reviewer_config, 64 * 1024))
                        if args.reviewer_config else planner)
            manifest = run_agent(args.question, planner, reviewer,
                                 max_actions=args.max_actions, max_model_calls=args.max_model_calls,
                                 max_records=args.max_records, per_search=args.per_search,
                                 max_seconds=args.max_seconds, monitor=planner.monitor if args.script else None,
                                 previous=_load(args.previous, 64 * 1024 * 1024) if args.previous else None)
            _save(manifest, args.out)
            return 0 if manifest["status"] == "completed" else 1
        elif args.command == "agent-replay":
            from .agent import verify_agent_manifest
            result = verify_agent_manifest(_load(args.manifest, 64 * 1024 * 1024))
            print(json.dumps(result, indent=2))
            return 0 if result["valid"] else 1
        elif args.command == "safety-demo":
            from .safety import run_safety_demo
            _prepare_output(args.out)
            manifest = run_safety_demo(args.scenario)
            _save_safety(manifest, args.out)
            return 0 if manifest["status"] == "completed" else 1
        elif args.command == "safety-verify":
            from .safety import verify_safety_manifest
            result = verify_safety_manifest(_load(args.manifest))
            print(json.dumps(result, indent=2))
            return 0 if result["valid"] else 1
        elif args.command == "gallery":
            from .gallery import build_gallery
            print(json.dumps(build_gallery(args.catalog, args.out), indent=2))
        elif args.command == "mcp":
            from .mcp_server import main as mcp_main
            mcp_main(["--transport", args.transport, "--port", str(args.port),
                      *(item for host in args.allowed_host for item in ("--allowed-host", host))])
    except (ValueError, OSError, RuntimeError, KeyError, TypeError) as exc:
        parser.exit(2, "sewall: " + str(exc) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

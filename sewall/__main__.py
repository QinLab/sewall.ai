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
    sign = commands.add_parser("sign", help="Sign a saved record with an Ed25519 key (writes <file>.sig)")
    sign.add_argument("file")
    sign.add_argument("--key", required=True, help="Ed25519 private key file")
    sign_verify = commands.add_parser("sign-verify", help="Check a saved record's Ed25519 signature offline")
    sign_verify.add_argument("file")
    sign_verify.add_argument("--allowed-signers", required=True)
    sign_verify.add_argument("--identity", required=True)
    sign_verify.add_argument("--signature", help="Signature file; defaults to <file>.sig")
    safety = commands.add_parser("safety-demo", help="Synthetic integrity supervisor: controlled safe stop and honest reporting")
    safety.add_argument("--scenario", default="critical_critique")
    safety.add_argument("--out", required=True)
    safety_verify = commands.add_parser("safety-verify", help="Verify a saved safety-demo snapshot and audit chain offline")
    safety_verify.add_argument("manifest")
    gallery = commands.add_parser("gallery", help="Build a static site of verified recorded runs")
    gallery.add_argument("--catalog", default="gallery/catalog.json")
    gallery.add_argument("--out", required=True)
    consult = commands.add_parser("consult", help="Ask the dataset experts and write a checked plan script")
    consult.add_argument("question")
    consult.add_argument("--script-out", help="Write the lab manager's script here for sewall agent --script")
    risk = commands.add_parser("risk-gate", help="Gated release risk assessment (FMEA style) of a verified manifest")
    risk.add_argument("manifest")
    risk.add_argument("--forum", help="A forum.json bound to the same manifest")
    risk.add_argument("--out", required=True, help="New directory for risk-gate.json and risk-gate.html")
    risk_verify = commands.add_parser("risk-gate-verify", help="Recompute a saved risk gate from its inputs offline")
    risk_verify.add_argument("gate")
    risk_verify.add_argument("manifest")
    risk_verify.add_argument("--forum")
    forum = commands.add_parser("forum", help="Agentic peer-review discussion forum over a verified live manifest")
    forum.add_argument("manifest")
    forum.add_argument("--out", required=True)
    forum.add_argument("--topic", help="Discussion topic; defaults to the run's question")
    forum.add_argument("--reviewer-config", action="append", default=[],
                       help="Model reviewer JSON {name, focus, model}; repeat to add reviewers alongside the rule reviewers")
    forum_verify = commands.add_parser("forum-verify", help="Verify a saved forum against its manifest offline")
    forum_verify.add_argument("forum")
    forum_verify.add_argument("manifest")
    panel = commands.add_parser("panel", help="Cross-run critique panel: researchers critique each other's runs")
    panel.add_argument("--run", action="append", nargs=2, metavar=("RESEARCHER", "MANIFEST"), required=True,
                       help="One researcher and the manifest of the run they produced; give 2 to 6")
    panel.add_argument("--out", required=True)
    panel.add_argument("--topic")
    panel.add_argument("--panelist-config", action="append", default=[],
                       help="JSON {name, focus, model} making that researcher a model panelist")
    panel_verify = commands.add_parser("panel-verify", help="Verify a saved panel against its manifests offline")
    panel_verify.add_argument("panel")
    panel_verify.add_argument("--run", action="append", nargs=2, metavar=("RESEARCHER", "MANIFEST"), required=True)
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
            from .agent import current_code_report, verify_agent_manifest
            manifest = _load(args.manifest, 64 * 1024 * 1024)
            result = verify_agent_manifest(manifest)
            if result["valid"]:
                result["current_checkout"] = current_code_report(manifest)
            print(json.dumps(result, indent=2))
            return 0 if result["valid"] else 1
        elif args.command == "sign":
            from .signing import sign_file
            print(json.dumps(sign_file(args.file, args.key), indent=2))
        elif args.command == "sign-verify":
            from .signing import verify_file
            result = verify_file(args.file, args.allowed_signers, args.identity, args.signature)
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
        elif args.command == "consult":
            from .experts import consult as consult_experts
            memo = consult_experts(args.question)
            if args.script_out:
                if memo["script"] is None:
                    raise ValueError("No expert proposed an action, so no script was written")
                with open(args.script_out, "x", encoding="utf-8") as handle:
                    handle.write(json.dumps(memo["script"], indent=2) + "\n")
            print(json.dumps(memo, indent=2))
        elif args.command == "risk-gate":
            from .riskgate import assess, render_risk_html
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            targets = [out / "risk-gate.json", out / "risk-gate.html"]
            if any(p.exists() for p in targets):
                raise ValueError("Output files already exist; choose a new --out directory")
            forum = _load(args.forum, 16 * 1024 * 1024) if args.forum else None
            result = assess(_load(args.manifest, 64 * 1024 * 1024), forum)
            serialized = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            for target, content in zip(targets, [serialized, render_risk_html(result)]):
                with target.open("x", encoding="utf-8") as handle:
                    handle.write(content)
            print(json.dumps({"decision": result["decision"], "released": len(result["released"]),
                              "held": result["held"], "blocked": result["blocked"],
                              "gates": {g["id"]: g["passed"] for g in result["gates"]},
                              "gate": str(targets[0]), "page": str(targets[1]),
                              "content_digest": result["content_digest"]}, indent=2))
        elif args.command == "risk-gate-verify":
            from .riskgate import verify_risk_gate
            forum = _load(args.forum, 16 * 1024 * 1024) if args.forum else None
            result = verify_risk_gate(_load(args.gate, 16 * 1024 * 1024), _load(args.manifest, 64 * 1024 * 1024), forum)
            print(json.dumps(result, indent=2))
            return 0 if result["valid"] else 1
        elif args.command == "forum":
            from .forum import RULE_REVIEWERS, ModelReviewer, render_forum_html, run_forum
            from .providers import client_from_config
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            targets = [out / "forum.json", out / "forum.html"]
            if any(p.exists() for p in targets):
                raise ValueError("Output files already exist; choose a new --out directory")
            reviewers = list(RULE_REVIEWERS)
            for path in args.reviewer_config:
                config = _load(path, 64 * 1024)
                if not isinstance(config, dict) or not isinstance(config.get("model"), dict):
                    raise ValueError("reviewer config needs name, optional focus, and a model object")
                reviewers.append((config.get("name"), ModelReviewer(config.get("name"), client_from_config(config["model"]),
                                                                    config.get("focus", "general methods and evidence"))))
            result = run_forum(_load(args.manifest, 64 * 1024 * 1024), args.topic, reviewers)
            serialized = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            for target, content in zip(targets, [serialized, render_forum_html(result)]):
                with target.open("x", encoding="utf-8") as handle:
                    handle.write(content)
            print(json.dumps({"verdict": result["synthesis"]["verdict"], "posts": len(result["posts"]),
                              "rejected": len(result["rejected"]), "concerns": result["synthesis"]["concern_counts"],
                              "forum": str(targets[0]), "page": str(targets[1]),
                              "content_digest": result["content_digest"]}, indent=2))
        elif args.command == "forum-verify":
            from .forum import verify_forum
            result = verify_forum(_load(args.forum, 16 * 1024 * 1024), _load(args.manifest, 64 * 1024 * 1024))
            print(json.dumps(result, indent=2))
            return 0 if result["valid"] else 1
        elif args.command == "panel":
            from .panel import ModelPanelist, render_panel_html, run_panel
            from .providers import client_from_config
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            targets = [out / "panel.json", out / "panel.html"]
            if any(p.exists() for p in targets):
                raise ValueError("Output files already exist; choose a new --out directory")
            panelists = {}
            for path in args.panelist_config:
                config = _load(path, 64 * 1024)
                if not isinstance(config, dict) or not isinstance(config.get("model"), dict):
                    raise ValueError("panelist config needs name, optional focus, and a model object")
                panelists[config.get("name")] = ModelPanelist(client_from_config(config["model"]),
                                                              config.get("focus", "methods, evidence and comparability across runs"))
            unknown = set(panelists) - {name for name, _ in args.run}
            if unknown:
                raise ValueError(f"panelist config names no submitted run: {sorted(map(str, unknown))}")
            entries = [(name, _load(path, 64 * 1024 * 1024), panelists.get(name)) for name, path in args.run]
            result = run_panel(entries, args.topic)
            serialized = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
            for target, content in zip(targets, [serialized, render_panel_html(result)]):
                with target.open("x", encoding="utf-8") as handle:
                    handle.write(content)
            runs = result["synthesis"]["runs"]
            print(json.dumps({"verdicts": {n: r["verdict"] for n, r in runs.items()},
                              "conceded": {n: len(r["conceded"]) for n, r in runs.items()},
                              "posts": len(result["posts"]), "rejected": len(result["rejected"]),
                              "panel": str(targets[0]), "page": str(targets[1]),
                              "content_digest": result["content_digest"]}, indent=2))
        elif args.command == "panel-verify":
            from .panel import verify_panel
            manifests = {name: _load(path, 64 * 1024 * 1024) for name, path in args.run}
            if len(manifests) != len(args.run):
                raise ValueError("each researcher may be named once")
            result = verify_panel(_load(args.panel, 16 * 1024 * 1024), manifests)
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

"""Small JSON capability checks against explicitly selected, preexisting Vertex models."""

import argparse
import json
import os
from pathlib import Path
import socket
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sewall.llm import LLMError, ModelConfig, VertexClient


def main():
    if not os.environ.get("SLURM_JOB_ID") or not os.environ.get("SLURM_JOB_PARTITION", "").startswith("cpu"):
        raise SystemExit("Model probes require an allocated Slurm CPU node")
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--api", choices=("gemini", "openai"), default="gemini")
    parser.add_argument("--api-version", choices=("v1", "v1beta1"), default="v1")
    parser.add_argument("--thinking-level")
    args = parser.parse_args()
    if len(args.model) > 3:
        parser.error("At most three explicit models per bounded probe")
    result = {"job_id": os.environ["SLURM_JOB_ID"], "partition": os.environ["SLURM_JOB_PARTITION"],
              "hostname": socket.gethostname(), "time": datetime.now(timezone.utc).isoformat(),
              "project": args.project, "location": args.location, "probes": []}
    for model in args.model:
        thinking = ({"thinking_level": args.thinking_level} if args.thinking_level else
                    {"thinking_budget": 0} if args.api == "gemini" and model.startswith("gemini-2.5") else {})
        config = ModelConfig(args.project, args.location, model, api=args.api,
                             api_version=args.api_version, max_output_tokens=256, timeout_seconds=45, **thinking)
        record = {"config": config.to_dict()}
        try:
            response = VertexClient(config).complete_json(
                "Return the JSON object {\"ready\":true}. This checks a software interface only.",
                {"task": "bounded_json_probe"})
            record.update(status="available" if response["output"] == {"ready": True} else "unexpected_output",
                          response=response)
        except (LLMError, ValueError) as exc:
            record.update(status="unavailable", error=str(exc))
        result["probes"].append(record)
        print(json.dumps({"model": model, "status": record["status"], "error": record.get("error")}))
    path = Path("output") / ("model-probe-" + os.environ["SLURM_JOB_ID"] + ".json")
    path.parent.mkdir(exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print("Probe record: " + str(path))
    return 0 if any(p["status"] == "available" for p in result["probes"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

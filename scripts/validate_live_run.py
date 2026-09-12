"""Validate retained Sewall.ai live-run artifacts on a Slurm CPU node.

From prototype/ inside an allocated CPU environment:
  crun -p .waterfield-env python3 scripts/validate_live_run.py \
      output/example/manifest.json --out output/example-validation.json

This script makes no model or source requests. A valid failed-run artifact is
accepted unless --require-completed is set. Receipts are unsigned local audit
records, not authorization, independent source authentication, or scientific
validation. jsonschema is required; validation is never silently skipped.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sewall.agent import require_cpu_allocation, verify_agent_manifest


MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_MANIFESTS = 20
TOKEN_FIELDS = ("input_tokens", "output_tokens", "total_tokens", "thinking_tokens")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object key")
        value[key] = item
    return value


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Non-finite JSON number")
    return number


def _read_json(path, limit=MAX_ARTIFACT_BYTES):
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("JSON artifact exceeds the byte limit")
    value = json.loads(
        raw, object_pairs_hook=_unique_object,
        parse_constant=_reject_constant, parse_float=_finite_float,
    )
    if not isinstance(value, dict):
        raise ValueError("JSON artifact must be an object")
    return value, raw


def _local_schema_refs(value):
    """Prevent schema resolution from requesting an external document."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("$ref", "$dynamicRef") and (
                not isinstance(item, str) or not item.startswith("#")
            ):
                raise ValueError("Validation schema must contain only local references")
            _local_schema_refs(item)
    elif isinstance(value, list):
        for item in value:
            _local_schema_refs(item)


def _token_summary(calls):
    totals = {field: 0 for field in TOKEN_FIELDS}
    missing_total_calls = 0
    for call in calls:
        usage = call.get("trace", {}).get("usage", {})
        if not isinstance(usage, dict):
            raise ValueError("Model usage metadata must be an object")
        if usage.get("total_tokens") is None:
            missing_total_calls += 1
        for field in TOKEN_FIELDS:
            value = usage.get(field)
            if value is None:
                continue
            if type(value) is not int or not 0 <= value <= 100_000_000:
                raise ValueError("Invalid model token usage count")
            totals[field] += value
    return totals, missing_total_calls


def _validate(path, validator, require_completed):
    result = {"path": str(path), "valid": False, "schema_valid": False, "recorded_trace_valid": False}
    try:
        manifest, raw = _read_json(path)
        result.update(artifact_sha256=hashlib.sha256(raw).hexdigest(), artifact_bytes=len(raw))
        if manifest.get("mode") != "live_public_metadata":
            raise ValueError("Expected mode live_public_metadata")
        metrics = manifest.get("metrics")
        if (
            manifest.get("claims") != [] or not isinstance(metrics, dict)
            or type(metrics.get("scientific_claims")) is not int
            or metrics["scientific_claims"] != 0
        ):
            raise ValueError("Scientific claims must be empty and the reported count must be integer zero")
        errors = []
        for error in validator.iter_errors(manifest):
            # Paths and validator names are sufficient to locate bad fields.
            # Do not copy potentially large or instruction-bearing instance text.
            errors.append({"path": str(error.json_path)[:300], "rule": str(error.validator)[:100]})
            if len(errors) == 5:
                break
        if errors:
            result["schema_errors"] = errors
            raise ValueError("Manifest does not satisfy the live-run schema; first five errors retained")
        result["schema_valid"] = True
        replay = verify_agent_manifest(manifest)
        result["recorded_trace"] = replay
        if not replay["valid"]:
            raise ValueError("Recorded graph and evidence verification failed")
        result["recorded_trace_valid"] = True
        calls = manifest["model_calls"]
        totals, missing_usage = _token_summary(calls)
        for field, observed in (
            ("records", len(manifest["records"])),
            ("model_calls", len(calls)),
            ("token_usage_missing_calls", missing_usage),
            *totals.items(),
        ):
            if type(metrics.get(field)) is not int or metrics[field] != observed:
                raise ValueError(f"Reported {field} differs from retained evidence")
        model_configs = manifest["context"]["models"]
        configured_models = {
            role: ({key: config[key] for key in ("project", "location", "model", "api", "api_version")}
                   if config is not None else None)
            for role, config in model_configs.items()
        }
        result.update(
            run_id=manifest["run_id"], status=manifest["status"], stop_reason=manifest["stop_reason"],
            mode=manifest["mode"], revision=manifest["revision"],
            record_count=len(manifest["records"]),
            record_ids=[record["id"] for record in manifest["records"]],
            models=configured_models,
            model_call_count=len(calls),
            completed_model_calls=sum(call["status"] == "completed" for call in calls),
            failed_model_calls=sum(call["status"] == "failed" for call in calls),
            token_totals=totals, token_usage_missing_calls=missing_usage,
            verified_metadata_links=metrics.get("verified_links"),
            scientific_claims=0, content_digest=manifest["content_digest"],
            run_runtime=manifest["context"]["runtime"],
        )
        if require_completed and manifest["status"] != "completed":
            raise ValueError("The artifact is valid, but its run status is not completed")
        result["valid"] = True
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError) as exc:
        result["error"] = str(exc)[:500]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline schema and recorded-trace validation for live metadata artifacts")
    parser.add_argument("manifests", nargs="+", help="One or more retained manifest.json paths")
    parser.add_argument("--out", type=Path, help="Optional new JSON receipt; an existing path is never overwritten")
    parser.add_argument("--require-completed", action="store_true", help="Also require every run to have completed status")
    args = parser.parse_args(argv)
    if len(args.manifests) > MAX_MANIFESTS:
        parser.error(f"At most {MAX_MANIFESTS} manifests may be validated per invocation")
    try:
        runtime = require_cpu_allocation()
        paths = [Path(value).resolve() for value in args.manifests]
        if len(set(paths)) != len(paths):
            raise ValueError("Manifest paths must be distinct")
        if args.out is not None and (args.out.exists() or args.out.is_symlink()):
            raise ValueError("Receipt path already exists; choose a new --out path")
        from jsonschema import Draft202012Validator, FormatChecker
        from jsonschema.exceptions import SchemaError

        schema_path = ROOT / "schemas/agent_manifest.schema.json"
        schema, schema_raw = _read_json(schema_path, 1024 * 1024)
        _local_schema_refs(schema)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            raise ValueError("The local manifest schema is invalid") from None
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        artifacts = [_validate(path, validator, args.require_completed) for path in paths]
        receipt = {
            "kind": "sewall_live_run_validation_receipt", "schema_version": "0.2.0",
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "validation_runtime": {**runtime, "python_version": sys.version.split()[0]},
            "schema_id": schema["$id"], "schema_sha256": hashlib.sha256(schema_raw).hexdigest(),
            "require_completed": args.require_completed,
            "valid": all(item["valid"] for item in artifacts),
            "artifacts": artifacts, "model_calls_performed": 0, "source_requests_performed": 0,
            "limitations": [
                "Validation checks retained structure, reported counts, and recorded event/evidence consistency.",
                "A valid artifact may describe a failed or incomplete run unless --require-completed was requested.",
                "Digests and this unsigned receipt do not authenticate original sources, grant access, or validate scientific findings.",
                "Recorded Slurm and model configuration values describe the artifact; validation does not independently authenticate them.",
            ],
        }
        if args.out is not None:
            with args.out.open("x", encoding="utf-8") as handle:
                json.dump(receipt, handle, indent=2, ensure_ascii=True, allow_nan=False)
                handle.write("\n")
        for item in artifacts:
            summary = {key: item[key] for key in (
                "path", "valid", "status", "record_count", "model_call_count", "token_totals",
                "token_usage_missing_calls", "artifact_sha256", "run_runtime", "error", "schema_errors",
            ) if key in item}
            if "models" in item:
                summary["models"] = {role: config["model"] if config else None for role, config in item["models"].items()}
            print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
        print(json.dumps({
            "valid": receipt["valid"], "artifacts": len(artifacts),
            "validation_runtime": receipt["validation_runtime"],
            "receipt": str(args.out.resolve()) if args.out is not None else None,
        }, sort_keys=True))
        return 0 if receipt["valid"] else 1
    except ImportError:
        parser.exit(2, "validate_live_run: jsonschema is required; validation was not performed\n")
    except (OSError, ValueError, TypeError, KeyError, RuntimeError) as exc:
        parser.exit(2, "validate_live_run: " + str(exc)[:500] + "\n")


if __name__ == "__main__":
    raise SystemExit(main())

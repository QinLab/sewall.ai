"""Read-only runtime inventory for a Slurm CPU allocation with Vertex AI access.

Set SEWALL_GCP_PROJECT to the Google Cloud project whose endpoints should be listed.
"""

import importlib.metadata
import json
import os
from pathlib import Path
import socket
import sys
from datetime import datetime, timezone

PROJECT = os.environ.get("SEWALL_GCP_PROJECT", "your-gcp-project")


def main():
    if not os.environ.get("SLURM_JOB_ID") or not os.environ.get("SLURM_JOB_PARTITION", "").startswith("cpu"):
        raise SystemExit("Run this probe inside a Slurm CPU allocation")
    result = {
        "time": datetime.now(timezone.utc).isoformat(), "hostname": socket.gethostname(),
        "slurm_job_id": os.environ["SLURM_JOB_ID"], "partition": os.environ["SLURM_JOB_PARTITION"],
        "python": sys.version.split()[0], "packages": {}, "project": PROJECT,
        "requests": [],
    }
    for package in ("google-auth", "requests", "mcp", "jsonschema"):
        try:
            result["packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result["packages"][package] = None
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession, Request
        credentials, auth_project = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(Request())
        result["adc"] = {"available": True, "credential_type": type(credentials).__name__,
                         "quota_project": getattr(credentials, "quota_project_id", None),
                         "default_project": auth_project}
        session = AuthorizedSession(credentials)
        for region in ("us-central1", "us-east5"):
            url = (f"https://{region}-aiplatform.googleapis.com/v1/projects/"
                   f"{PROJECT}/locations/{region}/endpoints?pageSize=20")
            response = session.get(url, timeout=20)
            item = {"operation": "list_existing_endpoints", "region": region,
                    "http_status": response.status_code}
            if response.status_code == 200:
                document = response.json()
                item["endpoints"] = [
                    {"name": e.get("name"), "display_name": e.get("displayName"),
                     "deployed_models": [{"model": m.get("model"), "display_name": m.get("displayName")}
                                         for m in e.get("deployedModels", [])]}
                    for e in document.get("endpoints", [])]
                item["truncated"] = bool(document.get("nextPageToken"))
            result["requests"].append(item)
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = "ADC or read-only inventory failed; credentials and raw errors withheld"
    output = Path("output") / ("cluster-probe-" + os.environ["SLURM_JOB_ID"] + ".json")
    output.parent.mkdir(exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
        handle.write("\n")
    print(json.dumps(result, indent=2))
    return 1 if "error" in result else 0


if __name__ == "__main__":
    raise SystemExit(main())

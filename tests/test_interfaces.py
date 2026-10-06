"""Offline CLI and optional real MCP stdio integration checks."""

import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PROTOTYPE = Path(__file__).resolve().parents[1]

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    MCP_AVAILABLE = False
else:
    MCP_AVAILABLE = True


def mcp_runtime_environment():
    """Retain Python/container paths omitted by the SDK's minimal environment.

    Waterfield crun can load packages from an overlay while sys.executable still
    names its base interpreter. Reusing that interpreter alone loses the overlay.
    Copy actual parent import paths, plus only relevant runtime variables.
    Credentials and unrelated environment variables are not forwarded.
    """
    allowed = ("PYTHONHOME", "PYTHONUSERBASE", "PYTHONNOUSERSITE", "VIRTUAL_ENV",
               "CONDA_PREFIX", "LD_LIBRARY_PATH", "LANG", "LC_ALL", "PYTHONUNBUFFERED",
               "SLURM_JOB_ID", "SLURM_JOB_PARTITION")
    env = {key: os.environ[key] for key in allowed
           if os.environ.get(key) and not os.environ[key].startswith("()")}
    paths = [str(PROTOTYPE), *(path for path in sys.path if path and os.path.isabs(path))]
    env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
    return env


class MCPRuntimeEnvironmentTests(unittest.TestCase):
    def test_python_overlay_is_retained_without_tokens_or_unrelated_settings(self):
        values = {"VIRTUAL_ENV": "/runtime/overlay", "PYTHONUSERBASE": "/runtime/overlay",
                  "LD_LIBRARY_PATH": "/runtime/libraries", "SLURM_JOB_ID": "123",
                  "SLURM_JOB_PARTITION": "cpu-2", "GOOGLE_API_KEY": "private-token",
                  "GOOGLE_APPLICATION_CREDENTIALS": "/private/credentials.json",
                  "UNRELATED_PRIVATE_VALUE": "secret", "LANG": "() shell function"}
        with patch.dict(os.environ, values, clear=True), patch.object(sys, "path", ["/runtime/overlay/site-packages", "", "/runtime/base/site-packages"]):
            result = mcp_runtime_environment()
        self.assertIn("/runtime/overlay/site-packages", result["PYTHONPATH"].split(os.pathsep))
        self.assertEqual(result["VIRTUAL_ENV"], "/runtime/overlay")
        self.assertEqual(result["SLURM_JOB_PARTITION"], "cpu-2")
        self.assertNotIn("GOOGLE_API_KEY", result)
        self.assertNotIn("GOOGLE_APPLICATION_CREDENTIALS", result)
        self.assertNotIn("UNRELATED_PRIVATE_VALUE", result)
        self.assertNotIn("LANG", result)


class CLIIntegrationTests(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "sewall", *map(str, args)],
            cwd=PROTOTYPE,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    def successful(self, *args):
        process = self.invoke(*args)
        self.assertEqual(process.returncode, 0, process.stderr)
        return json.loads(process.stdout)

    def test_demo_refine_and_replay_roundtrip(self):
        with tempfile.TemporaryDirectory(prefix="sewall-interfaces-") as temporary:
            original_dir = Path(temporary) / "original run"
            refined_dir = Path(temporary) / "refined run"
            result = self.successful("demo", "--out", original_dir)
            original_path = original_dir / "manifest.json"
            original = json.loads(original_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["mode"], "synthetic_fixture")
            self.assertEqual(result["revision"], 1)
            self.assertEqual(Path(result["manifest"]), original_path)
            self.assertIn("SYNTHETIC FIXTURE SIMULATION", (original_dir / "report.html").read_text(encoding="utf-8"))
            self.assertEqual(original["metrics"]["network_bytes"], 0)
            replay = self.successful("replay", original_path)
            self.assertTrue(replay["valid"])
            self.assertTrue(replay["replayed"])

            refined_result = self.successful(
                "refine", original_path, "--question", "Inspect organism traits",
                "--focus", "traits", "--out", refined_dir,
            )
            refined_path = refined_dir / "manifest.json"
            refined = json.loads(refined_path.read_text(encoding="utf-8"))
            self.assertEqual(refined_result["revision"], 2)
            self.assertEqual(refined["parent_digest"], original["content_digest"])
            self.assertEqual(refined["plan"]["selected_sources"], ["eol", "pubmed"])
            self.assertEqual(refined["claims"], [])
            self.assertTrue((refined_dir / "report.html").is_file())
            self.assertTrue(self.successful("replay", refined_path)["valid"])
            self.assertEqual(json.loads(original_path.read_text(encoding="utf-8")), original)

    def test_demo_refuses_to_overwrite_existing_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="sewall-overwrite-") as temporary:
            output = Path(temporary) / "run"
            self.successful("demo", "--out", output)
            paths = [output / "manifest.json", output / "report.html"]
            before = {path: path.read_bytes() for path in paths}
            process = self.invoke("demo", "--focus", "traits", "--out", output)
            self.assertEqual(process.returncode, 2)
            self.assertIn("already exist", process.stderr)
            self.assertEqual({path: path.read_bytes() for path in paths}, before)

    def test_tampered_manifest_fails_replay_and_refinement(self):
        with tempfile.TemporaryDirectory(prefix="sewall-tamper-") as temporary:
            output = Path(temporary) / "original"
            self.successful("demo", "--out", output)
            path = output / "manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["question"] = "A question changed after recording"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            replay = self.invoke("replay", path)
            self.assertEqual(replay.returncode, 1)
            self.assertFalse(json.loads(replay.stdout)["valid"])
            refined_output = Path(temporary) / "refused"
            refined = self.invoke("refine", path, "--question", "Inspect traits", "--out", refined_output)
            self.assertEqual(refined.returncode, 2)
            self.assertIn("digest mismatch", refined.stderr)
            self.assertFalse(refined_output.exists())


@unittest.skipUnless(MCP_AVAILABLE, "Optional MCP SDK is not installed")
class MCPStdioIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def result_dict(self, result):
        self.assertFalse(result.isError, str(result.content))
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict):
            return structured
        for block in result.content:
            if getattr(block, "type", None) == "text":
                value = json.loads(block.text)
                if isinstance(value, dict):
                    return value
        self.fail("Expected a JSON object from the MCP tool")

    async def test_real_stdio_plan_run_and_verify(self):
        async def roundtrip():
            params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "sewall.mcp_server"],
                cwd=str(PROTOTYPE),
                env=mcp_runtime_environment(),
            )
            with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as server_log:
                async with stdio_client(params, errlog=server_log) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        try:
                            initialized = await session.initialize()
                        except Exception as exc:
                            server_log.flush()
                            server_log.seek(0)
                            diagnostic = server_log.read(4096)
                            self.fail(f"MCP initialize failed ({type(exc).__name__}); bounded server stderr:\n{diagnostic}")
                        self.assertEqual(initialized.serverInfo.name, "Sewall.ai")
                        listed = await session.list_tools()
                        tools_by_name = {tool.name: tool for tool in listed.tools}
                        self.assertTrue({"propose_research", "run_fixture", "verify_fixture", "refine_fixture",
                                         "draft_data_access_request", "research_public_metadata",
                                         "verify_public_metadata_run"}.issubset(tools_by_name))
                        self.assertFalse(tools_by_name["run_fixture"].annotations.openWorldHint)
                        research_tool = tools_by_name["research_public_metadata"]
                        self.assertTrue(research_tool.annotations.openWorldHint)
                        self.assertFalse(research_tool.annotations.idempotentHint)
                        self.assertFalse(tools_by_name["verify_public_metadata_run"].annotations.openWorldHint)
                        # FastMCP derives a Pydantic argument model. model_config is
                        # reserved by Pydantic and prevents the entire server starting.
                        properties = research_tool.inputSchema["properties"]
                        self.assertIn("planner_config", properties)
                        self.assertIn("reviewer_config", properties)
                        self.assertNotIn("model_config", properties)
                        self.assertIn("planner_config", research_tool.inputSchema["required"])

                        plan = self.result_dict(await session.call_tool(
                            "propose_research", {"question": "Inspect organism traits", "focus": "traits"},
                        ))
                        self.assertEqual(plan["mode"], "synthetic_fixture")
                        self.assertFalse(plan["external_actions"])
                        self.assertEqual(plan["selected_sources"], ["eol", "pubmed"])

                        manifest = self.result_dict(await session.call_tool(
                            "run_fixture", {
                                "question": "Inspect genotype and expression with trait and habitat context",
                                "focus": "cross-scale", "scenario": "policy-review",
                            },
                        ))
                        self.assertEqual(manifest["status"], "needs_review")
                        self.assertEqual(manifest["mode"], "synthetic_fixture")
                        self.assertEqual(manifest["metrics"]["network_bytes"], 0)
                        self.assertEqual(manifest["claims"], [])
                        self.assertNotIn("source:ncbi_genotype", {node["id"] for node in manifest["graph"]["nodes"]})
                        verified = self.result_dict(await session.call_tool("verify_fixture", {"manifest": manifest}))
                        self.assertTrue(verified["valid"])
                        self.assertTrue(verified["replayed"])

                        tampered = deepcopy(manifest)
                        tampered["question"] = "Changed outside the graph controller"
                        rejected = self.result_dict(await session.call_tool("verify_fixture", {"manifest": tampered}))
                        self.assertFalse(rejected["valid"])
                        self.assertFalse(rejected["replayed"])
                        unsupported_live = self.result_dict(await session.call_tool(
                            "verify_public_metadata_run", {"manifest": manifest},
                        ))
                        self.assertFalse(unsupported_live["valid"])
                        self.assertEqual(unsupported_live["kind"], "recorded_trace_replay")
                        recorded = self.result_dict(await session.call_tool("verify_recorded_run", {"manifest": manifest}))
                        self.assertEqual((recorded["valid"], recorded["mode"]), (True, "synthetic_fixture"))
                        skills = self.result_dict(await session.call_tool("list_skills", {}))["skills"]
                        self.assertIn("taxon_inventory", {item["operation"] for item in skills})
                        stopped = self.result_dict(await session.call_tool("run_safety_demo", {}))
                        self.assertEqual(stopped["status"], "safe_stopped")
                        self.assertTrue(tools_by_name["run_planned_research"].annotations.openWorldHint)
                        self.assertFalse(tools_by_name["list_skills"].annotations.openWorldHint)

        await asyncio.wait_for(roundtrip(), timeout=35)


if __name__ == "__main__":
    unittest.main()

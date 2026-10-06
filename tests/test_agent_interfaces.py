"""CLI and MCP live boundaries with registration stubs and no external calls.

The optional MCP SDK is deliberately not required by this module. Its separate
stdio integration test exercises the real SDK when that dependency is installed.
Run these tests on allocated Slurm CPUs with the rest of the suite.
"""

from contextlib import redirect_stderr, redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from sewall.__main__ import main
from sewall.llm import ModelConfig


CONFIG = {"project": "example-project", "location": "us-central1", "model": "gemini-example"}
ARGS = ["agent", "--question", "Inspect public plant metadata", "--config", "planner.json", "--out", "unused-output"]


class AgentCLITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="sewall-agent-cli-")
        self.addCleanup(temporary.cleanup)
        self.output_path = str(Path(temporary.name) / "new-run")
        self.argv = [*ARGS[:-1], self.output_path]

    def test_login_guard_precedes_config_loading_and_all_model_work(self):
        with patch("sewall.agent.require_cpu_allocation", side_effect=RuntimeError("CPU allocation required")), \
                patch("sewall.__main__._load") as load, patch("sewall.llm.VertexClient") as client, \
                patch("sewall.agent.run_agent") as run, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(self.argv)
        self.assertEqual(caught.exception.code, 2)
        load.assert_not_called()
        client.assert_not_called()
        run.assert_not_called()

    def test_explicit_configs_budgets_previous_and_result_are_forwarded(self):
        reviewer_config = {**CONFIG, "api": "openai", "model": "meta/llama-example", "api_version": "v1beta1"}
        previous = {"prior": "retained manifest"}
        files = {"planner.json": CONFIG, "reviewer.json": reviewer_config, "previous.json": previous}
        clients = [Mock(), Mock()]
        manifest = {"status": "completed"}
        argv = self.argv + ["--reviewer-config", "reviewer.json", "--previous", "previous.json", "--max-actions", "3",
                       "--max-model-calls", "5", "--max-records", "7", "--per-search", "2", "--max-seconds", "90"]
        with patch("sewall.agent.require_cpu_allocation"), patch("sewall.__main__._load", side_effect=lambda path, *args, **kw: files[path]), \
                patch("sewall.llm.VertexClient", side_effect=clients) as client, \
                patch("sewall.agent.run_agent", return_value=manifest) as run, patch("sewall.__main__._save") as save:
            status = main(argv)
        self.assertEqual(status, 0)
        self.assertEqual(client.call_args_list[0].args[0], ModelConfig.from_dict(CONFIG))
        self.assertEqual(client.call_args_list[1].args[0].api_version, "v1beta1")
        self.assertEqual(run.call_args.args, ("Inspect public plant metadata", clients[0], clients[1]))
        self.assertEqual(run.call_args.kwargs, {"max_actions": 3, "max_model_calls": 5, "max_records": 7,
                                               "per_search": 2, "max_seconds": 90, "previous": previous,
                                               "monitor": None})
        save.assert_called_once_with(manifest, self.output_path)

    def test_incomplete_run_is_saved_and_returns_nonzero(self):
        manifest = {"status": "budget_exhausted"}
        with patch("sewall.agent.require_cpu_allocation"), patch("sewall.__main__._load", return_value=CONFIG), \
                patch("sewall.llm.VertexClient"), patch("sewall.agent.run_agent", return_value=manifest), \
                patch("sewall.__main__._save") as save:
            status = main(self.argv)
        self.assertEqual(status, 1)
        save.assert_called_once_with(manifest, self.output_path)

    def test_existing_output_is_refused_before_paid_or_source_work(self):
        with tempfile.TemporaryDirectory(prefix="sewall-live-collision-") as temporary:
            path = Path(temporary) / "manifest.json"
            path.write_text("retained user artifact", encoding="utf-8")
            argv = [*self.argv[:-1], temporary]
            with patch("sewall.agent.require_cpu_allocation"), patch("sewall.__main__._load", return_value=CONFIG), \
                    patch("sewall.llm.VertexClient"), patch("sewall.agent.run_agent") as run, redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    main(argv)
            self.assertEqual(caught.exception.code, 2)
            run.assert_not_called()
            self.assertEqual(path.read_text(encoding="utf-8"), "retained user artifact")

    def test_agent_replay_has_no_cpu_guard_model_or_source_requirement(self):
        output = io.StringIO()
        manifest = {"mode": "live_public_metadata"}
        checked = {"valid": True, "kind": "recorded_trace_replay", "model_calls_reexecuted": 0}
        with patch("sewall.__main__._load", return_value=manifest), \
                patch("sewall.agent.verify_agent_manifest", return_value=checked) as verify, \
                patch("sewall.agent.require_cpu_allocation") as guard, patch("sewall.llm.VertexClient") as client, \
                patch("sewall.agent.run_agent") as run, redirect_stdout(output):
            status = main(["agent-replay", "retained.json"])
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output.getvalue()), checked)
        verify.assert_called_once_with(manifest)
        guard.assert_not_called()
        client.assert_not_called()
        run.assert_not_called()

    def test_live_replay_accepts_trace_larger_than_legacy_fixture_limit(self):
        # Long but permitted source fields are repeated in saved graph/events.
        with tempfile.TemporaryDirectory(prefix="sewall-live-size-") as temporary:
            path = Path(temporary) / "retained.json"
            path.write_text(json.dumps({"padding": "x" * (5 * 1024 * 1024)}), encoding="utf-8")
            with patch("sewall.agent.verify_agent_manifest", return_value={"valid": True}) as verify, redirect_stdout(io.StringIO()):
                status = main(["agent-replay", str(path)])
            self.assertEqual(status, 0)
            self.assertEqual(len(verify.call_args.args[0]["padding"]), 5 * 1024 * 1024)


class VersionedModelConfigTests(unittest.TestCase):
    def test_explicit_version_preserves_fixed_google_host(self):
        config = ModelConfig.from_dict({**CONFIG, "api": "openai", "model": "meta/llama-example", "api_version": "v1beta1"})
        self.assertEqual(config.endpoint, "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/example-project/locations/us-central1/endpoints/openapi/chat/completions")
        self.assertEqual(config.to_dict()["api_version"], "v1beta1")
        for value in ["../v1", "v2", "v1?url=https://other.example", None]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                ModelConfig.from_dict({**CONFIG, "api_version": value})


class _RegistrationOnlyServer:
    def __init__(self, *args, **kwargs):
        self.run = Mock(side_effect=AssertionError("Tests must not start MCP transport"))
        self.settings = SimpleNamespace(host="127.0.0.1", port=8000, transport_security=SimpleNamespace(
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
            allowed_origins=["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]))

    def tool(self, **kwargs):
        return lambda function: function


def _mcp_functions_without_optional_sdk():
    modules = {name: ModuleType(name) for name in ("mcp", "mcp.server", "mcp.server.fastmcp", "mcp.types")}
    modules["mcp.server.fastmcp"].FastMCP = _RegistrationOnlyServer
    modules["mcp.types"].ToolAnnotations = lambda **kwargs: SimpleNamespace(**kwargs)
    source = Path(__file__).resolve().parents[1] / "sewall/mcp_server.py"
    spec = importlib.util.spec_from_file_location("sewall._mcp_boundary_tests", source)
    module = importlib.util.module_from_spec(spec)
    with patch.dict("sys.modules", modules):
        spec.loader.exec_module(module)
    return module


class AgentMCPBoundaryTests(unittest.TestCase):
    def test_import_only_registers_tools_and_does_not_start_transport(self):
        module = _mcp_functions_without_optional_sdk()
        module.mcp.run.assert_not_called()
        self.assertTrue(callable(module.research_public_metadata))

    def test_live_mcp_guard_precedes_planner_config_and_controller(self):
        module = _mcp_functions_without_optional_sdk()
        with patch("sewall.agent.require_cpu_allocation", side_effect=RuntimeError("CPU allocation required")), \
                patch("sewall.llm.VertexClient") as client, patch("sewall.agent.run_agent") as run:
            with self.assertRaisesRegex(RuntimeError, "CPU allocation"):
                module.research_public_metadata("Public question", planner_config=CONFIG)
        client.assert_not_called()
        run.assert_not_called()

    def test_live_mcp_uses_only_explicit_configs_and_action_budget(self):
        module = _mcp_functions_without_optional_sdk()
        clients = [Mock(), Mock()]
        reviewer_config = {**CONFIG, "model": "gemini-reviewer"}
        manifest = {"status": "no_evidence"}
        with patch("sewall.agent.require_cpu_allocation"), patch("sewall.llm.VertexClient", side_effect=clients), \
                patch("sewall.agent.run_agent", return_value=manifest) as run:
            result = module.research_public_metadata("Public question", planner_config=CONFIG,
                                                     reviewer_config=reviewer_config, max_actions=2)
        self.assertIs(result, manifest)
        run.assert_called_once_with("Public question", clients[0], clients[1], max_actions=2)

    def test_mcp_recorded_replay_is_offline(self):
        module = _mcp_functions_without_optional_sdk()
        checked = {"valid": False, "kind": "recorded_trace_replay"}
        with patch("sewall.agent.verify_agent_manifest", return_value=checked) as verify, \
                patch("sewall.agent.require_cpu_allocation") as guard, patch("sewall.llm.VertexClient") as client:
            result = module.verify_public_metadata_run({"saved": "trace"})
        self.assertEqual(result, checked)
        verify.assert_called_once_with({"saved": "trace"})
        guard.assert_not_called()
        client.assert_not_called()

    def test_planned_research_checks_the_allocation_before_the_plan(self):
        module = _mcp_functions_without_optional_sdk()
        with patch("sewall.agent.require_cpu_allocation", side_effect=RuntimeError("CPU allocation required")), \
                patch("sewall.agent.run_agent") as run:
            with self.assertRaisesRegex(RuntimeError, "CPU allocation"):
                module.run_planned_research("Public question", [{"action": "search"}])
        run.assert_not_called()

    def test_planned_research_runs_the_host_plan_as_a_labeled_script(self):
        module = _mcp_functions_without_optional_sdk()
        steps = [{"action": "search", "database": "pubmed", "query": "Zostera marina", "reason": "Find literature"}]
        with patch("sewall.agent.require_cpu_allocation"), patch("sewall.llm.VertexClient") as vertex, \
                patch("sewall.agent.run_agent", return_value={"status": "completed"}) as run:
            self.assertEqual(module.run_planned_research("Public question", steps, max_actions=2), {"status": "completed"})
        vertex.assert_not_called()
        question, planner, reviewer = run.call_args.args
        self.assertIs(planner, reviewer)
        self.assertEqual(run.call_args.kwargs, {"max_actions": 2})
        self.assertEqual((planner.script["origin"], planner.script["steps"]), ("mcp_host", steps))
        with patch("sewall.agent.require_cpu_allocation"), self.assertRaises(ValueError):
            module.run_planned_research("Public question", [])

    def test_offline_tools_list_skills_and_verify_any_mode(self):
        module = _mcp_functions_without_optional_sdk()
        self.assertIn("taxon_inventory", {item["operation"] for item in module.list_skills()["skills"]})
        demo = module.run_safety_demo("critical_critique")
        self.assertEqual(module.verify_recorded_run(demo)["valid"], True)
        demo["status"] = "completed"
        self.assertEqual(module.verify_recorded_run(demo)["valid"], False)

    def test_http_transport_binds_loopback_and_names_tunnel_hosts(self):
        module = _mcp_functions_without_optional_sdk()
        module.mcp.run = Mock()
        module.main(["--transport", "streamable-http", "--port", "8123", "--allowed-host", "abc.example.app"])
        module.mcp.run.assert_called_once_with(transport="streamable-http")
        settings = module.mcp.settings
        self.assertEqual((settings.host, settings.port), ("127.0.0.1", 8123))
        self.assertIn("abc.example.app", settings.transport_security.allowed_hosts)
        self.assertIn("https://abc.example.app", settings.transport_security.allowed_origins)
        for argv in (["--port", "80"], ["--allowed-host", "evil.example/path"], ["--transport", "sse"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                module.main(argv)
        module.mcp.run.reset_mock()
        module.main([])
        module.mcp.run.assert_called_once_with(transport="stdio")


if __name__ == "__main__":
    unittest.main()

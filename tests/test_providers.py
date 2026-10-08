"""Anthropic and OpenAI configuration, transport and response boundaries; no live calls."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from sewall.__main__ import main
from sewall.llm import LLMError, ModelConfig
from sewall.providers import (
    AnthropicClient,
    OpenAIClient,
    ProviderConfig,
    client_from_config,
    recorded_config,
)
from test_llm import Response

SECRET = "sk-test-not-a-real-key"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def claude(text='{"ready": true}', **extra):
    return {"id": "msg_123", "type": "message", "role": "assistant", "model": "claude-example-20260101",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5}, **extra}


def gpt(content='{"ready": true}', finish="stop", **message):
    return {"id": "chatcmpl-123", "object": "chat.completion", "model": "gpt-example-2026",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content, **message}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


class Transport:
    """Capture each request, including its headers, and return one canned response."""

    def __init__(self, document=None, url=ANTHROPIC_URL, error=None):
        self.document, self.url, self.error = document, url, error
        self.requests, self.headers_seen, self.handlers = [], [], []

    def __call__(self, *handlers):
        self.handlers.append(handlers)
        opener = Mock()
        opener.open.side_effect = self.open
        return opener

    def open(self, request, timeout):
        self.requests.append(request)
        self.headers_seen.append(dict(request.header_items()))
        if self.error:
            raise self.error
        raw = self.document if isinstance(self.document, bytes) else json.dumps(self.document).encode()
        return Response(raw, self.url)


def anthropic(**kwargs):
    return AnthropicClient(ProviderConfig.from_dict({"provider": "anthropic", "model": "claude-example", **kwargs}))


def openai(**kwargs):
    return OpenAIClient(ProviderConfig.from_dict({"provider": "openai", "model": "gpt-example", **kwargs}))


class ConfigTests(unittest.TestCase):
    def test_round_trip_and_endpoints(self):
        value = ProviderConfig.from_dict({"provider": "openai", "model": "gpt-example"})
        self.assertEqual(ProviderConfig.from_dict(value.to_dict()), value)
        self.assertEqual(value.endpoint, OPENAI_URL)
        self.assertEqual(value.key_variable, "OPENAI_API_KEY")
        self.assertEqual(anthropic().config.endpoint, ANTHROPIC_URL)
        local = ProviderConfig.from_dict({"provider": "openai", "model": "llama3.1:8b", "base_url": "http://127.0.0.1:11434/v1/"})
        self.assertEqual(local.endpoint, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertTrue(local.local)
        self.assertEqual(ProviderConfig.from_dict({"provider": "anthropic", "model": "c", "api_key_env": "LAB_KEY"}).key_variable, "LAB_KEY")
        # A custom endpoint never inherits the default key variable.
        self.assertIsNone(local.key_variable)
        remote = ProviderConfig.from_dict({"provider": "openai", "model": "m", "base_url": "https://lab.example/v1"})
        self.assertIsNone(remote.key_variable)
        self.assertEqual(ProviderConfig.from_dict({**remote.to_dict(), "api_key_env": "LAB_KEY"}).key_variable, "LAB_KEY")

    def test_rejects_keys_urls_and_unbounded_values(self):
        invalid = [
            {"provider": "gemini"}, {"model": "../x"}, {"model": "https://other.example"}, {"model": ""},
            {"max_output_tokens": 0}, {"max_output_tokens": 8193}, {"max_output_tokens": True},
            {"timeout_seconds": 121}, {"retries": 1}, {"temperature": 2}, {"temperature": "0"},
            {"api_key_env": SECRET}, {"api_key_env": "lower"}, {"api_key": SECRET},
            {"base_url": "http://api.example.com/v1"}, {"base_url": "https://user:pw@api.example.com/v1"},
            {"base_url": "https://api.example.com/v1?key=1"}, {"base_url": "ftp://127.0.0.1/v1"},
            {"base_url": "https://api.example.com:bad/v1"}, {"reasoning_effort": "extreme"},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                ProviderConfig.from_dict({"provider": "openai", "model": "gpt-example", **overrides})
        for overrides in ({"base_url": "https://127.0.0.1/v1"}, {"reasoning_effort": "low"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                ProviderConfig.from_dict({"provider": "anthropic", "model": "claude-example", **overrides})
        for document in ({}, {"model": "gpt-example"}, [], {"provider": "openai"}):
            with self.subTest(document=document), self.assertRaises(ValueError):
                ProviderConfig.from_dict(document)

    def test_dispatch_and_recorded_config(self):
        vertex = {"project": "example-project", "location": "us-central1", "model": "gemini-example"}
        with patch("sewall.llm.VertexClient") as client:
            client_from_config(vertex)
            client_from_config({**vertex, "provider": "vertex"})
        self.assertEqual([call.args[0] for call in client.call_args_list], [ModelConfig.from_dict(vertex)] * 2)
        self.assertIsInstance(client_from_config({"provider": "anthropic", "model": "claude-example"}), AnthropicClient)
        self.assertIsInstance(client_from_config({"provider": "openai", "model": "gpt-example"}), OpenAIClient)
        for bad in ({"provider": "other", "model": "x"}, "anthropic", {"provider": "anthropic", "model": "c", "project": "p"}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                client_from_config(bad)
        for field, value in (("base_url", "https://lab.example/v1"), ("api_key_env", "LAB_KEY")):
            config = {"provider": "openai", "model": "gpt-example", field: value}
            self.assertIsInstance(client_from_config(config), OpenAIClient)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "not accepted"):
                client_from_config(config, allow_endpoint_fields=False)
        self.assertIsInstance(client_from_config({"provider": "openai", "model": "gpt-example"},
                                                 allow_endpoint_fields=False), OpenAIClient)
        recorded = recorded_config(openai(api_key_env="LAB_KEY"))
        self.assertEqual(recorded["api_key_env"], "LAB_KEY")
        self.assertEqual(recorded_config(Mock(config="other")), None)
        with self.assertRaises(TypeError):
            AnthropicClient(ProviderConfig.from_dict({"provider": "openai", "model": "gpt-example"}))


class TransportTests(unittest.TestCase):
    def test_anthropic_request_response_and_key_handling(self):
        transport = Transport(claude())
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": SECRET}), patch("sewall.providers.build_opener", transport):
            result = anthropic(max_output_tokens=256).complete_json("Plan one step.", {"question": "eelgrass"})
        request = transport.requests[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, ANTHROPIC_URL)
        self.assertEqual(body["model"], "claude-example")
        self.assertEqual(body["max_tokens"], 256)
        self.assertIn("Plan one step.", body["system"])
        self.assertEqual(body["messages"], [{"role": "user", "content": '{"question":"eelgrass"}'}])
        self.assertNotIn("temperature", body)
        self.assertEqual(transport.headers_seen[0]["X-api-key"], SECRET)
        self.assertEqual(transport.headers_seen[0]["Anthropic-version"], "2023-06-01")
        self.assertNotIn("X-api-key", dict(request.header_items()))
        self.assertEqual(result["output"], {"ready": True})
        self.assertEqual((result["provider"], result["api"], result["model_version"]),
                         ("anthropic", "messages", "claude-example-20260101"))
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        self.assertEqual(result["request_id"], "msg_123")
        self.assertEqual(result["attempts"], 1)
        self.assertNotIn(SECRET, json.dumps(result))

    def test_openai_request_uses_completion_token_field_and_json_mode(self):
        transport = Transport(gpt(), url=OPENAI_URL)
        with patch.dict(os.environ, {"OPENAI_API_KEY": SECRET}), patch("sewall.providers.build_opener", transport):
            result = openai(temperature=0, reasoning_effort="low").complete_json("Plan.", {"q": 1})
        body = json.loads(transport.requests[0].data)
        self.assertEqual(body["max_completion_tokens"], 1024)
        self.assertNotIn("max_tokens", body)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual((body["temperature"], body["reasoning_effort"], body["n"]), (0, "low", 1))
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertEqual(transport.headers_seen[0]["Authorization"], "Bearer " + SECRET)
        self.assertNotIn("Authorization", dict(transport.requests[0].header_items()))
        self.assertEqual(result["usage"]["total_tokens"], 15)
        self.assertEqual(result["finish_reason"], "stop")

    def test_compatible_local_server_needs_no_key_and_uses_max_tokens(self):
        url = "http://127.0.0.1:11434/v1/chat/completions"
        transport = Transport(gpt(), url=url)
        with patch.dict(os.environ, {}, clear=True), patch("sewall.providers.build_opener", transport):
            openai(base_url="http://127.0.0.1:11434/v1").complete_json("Plan.", {"q": 1})
        body = json.loads(transport.requests[0].data)
        self.assertEqual(body["max_tokens"], 1024)
        self.assertNotIn("max_completion_tokens", body)
        self.assertNotIn("Authorization", transport.headers_seen[0])

    def test_custom_endpoint_never_receives_the_default_key(self):
        for base_url, url in (("http://127.0.0.1:8001/v1", "http://127.0.0.1:8001/v1/chat/completions"),
                              ("https://lab.example/v1", "https://lab.example/v1/chat/completions")):
            transport = Transport(gpt(), url=url)
            with self.subTest(base_url=base_url), patch.dict(os.environ, {"OPENAI_API_KEY": SECRET}), \
                    patch("sewall.providers.build_opener", transport):
                openai(base_url=base_url).complete_json("Plan.", {"q": 1})
            self.assertNotIn("Authorization", transport.headers_seen[0])
        transport = Transport(gpt(), url="http://127.0.0.1:8001/v1/chat/completions")
        with patch.dict(os.environ, {"OPENAI_API_KEY": SECRET, "VLLM_KEY": "local-key"}), \
                patch("sewall.providers.build_opener", transport):
            openai(base_url="http://127.0.0.1:8001/v1", api_key_env="VLLM_KEY").complete_json("Plan.", {"q": 1})
        self.assertEqual(transport.headers_seen[0]["Authorization"], "Bearer local-key")

    def test_loopback_requests_bypass_proxies(self):
        from urllib.request import ProxyHandler
        local = Transport(gpt(), url="http://127.0.0.1:8001/v1/chat/completions")
        remote = Transport(gpt(), url=OPENAI_URL)
        with patch.dict(os.environ, {"OPENAI_API_KEY": SECRET}):
            with patch("sewall.providers.build_opener", local):
                openai(base_url="http://127.0.0.1:8001/v1").complete_json("Plan.", {"q": 1})
            with patch("sewall.providers.build_opener", remote):
                openai().complete_json("Plan.", {"q": 1})
        proxies = [h for h in local.handlers[0] if isinstance(h, ProxyHandler)]
        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0].proxies, {})
        # Remote HTTPS keeps the default proxy behavior that sites may require.
        self.assertFalse(any(isinstance(h, ProxyHandler) for h in remote.handlers[0]))

    def test_missing_or_malformed_key_fails_before_any_request(self):
        transport = Transport(claude())
        with patch("sewall.providers.build_opener", transport):
            with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(LLMError, "export ANTHROPIC_API_KEY"):
                anthropic().complete_json("Plan.", {})
            with patch.dict(os.environ, {"LAB_KEY": "two words"}), self.assertRaises(LLMError) as caught:
                openai(api_key_env="LAB_KEY").complete_json("Plan.", {})
        self.assertNotIn("two words", str(caught.exception))
        self.assertEqual(transport.requests, [])

    def test_fenced_json_is_accepted_once(self):
        transport = Transport(claude('```json\n{"ready": true}\n```'))
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": SECRET}), patch("sewall.providers.build_opener", transport):
            self.assertEqual(anthropic().complete_json("Plan.", {})["output"], {"ready": True})

    def test_refusal_truncation_tools_and_transport_errors_fail_without_key(self):
        anthropic_bad = [
            claude(stop_reason="max_tokens"), claude(stop_reason="refusal"), claude(role="user"),
            claude(content=[{"type": "tool_use", "id": "t", "name": "x", "input": {}}]), claude(content=[]),
            claude(text="not json"), claude(text="[1, 2]"), claude(usage={"input_tokens": -1}),
            {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}, b"not json",
        ]
        for document in anthropic_bad:
            with self.subTest(document=str(document)[:60]), patch.dict(os.environ, {"ANTHROPIC_API_KEY": SECRET}), \
                    patch("sewall.providers.build_opener", Transport(document)), self.assertRaises(LLMError):
                anthropic().complete_json("Plan.", {})
        thinking = claude(content=[{"type": "thinking", "thinking": "..."}, {"type": "text", "text": '{"ok": 1}'}])
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": SECRET}), patch("sewall.providers.build_opener", Transport(thinking)):
            self.assertEqual(anthropic().complete_json("Plan.", {})["output"], {"ok": 1})
        openai_bad = [gpt(finish="length"), gpt(finish="content_filter"), gpt(refusal="I can't"),
                      gpt(tool_calls=[{"id": "t"}]), gpt(content=None), {**gpt(), "choices": []},
                      {**gpt(), "choices": gpt()["choices"] * 2}]
        for document in openai_bad:
            with self.subTest(document=str(document)[:60]), patch.dict(os.environ, {"OPENAI_API_KEY": SECRET}), \
                    patch("sewall.providers.build_opener", Transport(document, url=OPENAI_URL)), self.assertRaises(LLMError):
                openai().complete_json("Plan.", {})
        errors = [HTTPError(OPENAI_URL, 401, SECRET, {}, None), URLError(SECRET), TimeoutError(SECRET)]
        for error in errors:
            transport = Transport(error=error)
            with self.subTest(error=type(error).__name__), patch.dict(os.environ, {"OPENAI_API_KEY": SECRET}), \
                    patch("sewall.providers.build_opener", transport), self.assertRaises(LLMError) as caught:
                openai().complete_json("Plan.", {})
            self.assertNotIn(SECRET, str(caught.exception))
            self.assertIn("no retry", str(caught.exception))
            self.assertNotIn("Authorization", dict(transport.requests[0].header_items()))
        with patch.dict(os.environ, {"OPENAI_API_KEY": SECRET}), \
                patch("sewall.providers.build_opener", Transport(gpt(), url="https://other.example/")), \
                self.assertRaisesRegex(LLMError, "changed the requested URL"):
            openai().complete_json("Plan.", {})

    def test_unserializable_or_oversized_requests_are_rejected_locally(self):
        transport = Transport(claude())
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": SECRET}), patch("sewall.providers.build_opener", transport):
            for prompt, payload in (("", {}), ("Plan.", []), ("Plan.", {"x": float("nan")}), ("Plan.", {"x": "a" * 2_000_000})):
                with self.subTest(prompt=prompt, payload=str(payload)[:30]), self.assertRaises(ValueError):
                    anthropic().complete_json(prompt, payload)
        self.assertEqual(transport.requests, [])


class CLITests(unittest.TestCase):
    def test_agent_command_builds_provider_clients(self):
        with tempfile.TemporaryDirectory(prefix="sewall-provider-cli-") as directory:
            out = str(Path(directory) / "run")
            files = {"planner.json": {"provider": "anthropic", "model": "claude-example"},
                     "reviewer.json": {"provider": "openai", "model": "gpt-example"}}
            with patch("sewall.agent.require_cpu_allocation"), \
                    patch("sewall.__main__._load", side_effect=lambda path, *args, **kw: files[path]), \
                    patch("sewall.agent.run_agent", return_value={"status": "completed"}) as run, \
                    patch("sewall.__main__._save"):
                status = main(["agent", "--question", "Inspect public plant metadata", "--config", "planner.json",
                               "--reviewer-config", "reviewer.json", "--out", out])
        self.assertEqual(status, 0)
        planner, reviewer = run.call_args.args[1:]
        self.assertIsInstance(planner, AnthropicClient)
        self.assertIsInstance(reviewer, OpenAIClient)


if __name__ == "__main__":
    unittest.main()

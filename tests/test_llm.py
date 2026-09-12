"""Vertex configuration, authentication and transport boundaries; no live calls."""

import copy
import hashlib
import io
import json
import subprocess
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from sewall.llm import (
    LLMError,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    ModelConfig,
    VertexClient,
    _NoRedirect,
    _access_token,
)


class Response(io.BytesIO):
    def __init__(self, raw, url, status=200, headers=None):
        super().__init__(raw)
        self.url = url
        self.status = status
        self.headers = headers or {}

    def geturl(self):
        return self.url

    def getcode(self):
        return self.status


def config(**kwargs):
    return ModelConfig.from_dict({
        "project": "example-project", "location": "us-central1", "model": "gemini-example",
        **kwargs,
    })


def gemini(text='{"ready": true}'):
    return {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": "STOP"}],
        "modelVersion": "gemini-example-version",
        "responseId": "response-123",
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5, "totalTokenCount": 15},
    }


class ModelConfigTests(unittest.TestCase):
    def test_round_trip_and_fixed_google_endpoints(self):
        value = config()
        self.assertEqual(ModelConfig.from_dict(value.to_dict()), value)
        self.assertEqual(value.endpoint, "https://us-central1-aiplatform.googleapis.com/v1/projects/example-project/locations/us-central1/publishers/google/models/gemini-example:generateContent")
        self.assertEqual(config(location="global").endpoint.split("/")[2], "aiplatform.googleapis.com")
        self.assertTrue(config(api="openai", model="google/gemini-example").endpoint.endswith("/endpoints/openapi/chat/completions"))
        self.assertTrue(config(api="openai", endpoint_id="1234").endpoint.endswith("/endpoints/1234/chat/completions"))

    def test_rejects_implicit_projects_urls_credentials_and_unbounded_config(self):
        invalid = [
            {"project": ""}, {"project": "foo/../../bar"}, {"project": "bad.example"},
            {"location": "us-central1.evil.example"}, {"location": "../global"},
            {"model": "../../secret"}, {"model": "https://other.example"}, {"model": "gemini/another"},
            {"api": "custom"}, {"max_output_tokens": 2049}, {"max_output_tokens": True},
            {"max_output_tokens": 0}, {"timeout_seconds": 61}, {"retries": 1}, {"retries": False},
            {"auth_method": "api_key"}, {"endpoint_id": "http://other.example"},
            {"endpoint_id": "123"}, {"thinking_budget": -1}, {"thinking_budget": 1025},
            {"thinking_budget": True}, {"thinking_level": "automatic"},
            {"thinking_budget": 0, "thinking_level": "LOW"},
            {"api": "openai", "thinking_budget": 0},
            {"api": "openai", "model": "publisher/../../model"},
        ]
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                config(**overrides)
        for document in [{}, {"model": "gemini-example"}, {"access_token": "secret"}, []]:
            with self.subTest(document=document), self.assertRaises(ValueError):
                ModelConfig.from_dict(document)


class VertexClientTests(unittest.TestCase):
    def setUp(self):
        self.builder = patch("sewall.llm.build_opener").start()
        self.auth = patch("sewall.llm._access_token", return_value="test-token-do-not-log").start()
        self.addCleanup(patch.stopall)
        self.client = VertexClient(config(thinking_budget=0))
        self.sent = []

    def respond(self, document, *, url=None, status=200, headers=None):
        raw = document if isinstance(document, bytes) else json.dumps(document).encode()

        def open_request(request, **kwargs):
            self.sent.append({"url": request.full_url, "body": json.loads(request.data),
                              "headers": dict(request.header_items()), "timeout": kwargs["timeout"]})
            return Response(raw, url or request.full_url, status, headers)

        self.builder.return_value.open.side_effect = open_request
        return raw

    def test_gemini_payload_caps_audit_and_ephemeral_auth(self):
        raw = self.respond(gemini())
        user = {"question": "public metadata", "reference": "https://untrusted.example/no-fetch"}
        result = self.client.complete_json("Inspect public metadata.", user)
        sent = self.sent[0]
        generation = sent["body"]["generationConfig"]
        self.assertEqual(json.loads(sent["body"]["contents"][0]["parts"][0]["text"]), user)
        self.assertEqual(generation["maxOutputTokens"], 1024)
        self.assertEqual(generation["thinkingConfig"], {"thinkingBudget": 0})
        self.assertEqual(generation["responseMimeType"], "application/json")
        self.assertNotIn("tools", sent["body"])
        self.assertEqual(sent["timeout"], 45)
        self.assertEqual(sent["headers"]["Authorization"], "Bearer test-token-do-not-log")
        self.assertEqual(result["output"], {"ready": True})
        self.assertEqual(result["usage"]["total_tokens"], 15)
        self.assertEqual(result["model_version"], "gemini-example-version")
        self.assertEqual(result["finish_reason"], "STOP")
        self.assertEqual(result["request_id"], "response-123")
        self.assertEqual(result["response_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(result["attempts"], 1)
        self.assertGreaterEqual(result["latency_seconds"], 0)
        self.assertNotIn("test-token-do-not-log", json.dumps(result))
        request = self.builder.return_value.open.call_args.args[0]
        self.assertFalse(request.has_header("Authorization"))
        self.assertIsInstance(self.builder.call_args.args[0], _NoRedirect)

    def test_optional_thinking_level_and_header_request_id(self):
        document = gemini()
        del document["responseId"]
        self.respond(document, headers={"x-request-id": "header-123"})
        result = VertexClient(config(thinking_level="LOW")).complete_json("JSON please", {})
        self.assertEqual(self.sent[0]["body"]["generationConfig"]["thinkingConfig"], {"thinkingLevel": "LOW"})
        self.assertEqual(result["request_id"], "header-123")

    def test_openai_existing_endpoint_and_json_response(self):
        self.respond({"id": "completion-123", "model": "model-version", "choices": [
            {"message": {"role": "assistant", "content": '{"ready":true}'}, "finish_reason": "stop"}
        ], "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19}})
        result = VertexClient(config(api="openai", model="google/gemini-example")).complete_json("JSON please", {})
        self.assertEqual(result["output"], {"ready": True})
        self.assertEqual(result["usage"], {"input_tokens": 12, "output_tokens": 7, "total_tokens": 19})
        self.assertEqual(self.sent[0]["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(self.sent[0]["body"]["max_tokens"], 1024)
        self.assertFalse(self.sent[0]["body"]["stream"])

    def test_invalid_input_never_fetches_credentials_or_opens_network(self):
        cases = [("", {}), ("hello", []), ("x" * (MAX_REQUEST_BYTES + 1), {}),
                 ("hello", {"text": "x" * MAX_REQUEST_BYTES}), ("hello", {"value": float("nan")}),
                 ("hello", {"text": "😀" * (MAX_REQUEST_BYTES // 4)})]
        for system, payload in cases:
            with self.subTest(system=system[:20]), self.assertRaises(ValueError):
                self.client.complete_json(system, payload)
        self.auth.assert_not_called()
        self.builder.assert_not_called()

    def test_no_transport_retries_or_sensitive_error_echoes(self):
        errors = [HTTPError(self.client.config.endpoint, 429, "private response", {}, None),
                  HTTPError(self.client.config.endpoint, 403, "test-token-do-not-log", {}, None),
                  URLError("private URL"), TimeoutError("private request")]
        for error in errors:
            self.builder.return_value.open.reset_mock()
            self.builder.return_value.open.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(LLMError) as caught:
                self.client.complete_json("JSON please", {})
            self.assertIn("no retry", str(caught.exception))
            self.assertNotIn("private", str(caught.exception))
            self.assertNotIn("test-token", str(caught.exception))
            self.builder.return_value.open.assert_called_once()

    def test_redirects_non_success_and_oversized_responses_fail(self):
        self.assertIsNone(_NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))
        cases = [(gemini(), {"url": "https://other.example/"}), (gemini(), {"status": 201}),
                 (b"x" * (MAX_RESPONSE_BYTES + 1), {})]
        for response, overrides in cases:
            self.respond(response, **overrides)
            with self.subTest(overrides=overrides), self.assertRaises(LLMError):
                self.client.complete_json("JSON please", {})

    def test_blocks_refusals_truncation_missing_and_non_text_candidates(self):
        documents = [{"promptFeedback": {"blockReason": "SAFETY"}}, {}, {"error": {"message": "private"}},
                     {"candidates": []}, {"candidates": [None]}, {"candidates": [{}, {}]}]
        for reason in ["MAX_TOKENS", "SAFETY", "RECITATION", "OTHER", None]:
            document = gemini()
            document["candidates"][0]["finishReason"] = reason
            documents.append(document)
        for parts in [[], [{"functionCall": {"name": "execute"}}], [{"text": "{}", "functionCall": {}}],
                      [{"text": "hidden", "thought": True}]]:
            document = gemini()
            document["candidates"][0]["content"]["parts"] = parts
            documents.append(document)
        blocked = gemini()
        blocked["candidates"][0]["safetyRatings"] = [{"blocked": True}]
        documents.append(blocked)
        for document in documents:
            self.respond(document)
            with self.subTest(document=document), self.assertRaises(LLMError):
                self.client.complete_json("JSON please", {})

    def test_invalid_json_duplicate_keys_arrays_and_nan_fail(self):
        for content in ["I cannot do that", "```json\n{}\n```", "[]", "null", '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e999}', '{"x":']:
            self.respond(gemini(content))
            with self.subTest(content=content), self.assertRaises(LLMError):
                self.client.complete_json("JSON please", {})
        for raw in [b"not json", b'[]', b'{"a":1,"a":2}', b'\xff']:
            self.respond(raw)
            with self.subTest(raw=raw), self.assertRaises(LLMError):
                self.client.complete_json("JSON please", {})

    def test_only_final_text_is_returned_and_missing_usage_stays_unknown(self):
        document = gemini()
        document.pop("usageMetadata")
        document["candidates"][0]["content"]["parts"].insert(0, {"text": "private thought", "thought": True})
        self.respond(document)
        result = self.client.complete_json("JSON please", {})
        self.assertEqual(result["output"], {"ready": True})
        self.assertIsNone(result["usage"]["total_tokens"])
        self.assertNotIn("private thought", json.dumps(result))

    def test_invalid_audit_metadata_fails(self):
        documents = []
        for value in [-1, True, "12"]:
            document = gemini()
            document["usageMetadata"]["totalTokenCount"] = value
            documents.append(document)
        for key, value in [("responseId", "bad\nheader"), ("modelVersion", {}), ("usageMetadata", [])]:
            document = gemini()
            document[key] = value
            documents.append(document)
        for document in documents:
            self.respond(document)
            with self.subTest(document=document), self.assertRaises(LLMError):
                self.client.complete_json("JSON please", {})

    def test_openai_refusal_tools_and_truncation_fail(self):
        base = {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
        documents = [{"choices": []}]
        for key, value in [("refusal", "no"), ("tool_calls", [{}]), ("function_call", {"name": "execute"}), ("content", None)]:
            document = copy.deepcopy(base)
            document["choices"][0]["message"][key] = value
            documents.append(document)
        for reason in ["length", "content_filter", "tool_calls", None]:
            document = copy.deepcopy(base)
            document["choices"][0]["finish_reason"] = reason
            documents.append(document)
        for document in documents:
            self.respond(document)
            with self.subTest(document=document), self.assertRaises(LLMError):
                VertexClient(config(api="openai")).complete_json("JSON please", {})


class CredentialTests(unittest.TestCase):
    @patch("sewall.llm.subprocess.run")
    def test_adc_refresh_uses_bounded_transport_and_sanitizes_errors(self, run):
        google = ModuleType("google")
        auth = ModuleType("google.auth")
        transport = ModuleType("google.auth.transport")
        requests = ModuleType("google.auth.transport.requests")
        google.auth = auth
        auth.transport = transport
        transport.requests = requests
        network = Mock()
        requests.Request = Mock(return_value=network)
        credentials = Mock(valid=False, token="adc-test-token")
        credentials.refresh.side_effect = lambda request: request("https://oauth2.googleapis.com/token")
        auth.default = Mock(return_value=(credentials, "implicit-project-ignored"))
        modules = {module.__name__: module for module in [google, auth, transport, requests]}
        with patch.dict("sys.modules", modules):
            self.assertEqual(_access_token(config()), "adc-test-token")
            self.assertEqual(network.call_args.kwargs["timeout"], 30)
            self.assertEqual(auth.default.call_args.kwargs["scopes"], ["https://www.googleapis.com/auth/cloud-platform"])
            auth.default.side_effect = RuntimeError("secret-value")
            with self.assertRaises(LLMError) as caught:
                _access_token(config())
            self.assertNotIn("secret-value", str(caught.exception))
        run.assert_not_called()

    @patch("sewall.llm.subprocess.run")
    def test_gcloud_uses_no_shell_and_never_changes_configuration(self, run):
        run.return_value = Mock(stdout="test-token\n")
        self.assertEqual(_access_token(config(auth_method="gcloud")), "test-token")
        self.assertEqual(run.call_args.args[0], ["gcloud", "auth", "print-access-token", "--quiet"])
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        self.assertTrue(run.call_args.kwargs["capture_output"])
        self.assertNotIn("shell", run.call_args.kwargs)

    @patch("sewall.llm.subprocess.run")
    def test_auth_failures_and_bad_tokens_are_sanitized(self, run):
        run.side_effect = subprocess.CalledProcessError(1, "gcloud", stderr="secret-value")
        with self.assertRaises(LLMError) as caught:
            _access_token(config(auth_method="gcloud"))
        self.assertNotIn("secret-value", str(caught.exception))
        run.side_effect = None
        for token in ["", "token\nHeader: injection", "x" * 16_385]:
            run.return_value = Mock(stdout=token)
            with self.subTest(token=token[:30]), self.assertRaises(LLMError):
                _access_token(config(auth_method="gcloud"))


if __name__ == "__main__":
    unittest.main()

"""Bounded JSON inference through the Anthropic and OpenAI APIs, plus a config dispatcher.

API keys come from environment variables named in the configuration and are
read only at request time. Keys never enter a configuration, an audit record or
an error message. Each call makes one HTTPS request with redirects refused and
no retry. OpenAI-compatible servers (vLLM, Ollama) are reachable through
base_url; plain HTTP is accepted only on the loopback interface. A configuration
with base_url sends a key only from a variable it names in api_key_env, so the
default OPENAI_API_KEY never reaches another server. Loopback requests bypass
any configured proxy.

REST contracts consulted 2026-10-05 from provider documentation:
https://docs.anthropic.com/en/api/messages
https://platform.openai.com/docs/api-reference/chat/create
Model names are syntactically validated, not asserted available.
"""

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from . import llm
from .llm import LLMError, MAX_REQUEST_BYTES, MAX_RESPONSE_BYTES, ModelConfig, _NoRedirect, _parse_object


PROVIDERS = {
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "path": "/messages",
                  "api_key_env": "ANTHROPIC_API_KEY", "label": "Anthropic"},
    "openai": {"base_url": "https://api.openai.com/v1", "path": "/chat/completions",
               "api_key_env": "OPENAI_API_KEY", "label": "OpenAI"},
}
ANTHROPIC_VERSION = "2023-06-01"
MAX_PROVIDER_OUTPUT_TOKENS = 8192
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,199}\Z", re.ASCII)
_ENV = re.compile(r"[A-Z][A-Z0-9_]{0,63}\Z", re.ASCII)
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_FENCE = re.compile(r"\A```(?:json)?\s*\n(.*)\n```\s*\Z", re.DOTALL)


@dataclass(frozen=True)
class ProviderConfig:
    """Explicit provider and model selection; holds the name of a key variable, never a key."""

    provider: str
    model: str
    max_output_tokens: int = 1024
    timeout_seconds: int = 45
    retries: int = 0
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None

    def __post_init__(self):
        if self.provider not in PROVIDERS:
            raise ValueError("provider must be anthropic or openai")
        if (not isinstance(self.model, str) or not _MODEL.fullmatch(self.model)
                or ".." in self.model or "//" in self.model or self.model.endswith("/")):
            raise ValueError("invalid model identifier")
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= MAX_PROVIDER_OUTPUT_TOKENS:
            raise ValueError(f"max_output_tokens must be an integer from 1 to {MAX_PROVIDER_OUTPUT_TOKENS}")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 120:
            raise ValueError("timeout_seconds must be an integer from 1 to 120")
        if type(self.retries) is not int or self.retries != 0:
            raise ValueError("retries must be 0; ambiguous inference must not be repeated")
        if self.api_key_env is not None and (not isinstance(self.api_key_env, str) or not _ENV.fullmatch(self.api_key_env)):
            raise ValueError("api_key_env must name an environment variable, not hold a key")
        if self.temperature is not None and (type(self.temperature) not in (int, float) or not 0 <= self.temperature <= 1):
            raise ValueError("temperature must be between 0 and 1")
        if self.reasoning_effort is not None and (
                self.provider != "openai" or self.reasoning_effort not in ("minimal", "low", "medium", "high")):
            raise ValueError("reasoning_effort is minimal, low, medium or high and requires provider openai")
        if self.base_url is not None:
            if self.provider != "openai":
                raise ValueError("base_url is only configurable for OpenAI-compatible servers")
            _check_base_url(self.base_url)

    @classmethod
    def from_dict(cls, value: dict) -> "ProviderConfig":
        if not isinstance(value, dict):
            raise ValueError("model configuration must be an object")
        if set(value) - {field.name for field in fields(cls)}:
            raise ValueError("model configuration contains unknown fields")
        if not {"provider", "model"} <= set(value):
            raise ValueError("model configuration requires provider and model")
        return cls(**value)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def key_variable(self) -> str | None:
        # A custom endpoint never inherits the provider's default key variable.
        if self.api_key_env or self.base_url:
            return self.api_key_env
        return PROVIDERS[self.provider]["api_key_env"]

    @property
    def endpoint(self) -> str:
        spec = PROVIDERS[self.provider]
        return (self.base_url or spec["base_url"]).rstrip("/") + spec["path"]

    @property
    def local(self) -> bool:
        return urlsplit(self.endpoint).hostname in _LOOPBACK


def _check_base_url(value):
    if not isinstance(value, str) or len(value) > 300 or any(c.isspace() or ord(c) < 33 for c in value):
        raise ValueError("invalid base_url")
    parts = urlsplit(value)
    if (parts.scheme not in ("https", "http") or not parts.hostname or parts.username or parts.password
            or parts.query or parts.fragment or "@" in parts.netloc):
        raise ValueError("base_url must be an http(s) URL without credentials, query or fragment")
    if parts.scheme == "http" and parts.hostname not in _LOOPBACK:
        raise ValueError("plain http base_url is allowed only on the loopback interface")
    try:
        parts.port
    except ValueError:
        raise ValueError("invalid base_url port") from None


def _api_key(config: ProviderConfig) -> str | None:
    if config.key_variable is None:
        return None
    key = os.environ.get(config.key_variable)
    if not key:
        if config.local:
            return None
        raise LLMError(f"{PROVIDERS[config.provider]['label']} API key is not set; export {config.key_variable}")
    if len(key) > 1024 or any(c.isspace() or ord(c) < 33 or ord(c) > 126 for c in key):
        raise LLMError(f"{config.key_variable} does not contain a usable API key")
    return key


def _text(value, label, name):
    if value is not None and (not isinstance(value, str) or len(value) > 256
                              or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise LLMError(f"{label} returned invalid {name}")
    return value


def _counts(raw, keys, label):
    if not isinstance(raw, dict):
        raise LLMError(f"{label} returned invalid usage metadata")
    result = {}
    for normalized, original in keys.items():
        value = raw.get(original)
        if value is not None and (type(value) is not int or not 0 <= value <= 100_000_000):
            raise LLMError(f"{label} returned invalid token counts")
        result[normalized] = value
    if result.get("total_tokens") is None and None not in (result["input_tokens"], result["output_tokens"]):
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def _model_json(text, label):
    """Parse the model's JSON object, tolerating one surrounding Markdown fence."""
    match = _FENCE.match(text.strip())
    return _parse_object(match.group(1) if match else text, f"{label} model output")


class _ProviderClient:
    """One explicit, bounded JSON completion per call; no model tool execution."""

    provider = None

    def __init__(self, config: ProviderConfig):
        if not isinstance(config, ProviderConfig) or config.provider != self.provider:
            raise TypeError(f"config must be a ProviderConfig for {self.provider}")
        self.config = config
        self.label = PROVIDERS[self.provider]["label"]

    def complete_json(self, system_prompt: str, user_payload: dict) -> dict:
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt must be nonempty text")
        if not isinstance(user_payload, dict):
            raise ValueError("user_payload must be a JSON object")
        if len(system_prompt) > MAX_REQUEST_BYTES:
            raise ValueError("model request exceeds the byte limit")
        try:
            payload = json.dumps(user_payload, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
            instruction = system_prompt + "\nReturn one JSON object only, without Markdown fences."
            body = self._body(instruction, payload)
            request_bytes = json.dumps(body, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
            raise ValueError("model request must contain serializable finite JSON data") from None
        if len(request_bytes) > MAX_REQUEST_BYTES:
            raise ValueError("model request exceeds the byte limit")

        started = time.monotonic()
        endpoint = self.config.endpoint
        request = Request(endpoint, data=request_bytes, method="POST",
                          headers={"Content-Type": "application/json", "Accept": "application/json",
                                   "User-Agent": "SewallDiscoverPrototype/0.2"})
        key = _api_key(self.config)
        if key is not None:
            for name, value in self._auth(key).items():
                request.add_header(name, value)
        del key
        try:
            # A proxy would see a plain-HTTP loopback request, key included, in cleartext.
            handlers = [_NoRedirect(), ProxyHandler({})] if self.config.local else [_NoRedirect()]
            with build_opener(*handlers).open(request, timeout=self.config.timeout_seconds) as response:
                if response.geturl() != endpoint:
                    raise LLMError(f"{self.label} response changed the requested URL")
                if response.getcode() != 200:
                    raise LLMError(f"{self.label} returned a non-success status")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                header_request_id = response.headers.get("request-id") or response.headers.get("x-request-id")
        except HTTPError as exc:
            raise LLMError(f"{self.label} returned HTTP {exc.code}; no retry attempted") from None
        except (URLError, OSError, TimeoutError) as exc:
            raise LLMError(f"{self.label} request failed ({type(exc).__name__}); no retry attempted") from None
        finally:
            # No key-bearing Request is retained in the client or the audit record.
            for name in ("X-api-key", "Authorization"):
                request.remove_header(name)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LLMError(f"{self.label} response exceeded the byte limit")
        document = _parse_object(raw, f"{self.label} response")
        if "error" in document and document["error"]:
            raise LLMError(f"{self.label} returned an error response")
        output, finish_reason, usage = self._result(document)
        return {
            "output": output,
            "provider": self.provider,
            "api": self.api,
            "model": self.config.model,
            "model_version": _text(document.get("model"), self.label, "model version"),
            "finish_reason": finish_reason,
            "usage": usage,
            "request_id": _text(document.get("id") or header_request_id, self.label, "request ID"),
            "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
            "request_bytes": len(request_bytes),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "response_bytes": len(raw),
            "latency_seconds": round(time.monotonic() - started, 6),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "attempts": 1,
            "endpoint": endpoint,
            "max_output_tokens": self.config.max_output_tokens,
        }


class AnthropicClient(_ProviderClient):
    """Claude through the Anthropic Messages API."""

    provider = "anthropic"
    api = "messages"

    def _auth(self, key):
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}

    def _body(self, instruction, payload):
        body = {"model": self.config.model, "max_tokens": self.config.max_output_tokens,
                "system": instruction, "messages": [{"role": "user", "content": payload}], "stream": False}
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        return body

    def _result(self, document):
        if document.get("type") != "message" or document.get("role") != "assistant":
            raise LLMError("Anthropic did not return an assistant message")
        reason = document.get("stop_reason")
        if reason != "end_turn":
            raise LLMError("Anthropic completion was refused, truncated, or unfinished")
        content = document.get("content")
        if not isinstance(content, list) or not content:
            raise LLMError("Anthropic completion contains no text")
        texts = []
        for block in content:
            if not isinstance(block, dict):
                raise LLMError("Anthropic returned an unsupported content block")
            if block.get("type") in ("thinking", "redacted_thinking"):
                continue
            if block.get("type") != "text" or not isinstance(block.get("text"), str):
                raise LLMError("Anthropic returned an unsupported non-text block or tool call")
            texts.append(block["text"])
        if not texts:
            raise LLMError("Anthropic completion contains no text")
        usage = _counts(document.get("usage", {}),
                        {"input_tokens": "input_tokens", "output_tokens": "output_tokens", "total_tokens": "total_tokens"},
                        "Anthropic")
        return _model_json("".join(texts), "Anthropic"), reason, usage


class OpenAIClient(_ProviderClient):
    """OpenAI Chat Completions, or an OpenAI-compatible server named by base_url."""

    provider = "openai"
    api = "chat_completions"

    def _auth(self, key):
        return {"Authorization": f"Bearer {key}"}

    def _body(self, instruction, payload):
        body = {"model": self.config.model,
                "messages": [{"role": "system", "content": instruction}, {"role": "user", "content": payload}],
                "stream": False, "n": 1, "response_format": {"type": "json_object"}}
        # OpenAI's own API takes max_completion_tokens; compatible servers widely accept max_tokens.
        body["max_tokens" if self.config.base_url else "max_completion_tokens"] = self.config.max_output_tokens
        if self.config.temperature is not None:
            body["temperature"] = self.config.temperature
        if self.config.reasoning_effort is not None:
            body["reasoning_effort"] = self.config.reasoning_effort
        return body

    def _result(self, document):
        choices = document.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise LLMError("OpenAI did not return exactly one choice")
        choice = choices[0]
        if choice.get("finish_reason") != "stop":
            raise LLMError("OpenAI completion was blocked, refused, truncated, or unfinished")
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("refusal") or message.get("tool_calls") or message.get("function_call"):
            raise LLMError("OpenAI refused the prompt or returned an unsupported tool call")
        if not isinstance(message.get("content"), str):
            raise LLMError("OpenAI completion contains no text")
        usage = _counts(document.get("usage", {}),
                        {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens", "total_tokens": "total_tokens"},
                        "OpenAI")
        return _model_json(message["content"], "OpenAI"), "stop", usage


CLIENTS = {"anthropic": AnthropicClient, "openai": OpenAIClient}


# Fields that choose where a request goes or which secret it carries.
ENDPOINT_FIELDS = ("base_url", "api_key_env")


def client_from_config(value: dict, allow_endpoint_fields: bool = True):
    """Build a model client: a provider field selects Anthropic or OpenAI; otherwise Vertex AI.

    Callers that receive configurations from an untrusted party (the MCP server) pass
    allow_endpoint_fields=False, so the party cannot name the destination or the key variable.
    """
    if not isinstance(value, dict):
        raise ValueError("model configuration must be an object")
    if not allow_endpoint_fields and any(field in value for field in ENDPOINT_FIELDS):
        raise ValueError("base_url and api_key_env are not accepted here; use the provider's default endpoint")
    provider = value.get("provider")
    # Looked up at call time so tests can substitute the Vertex client.
    if provider is None:
        return llm.VertexClient(ModelConfig.from_dict(value))
    if provider == "vertex":
        return llm.VertexClient(ModelConfig.from_dict({k: v for k, v in value.items() if k != "provider"}))
    if provider not in CLIENTS:
        raise ValueError("provider must be anthropic, openai or vertex")
    return CLIENTS[provider](ProviderConfig.from_dict(value))


def recorded_config(client):
    """Return the client's configuration for a manifest, or None for an unrecognized client."""
    config = getattr(client, "config", None)
    return config.to_dict() if isinstance(config, (ModelConfig, ProviderConfig)) else None

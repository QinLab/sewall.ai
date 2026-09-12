"""Bounded, auditable Vertex AI JSON inference with existing credentials only.

The caller supplies every prompt byte. This module does not read project files,
follow prompt URLs, execute model tools, provision endpoints, or retry inference.
Run live calls on an allocated Slurm CPU node, not an HPC login node.

REST contracts (Google documentation, consulted 2026-09-10):
https://cloud.google.com/vertex-ai/generative-ai/docs/model-reference/inference
https://cloud.google.com/vertex-ai/generative-ai/docs/reference/rest/v1/GenerateContentResponse
https://cloud.google.com/vertex-ai/generative-ai/docs/migrate/openai/auth-and-credentials
"""

from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener


MAX_REQUEST_BYTES = 40_960
MAX_RESPONSE_BYTES = 1_048_576
MAX_OUTPUT_TOKENS = 2048
_PROJECT = re.compile(r"(?:[a-z][a-z0-9-]{4,28}[a-z0-9]|[0-9]{6,20})\Z")
_LOCATION = re.compile(r"(?:global|[a-z]+(?:-[a-z]+)*[0-9])\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,199}\Z")
_OPENAI_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@/-]{0,199}\Z")


class LLMError(RuntimeError):
    """A model request failed; no successful or synthetic result is implied."""


@dataclass(frozen=True)
class ModelConfig:
    """Explicit deployment selection; config contains no credentials.

    Model names and regions are syntactically validated, not asserted available.
    The caller must verify access with a bounded live probe. OpenAI compatibility
    varies by model; unsupported JSON configuration fails without fallback.
    """

    project: str
    location: str
    model: str
    api: str = "gemini"
    max_output_tokens: int = 1024
    timeout_seconds: int = 45
    retries: int = 0
    auth_method: str = "adc"
    endpoint_id: str = "openapi"
    thinking_budget: int | None = None
    thinking_level: str | None = None
    api_version: str = "v1"

    def __post_init__(self):
        for name, value, pattern in (
            ("project", self.project, _PROJECT),
            ("location", self.location, _LOCATION),
        ):
            if not isinstance(value, str) or not pattern.fullmatch(value):
                raise ValueError(f"invalid {name}")
        if self.api not in ("gemini", "openai"):
            raise ValueError("api must be gemini or openai")
        if self.api_version not in ("v1", "v1beta1"):
            raise ValueError("api_version must be v1 or v1beta1")
        model_pattern = _MODEL if self.api == "gemini" else _OPENAI_MODEL
        if (
            not isinstance(self.model, str)
            or not model_pattern.fullmatch(self.model)
            or ".." in self.model
            or "//" in self.model
            or self.model.endswith("/")
        ):
            raise ValueError("invalid model identifier")
        if type(self.max_output_tokens) is not int or not 1 <= self.max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise ValueError("max_output_tokens must be an integer from 1 to 2048")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 60:
            raise ValueError("timeout_seconds must be an integer from 1 to 60")
        if type(self.retries) is not int or self.retries != 0:
            raise ValueError("retries must be 0; ambiguous inference must not be repeated")
        if self.auth_method not in ("adc", "gcloud"):
            raise ValueError("auth_method must be adc or gcloud")
        if not isinstance(self.endpoint_id, str) or not re.fullmatch(r"openapi|[0-9]{1,30}", self.endpoint_id):
            raise ValueError("endpoint_id must be openapi or an existing numeric endpoint ID")
        if self.api == "gemini" and self.endpoint_id != "openapi":
            raise ValueError("endpoint_id is only configurable for api=openai")
        if self.thinking_budget is not None and (
            type(self.thinking_budget) is not int
            or not 0 <= self.thinking_budget <= self.max_output_tokens
        ):
            raise ValueError("thinking_budget must be between 0 and max_output_tokens")
        if self.thinking_level is not None and self.thinking_level not in ("MINIMAL", "LOW", "MEDIUM", "HIGH"):
            raise ValueError("invalid thinking_level")
        if self.thinking_budget is not None and self.thinking_level is not None:
            raise ValueError("set only one of thinking_budget and thinking_level")
        if self.api == "openai" and (self.thinking_budget is not None or self.thinking_level is not None):
            raise ValueError("thinking controls currently require api=gemini")

    @classmethod
    def from_dict(cls, value: dict) -> "ModelConfig":
        if not isinstance(value, dict):
            raise ValueError("model configuration must be an object")
        expected = {field.name for field in fields(cls)}
        if set(value) - expected:
            raise ValueError("model configuration contains unknown fields")
        if not {"project", "location", "model"} <= set(value):
            raise ValueError("model configuration requires project, location, and model")
        return cls(**value)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def endpoint(self) -> str:
        host = "aiplatform.googleapis.com" if self.location == "global" else f"{self.location}-aiplatform.googleapis.com"
        base = f"https://{host}/{self.api_version}/projects/{self.project}/locations/{self.location}"
        if self.api == "gemini":
            return f"{base}/publishers/google/models/{self.model}:generateContent"
        return f"{base}/endpoints/{self.endpoint_id}/chat/completions"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _access_token(config: ModelConfig) -> str:
    """Get an ephemeral token; errors never contain credential/provider output."""
    try:
        if config.auth_method == "gcloud":
            result = subprocess.run(
                ["gcloud", "auth", "print-access-token", "--quiet"],
                check=True,
                capture_output=True,
                text=True,
                timeout=min(config.timeout_seconds, 30),
            )
            token = result.stdout.strip()
        else:
            import google.auth
            from google.auth.transport.requests import Request as AuthRequest

            transport = AuthRequest()

            def bounded_request(*args, **kwargs):
                kwargs["timeout"] = min(config.timeout_seconds, 30)
                return transport(*args, **kwargs)

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            if not credentials.valid:
                credentials.refresh(bounded_request)
            token = credentials.token
        if (
            not isinstance(token, str)
            or not 1 <= len(token) <= 16_384
            or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in token)
        ):
            raise ValueError("invalid access token")
        return token
    except Exception:
        raise LLMError(
            f"Vertex authentication failed using {config.auth_method}; check existing credentials"
        ) from None


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("non-finite JSON number")


def _finite_float(value):
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("non-finite JSON number")
    return number


def _parse_object(raw: str | bytes, label: str) -> dict:
    try:
        value = json.loads(
            raw, object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant, parse_float=_finite_float,
        )
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise LLMError(f"{label} is not a valid JSON object") from None


def _metadata_text(value, name):
    if value is not None and (
        not isinstance(value, str) or len(value) > 256
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        raise LLMError(f"Vertex returned invalid {name}")
    return value


def _usage(raw: dict, api: str) -> dict:
    if not isinstance(raw, dict):
        raise LLMError("Vertex returned invalid usage metadata")
    keys = (
        {"input_tokens": "promptTokenCount", "output_tokens": "candidatesTokenCount",
         "total_tokens": "totalTokenCount", "thinking_tokens": "thoughtsTokenCount"}
        if api == "gemini" else
        {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens", "total_tokens": "total_tokens"}
    )
    result = {}
    for normalized, original in keys.items():
        value = raw.get(original)
        if value is not None and (type(value) is not int or not 0 <= value <= 100_000_000):
            raise LLMError("Vertex returned invalid token counts")
        result[normalized] = value
    return result


class VertexClient:
    """One explicit, bounded JSON completion per call; no model tool execution."""

    def __init__(self, config: ModelConfig):
        if not isinstance(config, ModelConfig):
            raise TypeError("config must be a ModelConfig")
        self.config = config

    def complete_json(self, system_prompt: str, user_payload: dict) -> dict:
        if not isinstance(system_prompt, str) or not system_prompt.strip():
            raise ValueError("system_prompt must be nonempty text")
        if not isinstance(user_payload, dict):
            raise ValueError("user_payload must be a JSON object")
        # Check text size before creating transport or looking up credentials.
        if len(system_prompt) > MAX_REQUEST_BYTES:
            raise ValueError("model request exceeds the byte limit")
        try:
            payload = json.dumps(user_payload, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
            instruction = system_prompt + "\nReturn one JSON object only, without Markdown fences."
            if self.config.api == "gemini":
                generation = {
                    "maxOutputTokens": self.config.max_output_tokens,
                    "candidateCount": 1,
                    "temperature": 0,
                    "responseMimeType": "application/json",
                }
                if self.config.thinking_budget is not None:
                    generation["thinkingConfig"] = {"thinkingBudget": self.config.thinking_budget}
                if self.config.thinking_level is not None:
                    generation["thinkingConfig"] = {"thinkingLevel": self.config.thinking_level}
                body = {
                    "systemInstruction": {"parts": [{"text": instruction}]},
                    "contents": [{"role": "user", "parts": [{"text": payload}]}],
                    "generationConfig": generation,
                }
            else:
                body = {
                    "model": self.config.model,
                    "messages": [{"role": "system", "content": instruction},
                                 {"role": "user", "content": payload}],
                    "max_tokens": self.config.max_output_tokens,
                    "temperature": 0,
                    "stream": False,
                    "response_format": {"type": "json_object"},
                }
            request_bytes = json.dumps(body, allow_nan=False, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
            raise ValueError("model request must contain serializable finite JSON data") from None
        if len(request_bytes) > MAX_REQUEST_BYTES:
            raise ValueError("model request exceeds the byte limit")

        started = time.monotonic()
        token = _access_token(self.config)
        request = Request(
            self.config.endpoint,
            data=request_bytes,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                     "Accept": "application/json", "User-Agent": "SewallDiscoverPrototype/0.2"},
            method="POST",
        )
        del token
        try:
            opener = build_opener(_NoRedirect())
            with opener.open(request, timeout=self.config.timeout_seconds) as response:
                if response.geturl() != self.config.endpoint:
                    raise LLMError("Vertex response changed the requested URL")
                if response.getcode() != 200:
                    raise LLMError("Vertex returned a non-success status")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                header_request_id = response.headers.get("x-request-id") or response.headers.get("x-goog-request-id")
        except HTTPError as exc:
            raise LLMError(f"Vertex returned HTTP {exc.code}; no retry attempted") from None
        except (URLError, OSError, TimeoutError) as exc:
            raise LLMError(f"Vertex request failed ({type(exc).__name__}); no retry attempted") from None
        finally:
            # No auth-bearing Request is retained in the client or audit record.
            request.remove_header("Authorization")
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LLMError("Vertex response exceeded the byte limit")
        document = _parse_object(raw, "Vertex response")
        if "error" in document:
            raise LLMError("Vertex returned an error response")
        if self.config.api == "gemini":
            output, finish_reason, usage = self._gemini_result(document)
            version = document.get("modelVersion")
            request_id = document.get("responseId") or header_request_id
        else:
            output, finish_reason, usage = self._openai_result(document)
            version = document.get("model")
            request_id = document.get("id") or header_request_id
        return {
            "output": output,
            "provider": "vertex-ai",
            "api": self.config.api,
            "model": self.config.model,
            "model_version": _metadata_text(version, "model version"),
            "finish_reason": finish_reason,
            "usage": usage,
            "request_id": _metadata_text(request_id, "request ID"),
            "request_sha256": hashlib.sha256(request_bytes).hexdigest(),
            "request_bytes": len(request_bytes),
            "response_sha256": hashlib.sha256(raw).hexdigest(),
            "response_bytes": len(raw),
            "latency_seconds": round(time.monotonic() - started, 6),
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "attempts": 1,
            "endpoint": self.config.endpoint,
            "max_output_tokens": self.config.max_output_tokens,
        }

    @staticmethod
    def _gemini_result(document):
        feedback = document.get("promptFeedback", {})
        if not isinstance(feedback, dict) or feedback.get("blockReason"):
            raise LLMError("Vertex blocked or refused the prompt")
        candidates = document.get("candidates")
        if not isinstance(candidates, list) or len(candidates) != 1 or not isinstance(candidates[0], dict):
            raise LLMError("Vertex did not return exactly one candidate")
        candidate = candidates[0]
        reason = candidate.get("finishReason")
        if reason != "STOP":
            raise LLMError("Vertex completion was blocked, refused, truncated, or unfinished")
        ratings = candidate.get("safetyRatings", [])
        if not isinstance(ratings, list) or any(not isinstance(rating, dict) or rating.get("blocked") for rating in ratings):
            raise LLMError("Vertex blocked the completion")
        content = candidate.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list) or not parts:
            raise LLMError("Vertex completion contains no text")
        texts = []
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                raise LLMError("Vertex returned an unsupported non-text completion")
            if set(part) - {"text", "thought", "thoughtSignature"}:
                raise LLMError("Vertex returned an unsupported completion part")
            if part.get("thought") is True:
                continue
            texts.append(part["text"])
        return _parse_object("".join(texts), "Model output"), reason, _usage(document.get("usageMetadata", {}), "gemini")

    @staticmethod
    def _openai_result(document):
        choices = document.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise LLMError("Vertex did not return exactly one choice")
        choice = choices[0]
        if choice.get("finish_reason") != "stop":
            raise LLMError("Vertex completion was blocked, refused, truncated, or unfinished")
        message = choice.get("message")
        if not isinstance(message, dict) or message.get("refusal") or message.get("tool_calls") or message.get("function_call"):
            raise LLMError("Vertex refused the prompt or returned an unsupported tool call")
        text = message.get("content")
        if not isinstance(text, str):
            raise LLMError("Vertex completion contains no text")
        return _parse_object(text, "Model output"), "stop", _usage(document.get("usage", {}), "openai")

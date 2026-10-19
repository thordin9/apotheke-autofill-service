"""Optional Cloudflare Workers AI backend.

Calls the Workers AI REST API directly. There is no Worker proxy in this
process. Photos are re-encoded to a small JPEG before they leave the machine.
The API token and account id are never logged, returned, or placed in errors.
"""

from __future__ import annotations

import base64
import io
import json
import logging
from typing import Any

import httpx
from PIL import Image

from ..settings import Secret, get_settings
from .base import CAPABILITIES, Backend, BackendUnavailable
from .ollama import BackendError, build_prompt, build_schema, parse_suggestion

log = logging.getLogger(__name__)

API_HOST = "https://api.cloudflare.com"
DEFAULT_MODEL = "@cf/google/gemma-4-26b-a4b-it"
# About 1.5 MB of raw JPEG before base64. Workers AI publishes no body limit;
# reports cluster around 2.5 MB of JSON.
CF_IMAGE_MAX_BYTES = 1_500_000
CF_LONG_EDGE = 560
PAID_PLAN_CODE = 5035

# Structured output, by model id. Anything else gets the schema in the prompt
# and one repair retry. Llama vision models are absent on purpose: they are
# rejected (EU multimodal license) and refused at startup.
RESPONSE_FORMAT_MODELS = frozenset(
    {
        "@cf/google/gemma-4-26b-a4b-it",
        "@cf/qwen/qwen3.8-27b",
    }
)
# guided_json where Cloudflare documents it. Mistral Small 3.1 has no confirmed
# image input, so it is not the default; the id stays configurable.
GUIDED_JSON_MODELS = frozenset(
    {
        "@cf/mistralai/mistral-small-3.1-24b-instruct",
    }
)
REJECTED_MODEL_PREFIXES = (
    "@cf/meta/llama-3.2-11b-vision",
    "@cf/meta/llama-4-scout",
)


class _Redact(logging.Filter):
    """Drop configured credentials from any log record that contains them."""

    def __init__(self) -> None:
        super().__init__()
        self.secrets: set[str] = set()

    def add(self, value: str) -> None:
        if value and len(value) >= 8:
            self.secrets.add(value)

    def filter(self, record: logging.LogRecord) -> bool:
        if not self.secrets:
            return True
        rendered = record.getMessage()
        if any(secret in rendered for secret in self.secrets):
            record.msg = _scrub(rendered, self.secrets)
            record.args = ()
        if record.exc_text and any(secret in record.exc_text for secret in self.secrets):
            record.exc_text = _scrub(record.exc_text, self.secrets)
        return True


_REDACT = _Redact()


def _scrub(text: str, secrets: set[str]) -> str:
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


def install_redaction(*values: str) -> None:
    for value in values:
        _REDACT.add(value)
    root = logging.getLogger()
    if _REDACT not in root.filters:
        root.addFilter(_REDACT)
    # httpx logs the request URL (it contains the account id) and headers at DEBUG.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def model_rejected(model: str) -> bool:
    return any(model == prefix or model.startswith(prefix) for prefix in REJECTED_MODEL_PREFIXES)


def structured_mode(model: str) -> str | None:
    """'response_format', 'guided_json', or None (schema in the prompt)."""
    if model_rejected(model):
        return None
    if model in RESPONSE_FORMAT_MODELS or "/qwen/qwen3" in model:
        return "response_format"
    if model in GUIDED_JSON_MODELS:
        return "guided_json"
    return None


class ImageNotAccepted(BackendError):
    """The photo cannot be sent to Cloudflare. Keep it on the local backend."""

    def __init__(self) -> None:
        super().__init__("image exceeds the cloudflare upload cap")


class PaidPlanRequired(BackendUnavailable):
    """HTTP 403 with Workers AI error 5035. Not a transient failure."""

    code = "backend_unavailable"

    def __init__(self) -> None:
        super().__init__("the configured Cloudflare model requires Workers Paid")


class RateLimited(BackendUnavailable):
    code = "backend_unavailable"

    def __init__(self, retry_after: int | None) -> None:
        super().__init__("cloudflare rate limit exceeded")
        if retry_after is not None:
            self.headers = {"Retry-After": str(retry_after)}


def encode_for_cloudflare(
    data: bytes,
    *,
    max_bytes: int = CF_IMAGE_MAX_BYTES,
    long_edge: int = CF_LONG_EDGE,
) -> bytes | None:
    """JPEG whose long edge is at most `long_edge`, or None if it cannot fit."""
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        return None
    if image.mode != "RGB":
        image = image.convert("RGB")
    width, height = image.size
    edge = max(width, height)
    if edge > long_edge and edge > 0:
        scale = long_edge / edge
        image = image.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )
    for quality in (85, 75, 60, 45, 30, 20, 10):
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=quality, optimize=True)
        blob = buf.getvalue()
        if len(blob) <= max_bytes:
            return blob
    return None


def _prompt_for(context: dict[str, Any], *, repair: bool) -> str:
    prompt = build_prompt(context)
    if structured_mode(get_settings().cf_model) is not None and not repair:
        return prompt
    schema = json.dumps(build_schema(context), separators=(",", ":"), ensure_ascii=False)
    lead = ""
    if repair:
        lead = (
            "The previous answer was not valid JSON matching the schema. "
            "Return only one JSON object and no markdown.\n"
        )
    return f"{prompt}\n{lead}JSON schema: {schema}"


def _payload(prompt: str, jpeg: bytes, context: dict[str, Any]) -> dict[str, Any]:
    encoded = base64.b64encode(jpeg).decode("ascii")
    body: dict[str, Any] = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                    },
                ],
            }
        ]
    }
    schema = build_schema(context)
    mode = structured_mode(get_settings().cf_model)
    if mode == "response_format":
        body["response_format"] = {"type": "json_schema", "json_schema": schema}
    elif mode == "guided_json":
        body["guided_json"] = schema
    return body


def _error_codes(data: Any) -> set[int]:
    if not isinstance(data, dict):
        return set()
    errors = data.get("errors")
    if not isinstance(errors, list):
        return set()
    codes: set[int] = set()
    for err in errors:
        if not isinstance(err, dict):
            continue
        code = err.get("code")
        if isinstance(code, bool):
            continue
        if isinstance(code, int):
            codes.add(code)
        elif isinstance(code, str) and code.isdecimal():
            codes.add(int(code))
    return codes


def _usage_counts(data: dict[str, Any]) -> dict[str, int | float]:
    blobs: list[dict[str, Any]] = []
    usage = data.get("usage")
    if isinstance(usage, dict):
        blobs.append(usage)
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("usage"), dict):
        blobs.append(result["usage"])
    counts: dict[str, int | float] = {}

    def walk(obj: dict[str, Any], prefix: str) -> None:
        for key, value in obj.items():
            name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, bool):
                continue
            if isinstance(value, int | float):
                counts[name] = value
            elif isinstance(value, dict):
                walk(value, name)

    for blob in blobs:
        walk(blob, "")
    return counts


def _result_text(data: dict[str, Any]) -> str | None:
    result = data.get("result")
    if isinstance(result, str) and result.strip():
        return result
    if not isinstance(result, dict):
        return None
    response = result.get("response")
    if isinstance(response, str) and response.strip():
        return response
    choices = result.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            content = message["content"]
            if content.strip():
                return content
    return None


def _retry_after(value: str | None) -> int | None:
    if not value:
        return None
    try:
        seconds = int(value.strip())
    except ValueError:
        return None
    if seconds < 0:
        return None
    return seconds


class CloudflareBackend(Backend):
    id = "cloudflare"

    def __init__(self) -> None:
        settings = get_settings()
        self.account_id: Secret = settings.cf_account_id
        self.token: Secret = settings.cf_api_token
        self.model = settings.cf_model
        self.timeout_s = settings.cf_timeout_s
        # HTTP 5xx only. A Workers Paid 403 (code 5035) must not move this.
        self.transient_failures = 0
        self.plan_rejections = 0
        install_redaction(self.account_id.reveal(), self.token.reveal())

    def __repr__(self) -> str:
        return f"CloudflareBackend(model={self.model!r})"

    async def info(self) -> dict[str, Any]:
        return {
            "name": f"cloudflare-{self.model}",
            "capabilities": list(CAPABILITIES),
            "ready": True,
        }

    async def suggest(
        self,
        image_bytes: bytes,
        mime: str,
        context: dict[str, Any],
        *,
        timeout_s: float | None = None,
    ) -> Any:
        jpeg = encode_for_cloudflare(image_bytes)
        if jpeg is None:
            raise ImageNotAccepted
        mode = structured_mode(self.model)
        text = await self._complete(jpeg, context, repair=False, timeout_s=timeout_s)
        try:
            out = self._validate(text, context)
        except BackendError:
            if mode is not None:
                raise
            text = await self._complete(jpeg, context, repair=True, timeout_s=timeout_s)
            out = self._validate(text, context)
        return {**out, "model_used": self.model, "backend_used": "cloudflare"}

    def _validate(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        out, _how = parse_suggestion(text, context)
        return out

    async def _complete(
        self,
        jpeg: bytes,
        context: dict[str, Any],
        *,
        repair: bool,
        timeout_s: float | None,
    ) -> str:
        prompt = _prompt_for(context, repair=repair)
        return await self._post(_payload(prompt, jpeg, context), timeout_s=timeout_s)

    async def _post(self, payload: dict[str, Any], *, timeout_s: float | None) -> str:
        account = self.account_id.reveal()
        token = self.token.reveal()
        url = f"{API_HOST}/client/v4/accounts/{account}/ai/run/{self.model}"
        timeout = self.timeout_s if timeout_s is None else timeout_s
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.HTTPError:
            raise BackendUnavailable("cloudflare unreachable") from None
        try:
            data = response.json()
        except ValueError:
            data = None
        if response.status_code == 403 and PAID_PLAN_CODE in _error_codes(data):
            self.plan_rejections += 1
            log.warning("cloudflare model %s requires Workers Paid", self.model)
            raise PaidPlanRequired
        if isinstance(data, dict):
            counts = _usage_counts(data)
            if counts:
                rendered = " ".join(f"{key}={counts[key]}" for key in sorted(counts))
                log.debug("cloudflare usage %s", rendered)
        if response.status_code >= 500:
            self.transient_failures += 1
            raise BackendError("cloudflare returned an upstream error", status=response.status_code)
        if response.status_code == 429:
            raise RateLimited(_retry_after(response.headers.get("Retry-After")))
        if response.status_code in (401, 403):
            raise BackendError(
                "cloudflare rejected the credentials or the model license",
                status=response.status_code,
            )
        if response.status_code >= 400:
            raise BackendError("cloudflare returned an upstream error", status=response.status_code)
        if not isinstance(data, dict):
            raise BackendError("cloudflare returned an unusable response")
        text = _result_text(data)
        if text is None:
            raise BackendError("cloudflare returned an unusable response")
        return text

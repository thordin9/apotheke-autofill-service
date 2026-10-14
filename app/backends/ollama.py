"""Ollama backend: asks a local vision model (Moondream by default) for a
Suggestion as strict JSON, then validates and clamps the answer.

Env (read when the backend is created):
  OLLAMA_URL    default http://ollama:11434
  OLLAMA_MODEL  default moondream (qwen2-vl-2b style tags also work)
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

import httpx

from ..settings import get_settings
from .base import CAPABILITIES, Backend, BackendUnavailable

DEFAULT_URL = "http://ollama:11434"
DEFAULT_MODEL = "moondream"

MAX_TAGS = 10
MAX_NAME = 120
MAX_CATEGORY = 80
MAX_TAG = 40
MAX_DESCRIPTION = 500
MAX_CONTEXT_ITEMS = 50


class BackendError(BackendUnavailable):
    """Upstream answered but the answer was unusable (maps to backend_error)."""

    code = "backend_error"


def build_prompt(context: dict[str, Any]) -> str:
    cats = [c for c in context.get("categories", []) if c.strip()][:MAX_CONTEXT_ITEMS]
    tags = [t for t in context.get("tags", []) if t.strip()][:MAX_CONTEXT_ITEMS]
    locale = context.get("locale") or "en"
    lines = [
        "You catalogue household inventory items from a single photo.",
        "Reply with ONLY one JSON object, no prose, no markdown, of this exact shape:",
        '{"name":{"value":str,"confidence":0..1},"category":{"value":str,"confidence":0..1},'
        '"tags":[{"value":str,"confidence":0..1}],"description":{"value":str,"confidence":0..1}}',
        "Omit any field you are unsure about. name: short product name (max 6 words).",
        f"description: one short sentence. Use at most {MAX_TAGS} tags, lowercase, 1-2 words.",
        f"Write all values in the language for locale '{locale}'.",
    ]
    if cats:
        lines.append(
            "Existing categories (use one of these with EXACT spelling if it fits): "
            + json.dumps(cats, ensure_ascii=False)
        )
    if tags:
        lines.append(
            "Existing tags (prefer these with EXACT spelling over new ones): "
            + json.dumps(tags, ensure_ascii=False)
        )
    return "\n".join(lines)


def extract_json(text: str) -> dict[str, Any]:
    """Find the first JSON object in model output (tolerates fences/prose)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    raise BackendError("model output was not a JSON object")


def _clip(s: str, limit: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _scored(v: Any, limit: int, prefer: list[str] | None = None) -> dict[str, Any] | None:
    if isinstance(v, str):
        v = {"value": v}
    if not isinstance(v, dict) or not isinstance(v.get("value"), str):
        return None
    value = _clip(v["value"], limit)
    if not value:
        return None
    if prefer:
        exact = {p.casefold(): p for p in prefer}
        value = exact.get(value.casefold(), value)
    out: dict[str, Any] = {"value": value}
    c = v.get("confidence")
    if isinstance(c, int | float) and not isinstance(c, bool):
        out["confidence"] = min(1.0, max(0.0, float(c)))
    return out


def clamp(raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, limit, prefer in (
        ("name", MAX_NAME, None),
        ("category", MAX_CATEGORY, context.get("categories")),
        ("description", MAX_DESCRIPTION, None),
    ):
        if (s := _scored(raw.get(key), limit, prefer)) is not None:
            out[key] = s
    tags_raw = raw.get("tags")
    if isinstance(tags_raw, list):
        seen: set[str] = set()
        tags = []
        for t in tags_raw:
            s = _scored(t, MAX_TAG, context.get("tags"))
            if s is None or s["value"].casefold() in seen:
                continue
            seen.add(s["value"].casefold())
            tags.append(s)
            if len(tags) >= MAX_TAGS:
                break
        if tags:
            out["tags"] = tags
    if not out:
        raise BackendError("model output had no usable fields")
    return out


class OllamaBackend(Backend):
    id = "ollama"

    def __init__(self, url: str | None = None, model: str | None = None):
        self.url = (url or os.environ.get("OLLAMA_URL") or DEFAULT_URL).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", "").strip() or DEFAULT_MODEL

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, timeout=get_settings().backend_timeout_s)

    async def info(self) -> dict[str, Any]:
        return {"name": f"ollama-{self.model}", "capabilities": list(CAPABILITIES)}

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client() as client:
                r = await client.post(path, json=payload)
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"ollama unreachable at {self.url}: {exc}") from None
        if r.status_code >= 400:
            raise BackendError(f"ollama returned HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            raise BackendError("ollama returned a non-JSON body") from None
        if not isinstance(data, dict):
            raise BackendError("ollama returned an unexpected body")
        return data

    async def generate(self, image_bytes: bytes, context: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0},
            "messages": [
                {
                    "role": "user",
                    "content": build_prompt(context),
                    "images": [base64.b64encode(image_bytes).decode()],
                }
            ],
        }
        data = await self._post("/api/chat", payload)
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise BackendError("ollama response had no message content")
        return clamp(extract_json(content), context)

    async def suggest(self, image_bytes: bytes, mime: str, context: dict[str, Any]) -> Any:
        return await self.generate(image_bytes, context)

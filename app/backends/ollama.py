"""Ollama backend: asks a local vision model (Moondream by default) for a
Suggestion as strict JSON, then validates and clamps the answer.

Env (read when the backend is created):
  OLLAMA_URL    default http://ollama:11434
  OLLAMA_MODEL  default moondream (qwen2-vl-2b style tags also work)
  OLLAMA_WARMUP          1 (default) loads the model in the background at startup;
                         /v1/info reports ready:false and /v1/suggest answers 503
                         with Retry-After until it has loaded. 0 skips warm-up.
  OLLAMA_QUEUE_SIZE      requests allowed to wait behind the one in flight (default 1)
  OLLAMA_RETRY_AFTER_S   Retry-After seconds on 503 busy/warming (default 5)
  OLLAMA_NUM_PREDICT     max tokens the model may generate per request (default 256;
                         0 or less removes the cap). Stops runaway output, e.g. a
                         small model padding format=json output with whitespace.
  OLLAMA_TEMPERATURE     sampling temperature (default 0, deterministic)
  OLLAMA_KEEP_ALIVE      how long Ollama keeps the model loaded after a request
                         (default 30m; -1 keeps it loaded forever)
  OLLAMA_JSON_FORMAT     1 (default) sends format=json; 0 lets the model answer freely
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import threading
import time
from typing import Any

import httpx

from ..settings import get_settings
from .base import CAPABILITIES, Backend, BackendUnavailable

DEFAULT_URL = "http://ollama:11434"
DEFAULT_MODEL = "moondream"
DEFAULT_NUM_PREDICT = 256
DEFAULT_KEEP_ALIVE = "30m"

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


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _keep_alive(raw: str) -> str | int:
    # Ollama takes a duration string ("30m") or a number of seconds (-1 = forever).
    raw = raw.strip() or DEFAULT_KEEP_ALIVE
    try:
        return int(raw)
    except ValueError:
        return raw


class Busy(BackendUnavailable):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.headers = {"Retry-After": str(retry_after)}


class OllamaBackend(Backend):
    id = "ollama"

    def __init__(self, url: str | None = None, model: str | None = None):
        self.url = (url or os.environ.get("OLLAMA_URL") or DEFAULT_URL).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", "").strip() or DEFAULT_MODEL
        self.queue_size = max(0, _env_int("OLLAMA_QUEUE_SIZE", 1))
        self.retry_after = max(1, _env_int("OLLAMA_RETRY_AFTER_S", 5))
        self.warmup_enabled = os.environ.get("OLLAMA_WARMUP", "1").strip() not in ("0", "false")
        self.num_predict = _env_int("OLLAMA_NUM_PREDICT", DEFAULT_NUM_PREDICT)
        self.temperature = _env_float("OLLAMA_TEMPERATURE", 0.0)
        self.keep_alive = _keep_alive(os.environ.get("OLLAMA_KEEP_ALIVE", ""))
        self.json_format = _env_flag("OLLAMA_JSON_FORMAT", True)
        self.ready = not self.warmup_enabled
        self.warmup_error: str | None = None
        self._warm_thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._in_flight = 0
        self._waiting = 0

    # -- warm-up (thread, so it is independent of any event loop) ----------
    def start(self) -> None:
        with self._start_lock:
            if self.ready or self._warm_thread is not None:
                return
            self._warm_thread = threading.Thread(target=self._warm, daemon=True)
            self._warm_thread.start()

    def _warm(self) -> None:
        delay = 1.0
        payload = {
            "model": self.model,
            "prompt": "",
            "stream": False,
            "keep_alive": self.keep_alive,
        }
        while True:
            try:
                r = httpx.post(f"{self.url}/api/generate", json=payload, timeout=600)
                if r.status_code < 400:
                    self.ready, self.warmup_error = True, None
                    return
                self.warmup_error = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                self.warmup_error = str(exc) or type(exc).__name__
            time.sleep(delay)
            delay = min(delay * 2, 30.0)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, timeout=get_settings().backend_timeout_s)

    async def info(self) -> dict[str, Any]:
        self.start()
        return {
            "name": f"ollama-{self.model}",
            "capabilities": list(CAPABILITIES),
            "ready": self.ready,
        }

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

    def options(self) -> dict[str, Any]:
        opts: dict[str, Any] = {"temperature": self.temperature}
        if self.num_predict > 0:
            opts["num_predict"] = self.num_predict
        return opts

    async def generate(self, image_bytes: bytes, context: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": self.options(),
            "messages": [
                {
                    "role": "user",
                    "content": build_prompt(context),
                    "images": [base64.b64encode(image_bytes).decode()],
                }
            ],
        }
        if self.json_format:
            payload["format"] = "json"
        data = await self._post("/api/chat", payload)
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise BackendError("ollama response had no message content")
        return clamp(extract_json(content), context)

    async def suggest(self, image_bytes: bytes, mime: str, context: dict[str, Any]) -> Any:
        self.start()
        if not self.ready:
            raise Busy(f"model '{self.model}' is warming up", self.retry_after)
        # One request in flight, up to queue_size waiting; the rest get 503.
        # Plain counters + polling keep this independent of the event loop.
        if self._in_flight and self._waiting >= self.queue_size:
            raise Busy("backend busy, queue full", self.retry_after)
        self._waiting += 1
        try:
            while self._in_flight:
                await asyncio.sleep(0.02)
        finally:
            self._waiting -= 1
        self._in_flight += 1
        try:
            return await self.generate(image_bytes, context)
        finally:
            self._in_flight -= 1

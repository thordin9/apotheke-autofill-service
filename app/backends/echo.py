"""Deterministic echo backend for tests, CI and QA failure simulation.

Env knobs (read per request, so they work with plain uvicorn):
  ECHO_MODE      ok (default) | error (-> 503) | malformed (-> 200 non-JSON body)
                 | slow (sleeps past BACKEND_TIMEOUT_S -> 504)
  ECHO_DELAY_MS  extra delay before answering in any mode (default 0); also
                 the generation time the cold-start check assumes
  ECHO_MODEL     the default model's name (default "echo")
  ECHO_INSTALLED_MODELS  comma-separated models echo pretends are installed
                 (default: all of ALLOWED_MODELS); others fall back to the default
  ECHO_LOAD_MS   simulated load time when a request switches models (default 0)

Model switching (with ALLOWED_MODELS set) mirrors the Ollama backend: echo
holds one "loaded" model, starting with the default. A request for another
model is a cold start: when ECHO_LOAD_MS + ECHO_DELAY_MS fit in 90% of
BACKEND_TIMEOUT_S it waits for the load, otherwise it gets 503 + Retry-After
at once and the load carries on in the background.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import time
from typing import Any

from ..settings import get_settings, parse_models
from .base import CAPABILITIES, Backend, BackendUnavailable, Busy
from .selection import allowlist, cold_wait_fits, model_key, resolve_model, same_model

DEFAULT_TAGS = ["echo", "sample"]
DEFAULT_ECHO_MODEL = "echo"


def _env_ms(name: str) -> int:
    try:
        return max(0, int(os.environ.get(name, "0") or 0))
    except ValueError:
        return 0


def _delay_ms() -> int:
    return _env_ms("ECHO_DELAY_MS")


def _default_model() -> str:
    return os.environ.get("ECHO_MODEL", "").strip() or DEFAULT_ECHO_MODEL


def _installed(allowed: list[str]) -> set[str]:
    raw = os.environ.get("ECHO_INSTALLED_MODELS")
    names = allowed if raw is None or not raw.strip() else list(parse_models(raw))
    return {model_key(m) for m in names}


class MalformedOutput(Exception):
    """Signals the app to send a deliberately broken body (QA only)."""


class EchoBackend(Backend):
    id = "echo"

    def __init__(self) -> None:
        # Simulated Ollama memory: one loaded model (None = the default) and
        # when its load finishes (monotonic time).
        self._loaded: str | None = None
        self._ready_at = 0.0

    def _models(self) -> tuple[str, list[str], set[str]]:
        default = _default_model()
        allowed = allowlist(default, get_settings().allowed_models)
        return default, allowed, _installed(allowed)

    def _loaded_model(self, default: str) -> str:
        return self._loaded or default

    async def info(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": "echo", "capabilities": list(CAPABILITIES)}
        # With ECHO_DELAY_MS set, every answer takes that long, so report it:
        # lets clients test their progress estimate without a real model.
        delay_ms = _delay_ms()
        if delay_ms > 0:
            out["avg_latency_ms"] = delay_ms
        if get_settings().allowed_models:
            default, allowed, installed = self._models()
            loading = self._ready_at > time.monotonic()
            loaded = self._loaded_model(default)
            out["ready"] = not loading
            entries = []
            for m in allowed:
                if m != default and model_key(m) not in installed:
                    continue
                entry: dict[str, Any] = {
                    "id": m,
                    "default": m == default,
                    "loaded": same_model(m, loaded) and not loading,
                }
                if delay_ms > 0:
                    entry["avg_latency_ms"] = delay_ms
                entries.append(entry)
            out["models"] = entries
        return out

    async def _admit(self, model: str, default: str, delay_ms: int) -> None:
        """Simulated cold start, with the Ollama backend's fast-503 / wait rule."""
        now = time.monotonic()
        switch = not same_model(model, self._loaded_model(default))
        remaining = _env_ms("ECHO_LOAD_MS") / 1000 if switch else max(0.0, self._ready_at - now)
        if remaining <= 0:
            if switch:
                self._loaded = model
            return
        if switch:
            # Like Ollama with one model slot: the switch evicts the old one.
            self._loaded, self._ready_at = model, now + remaining
        timeout = get_settings().backend_timeout_s
        if not cold_wait_fits(remaining, delay_ms / 1000, timeout):
            raise Busy(f"model '{model}' is loading", max(1, math.ceil(remaining)))
        await asyncio.sleep(remaining)

    async def suggest(self, image_bytes: bytes, mime: str, context: dict[str, Any]) -> Any:
        mode = os.environ.get("ECHO_MODE", "ok").strip().lower() or "ok"
        delay_ms = _delay_ms()
        model = default = _default_model()
        if get_settings().allowed_models:
            _, allowed, installed = self._models()
            model = resolve_model(context.get("model"), default, allowed, installed)
            await self._admit(model, default, delay_ms)
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000)
        if mode == "slow":
            await asyncio.sleep(3600)
        if mode == "error":
            raise BackendUnavailable("echo backend simulated failure (ECHO_MODE=error)")
        if mode == "malformed":
            raise MalformedOutput()

        kind = mime.split("/")[-1].upper()
        digest = hashlib.sha256(image_bytes).hexdigest()[:8]
        categories = [c for c in context.get("categories", []) if c.strip()]
        tags = [t for t in context.get("tags", []) if t.strip()][:3] or DEFAULT_TAGS
        return {
            "name": {"value": f"Echo item {digest}", "confidence": 0.5},
            "category": {"value": categories[0] if categories else "Misc", "confidence": 0.5},
            "tags": [{"value": t, "confidence": 0.5} for t in tags],
            "description": {
                "value": f"{kind} image, {len(image_bytes)} bytes",
                "confidence": 0.5,
            },
            "model_used": model,
        }

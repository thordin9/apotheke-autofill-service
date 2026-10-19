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

With ALLOWED_MODELS set, echo also handles one request at a time like Ollama
(OLLAMA_NUM_PARALLEL=1, OLLAMA_QUEUE_SIZE=1): one request waits behind the one
in flight and more get 503 "queue full". A queued request is refused at once
(503 "a queued request / a model switch would time out") when the requests
ahead of it (ECHO_DELAY_MS each), its own answer, the in-flight request's
remaining load, and, for another model than the one in flight, ECHO_LOAD_MS
exceed 90% of BACKEND_TIMEOUT_S. Busy 503s carry Retry-After =
ECHO_DELAY_MS x (waiting + 1), plus ECHO_LOAD_MS when the refused request
switches models, in whole seconds, at least 1.
A single request behaves as before. Without ALLOWED_MODELS requests are not
serialized (v1 behaviour).

ECHO_BRAND, ECHO_MODEL_NUMBER and ECHO_SERIAL_NUMBER set the raw identity
fields (unset = omitted). A value that starts with ``json:`` is JSON
(``json:null``, ``json:""``, ``json:{"value":"x"}``); anything else is a
literal string, including empty, over-long and unusual characters.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from typing import Any

from ..settings import get_settings, parse_models
from .base import CAPABILITIES, Backend, BackendUnavailable, Busy
from .selection import (
    TIMEOUT_SHARE,
    allowlist,
    cold_wait_fits,
    model_key,
    resolve_model,
    same_model,
)

DEFAULT_TAGS = ["echo", "sample"]
DEFAULT_ECHO_MODEL = "echo"
# Requests allowed to wait behind the one in flight (Ollama's default).
QUEUE_SIZE = 1


def _env_ms(name: str) -> int:
    try:
        return max(0, int(os.environ.get(name, "0") or 0))
    except ValueError:
        return 0


def _delay_ms() -> int:
    return _env_ms("ECHO_DELAY_MS")


def _identity_raw() -> dict[str, Any]:
    """Raw brand / model_number / serial_number from ECHO_* (unset = omit).

    ``json:`` prefix parses the rest as JSON so QA can send null, a nested
    ``{"value": ...}`` object, or a string. Any other value is literal text.
    """
    out: dict[str, Any] = {}
    for env, key in (
        ("ECHO_BRAND", "brand"),
        ("ECHO_MODEL_NUMBER", "model_number"),
        ("ECHO_SERIAL_NUMBER", "serial_number"),
    ):
        if env not in os.environ:
            continue
        raw = os.environ.get(env, "")
        if raw.startswith("json:"):
            try:
                out[key] = json.loads(raw[5:])
            except ValueError:
                out[key] = raw[5:]
        else:
            out[key] = raw
    return out


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
        # One request at a time with ALLOWED_MODELS set. Plain counters and
        # polling, like the Ollama backend, so no event loop is assumed.
        self._in_flight = 0
        self._waiting = 0
        self._current: str | None = None  # model of the request in flight
        # model_key()s with a successful suggest since they became resident.
        self._warm: set[str] = set()

    async def _take_turn(self, model: str, delay_ms: int) -> None:
        """Wait for the request in flight, with Ollama's queue rules."""
        if self._in_flight:
            gen_s = delay_ms / 1000
            switch = self._current is not None and not same_model(model, self._current)
            retry_s = gen_s * (self._waiting + 1)
            if switch:
                retry_s += _env_ms("ECHO_LOAD_MS") / 1000
            retry = max(1, math.ceil(retry_s))
            if self._waiting >= QUEUE_SIZE:
                raise Busy("backend busy, queue full", retry)
            need = (self._waiting + 2) * gen_s
            if switch:
                need += _env_ms("ECHO_LOAD_MS") / 1000
            # Remaining load of the request already in flight (F5).
            need += max(0.0, self._ready_at - time.monotonic())
            if need > get_settings().backend_timeout_s * TIMEOUT_SHARE:
                reason = "a model switch" if switch else "a queued request"
                raise Busy(f"backend busy, {reason} would time out", retry)
        self._waiting += 1
        try:
            while self._in_flight:
                await asyncio.sleep(0.02)
        finally:
            self._waiting -= 1
        self._in_flight += 1
        self._current = model

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
                    "state": self._state(m, loaded, loading),
                }
                if delay_ms > 0:
                    entry["avg_latency_ms"] = delay_ms
                entries.append(entry)
            out["models"] = entries
        return out

    def _state(self, model: str, resident: str, loading: bool) -> str:
        if not same_model(model, resident):
            return "cold"
        if loading or model_key(model) not in self._warm:
            return "loading"
        return "warm"

    async def health_snapshot(self) -> dict[str, Any]:
        data = await self.info()
        return {"degraded": False, "models": data.get("models")}

    async def _admit(self, model: str, default: str, delay_ms: int) -> None:
        """Simulated cold start, with the Ollama backend's fast-503 / wait rule."""
        now = time.monotonic()
        switch = not same_model(model, self._loaded_model(default))
        remaining = _env_ms("ECHO_LOAD_MS") / 1000 if switch else max(0.0, self._ready_at - now)
        if switch:
            # A new residency is not warm until this suggest finishes.
            self._warm.discard(model_key(model))
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
        if not get_settings().allowed_models:
            return await self._answer(image_bytes, mime, context, mode, delay_ms, model)
        _, allowed, installed = self._models()
        model = resolve_model(context.get("model"), default, allowed, installed)
        await self._take_turn(model, delay_ms)
        try:
            await self._admit(model, default, delay_ms)
            out = await self._answer(image_bytes, mime, context, mode, delay_ms, model)
            self._warm.add(model_key(model))
            return out
        finally:
            self._in_flight -= 1

    async def _answer(
        self,
        image_bytes: bytes,
        mime: str,
        context: dict[str, Any],
        mode: str,
        delay_ms: int,
        model: str,
    ) -> Any:
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
        out: dict[str, Any] = {
            "name": {"value": f"Echo item {digest}", "confidence": 0.5},
            "category": {"value": categories[0] if categories else "Misc", "confidence": 0.5},
            "tags": [{"value": t, "confidence": 0.5} for t in tags],
            "description": {
                "value": f"{kind} image, {len(image_bytes)} bytes",
                "confidence": 0.5,
            },
            "model_used": model,
        }
        out.update(_identity_raw())
        return out

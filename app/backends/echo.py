"""Deterministic echo backend for tests, CI and QA failure simulation.

Env knobs (read per request, so they work with plain uvicorn):
  ECHO_MODE      ok (default) | error (-> 503) | malformed (-> 200 non-JSON body)
                 | slow (sleeps past BACKEND_TIMEOUT_S -> 504)
  ECHO_DELAY_MS  extra delay before answering in any mode (default 0)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from typing import Any

from .base import CAPABILITIES, Backend, BackendUnavailable

DEFAULT_TAGS = ["echo", "sample"]


class MalformedOutput(Exception):
    """Signals the app to send a deliberately broken body (QA only)."""


class EchoBackend(Backend):
    id = "echo"

    async def info(self) -> dict[str, Any]:
        return {"name": "echo", "capabilities": list(CAPABILITIES)}

    async def suggest(self, image_bytes: bytes, mime: str, context: dict[str, Any]) -> Any:
        mode = os.environ.get("ECHO_MODE", "ok").strip().lower() or "ok"
        try:
            delay_ms = int(os.environ.get("ECHO_DELAY_MS", "0") or 0)
        except ValueError:
            delay_ms = 0
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
        }

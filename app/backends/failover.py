"""Ollama primary with an optional Cloudflare fallback.

Failover runs only when FALLBACK_BACKEND=cloudflare. The combined wait stays
under Apotheke's client timeout, which is BACKEND_TIMEOUT_S plus 5 seconds.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..settings import get_settings
from .base import Backend, BackendUnavailable, Busy
from .cloudflare import CloudflareBackend, ImageNotAccepted, PaidPlanRequired, RateLimited
from .ollama import BackendError

# Apotheke's recommended client timeout is the server budget plus this.
CLIENT_TIMEOUT_EXTRA_S = 5.0
# Finish at least this long before the client gives up.
DEADLINE_SAFETY_S = 1.0
BOTH_FAILED = "ollama and the fallback backend failed"


def client_budget_s(backend_timeout_s: float) -> float:
    return backend_timeout_s + CLIENT_TIMEOUT_EXTRA_S


def usable_budget_s(backend_timeout_s: float) -> float:
    return client_budget_s(backend_timeout_s) - DEADLINE_SAFETY_S


def attempt_budgets(backend_timeout_s: float, cf_timeout_s: float) -> tuple[float, float]:
    """Return (ollama_limit_s, cf_reserve_s).

    A long Ollama attempt keeps a reserve for the Cloudflare call. The sum of
    the Ollama limit and that reserve stays inside the usable budget, which is
    itself under the client timeout.
    """
    usable = usable_budget_s(backend_timeout_s)
    client = client_budget_s(backend_timeout_s)
    if cf_timeout_s <= 0 or usable <= 0:
        return max(0.05, min(backend_timeout_s, usable)), 0.0
    wanted = min(cf_timeout_s, max(5.0, client * 0.2))
    reserve = min(wanted, cf_timeout_s, usable * 0.5)
    ollama_limit = min(backend_timeout_s, max(0.05, usable - reserve))
    return ollama_limit, reserve


class FailoverBackend(Backend):
    """Primary Ollama, fallback Cloudflare. `id` stays the primary's id."""

    owns_deadline = True

    def __init__(self, primary: Backend, fallback: CloudflareBackend):
        self.primary = primary
        self.fallback = fallback
        self.id = primary.id

    def start(self) -> None:
        start = getattr(self.primary, "start", None)
        if start is not None:
            start()

    async def health_snapshot(self) -> dict[str, Any]:
        health = getattr(self.primary, "health_snapshot", None)
        if health is None:
            return {"degraded": False}
        return await health()

    def degraded(self) -> bool:
        fn = getattr(self.primary, "degraded", None)
        return bool(fn()) if fn is not None else False

    async def info(self) -> dict[str, Any]:
        try:
            return await self.primary.info()
        except BackendUnavailable:
            return {
                "name": self.primary.id,
                "capabilities": [],
                "ready": False,
                "unavailable": True,
            }

    async def _primary_degraded(self) -> bool:
        try:
            snap = await asyncio.wait_for(self.health_snapshot(), timeout=2.0)
        except Exception:
            return self.degraded()
        return isinstance(snap, dict) and snap.get("degraded") is True

    async def _call_fallback(
        self, image_bytes: bytes, mime: str, context: dict[str, Any], deadline: float
    ) -> Any:
        remaining = deadline - time.monotonic()
        if remaining <= 0.05:
            raise BackendUnavailable(BOTH_FAILED)
        timeout_s = min(self.fallback.timeout_s, remaining)
        try:
            return await asyncio.wait_for(
                self.fallback.suggest(image_bytes, mime, context, timeout_s=timeout_s),
                timeout=remaining,
            )
        except ImageNotAccepted:
            raise
        except TimeoutError:
            raise BackendUnavailable(BOTH_FAILED) from None
        except PaidPlanRequired:
            raise BackendUnavailable(BOTH_FAILED) from None
        except RateLimited as exc:
            err = BackendUnavailable(BOTH_FAILED)
            headers = getattr(exc, "headers", None)
            if headers:
                err.headers = headers
            raise err from None
        except BackendUnavailable:
            raise BackendUnavailable(BOTH_FAILED) from None

    async def suggest(self, image_bytes: bytes, mime: str, context: dict[str, Any]) -> Any:
        settings = get_settings()
        ollama_limit, _reserve = attempt_budgets(
            settings.backend_timeout_s, self.fallback.timeout_s
        )
        deadline = time.monotonic() + usable_budget_s(settings.backend_timeout_s)
        if await self._primary_degraded():
            try:
                return await self._call_fallback(image_bytes, mime, context, deadline)
            except ImageNotAccepted:
                return await self.primary.suggest(image_bytes, mime, context)
        try:
            return await asyncio.wait_for(
                self.primary.suggest(image_bytes, mime, context),
                timeout=ollama_limit,
            )
        except Busy as exc:
            if exc.reason == "cold" and not settings.failover_on_cold:
                raise
            return await self._failover(exc, image_bytes, mime, context, deadline)
        except BackendError as exc:
            status = getattr(exc, "status", None)
            if status is not None and 400 <= status < 500:
                raise
            if status is not None and status >= 500:
                return await self._failover(exc, image_bytes, mime, context, deadline)
            raise
        except BackendUnavailable as exc:
            return await self._failover(exc, image_bytes, mime, context, deadline)
        except TimeoutError as exc:
            return await self._failover(exc, image_bytes, mime, context, deadline)

    async def _failover(
        self,
        original: BaseException,
        image_bytes: bytes,
        mime: str,
        context: dict[str, Any],
        deadline: float,
    ) -> Any:
        try:
            return await self._call_fallback(image_bytes, mime, context, deadline)
        except ImageNotAccepted:
            raise original from None

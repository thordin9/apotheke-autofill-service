"""Direction-agnostic failover between Ollama and Cloudflare.

Ollama primary with FALLBACK_BACKEND=cloudflare keeps the original rules.
Cloudflare primary with FALLBACK_BACKEND=ollama is the recommended opt-in:
one request deadline, a warm or cold reserve for the local model, and a
Cloudflare slice that shrinks so the pair stays under that deadline.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

from ..settings import get_settings
from .base import Backend, BackendUnavailable, Busy
from .cloudflare import (
    BudgetExhausted,
    CloudflareBackend,
    ImageNotAccepted,
    PaidPlanRequired,
    RateLimited,
)
from .ollama import BackendError, OllamaBackend
from .selection import model_key

# Apotheke's recommended client timeout is the server budget plus this.
CLIENT_TIMEOUT_EXTRA_S = 5.0
# Finish at least this long before the client gives up, on the Ollama-primary path.
DEADLINE_SAFETY_S = 1.0
# Apotheke's own client timeout. REQUEST_DEADLINE_S must stay under it.
APOTHEKE_CLIENT_TIMEOUT_S = 50.0
# Effective Cloudflare slice never goes below this when Ollama is the fallback.
MIN_CF_SLICE_S = 5.0
BOTH_FAILED = "the primary backend and the fallback backend failed"
NOT_ENOUGH_TIME = "not enough time left to fall back to the local model"
LOCAL_DEADLINE = "local model did not answer before the request deadline"


def client_budget_s(backend_timeout_s: float) -> float:
    return backend_timeout_s + CLIENT_TIMEOUT_EXTRA_S


def usable_budget_s(backend_timeout_s: float) -> float:
    return client_budget_s(backend_timeout_s) - DEADLINE_SAFETY_S


def attempt_budgets(backend_timeout_s: float, cf_timeout_s: float) -> tuple[float, float]:
    """Return (ollama_limit_s, cf_reserve_s) for Ollama primary.

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


def repair_allowance_s(request_deadline_s: float, cf_timeout_s: float, reserve_s: float) -> float:
    """A second full Cloudflare attempt, only when it fits beside the first.

    With the recommended defaults the room after the reserve is smaller than
    two CF_TIMEOUT_S values, so this is 0. A repair then runs only when the
    first call returns with time left inside the slice.
    """
    room = request_deadline_s - reserve_s
    if cf_timeout_s > 0 and room >= cf_timeout_s * 2:
        return cf_timeout_s
    return 0.0


def effective_cf_slice_s(request_deadline_s: float, cf_timeout_s: float, reserve_s: float) -> float:
    """Cloudflare time for one attempt, leaving `reserve_s` for Ollama.

    slice = min(CF_TIMEOUT_S, REQUEST_DEADLINE_S - reserve - repair allowance),
    and at least MIN_CF_SLICE_S. The repair allowance is dropped when keeping
    it would push the slice under that floor, so slice + reserve still fits
    in the deadline whenever the startup check passed.
    """
    allowance = repair_allowance_s(request_deadline_s, cf_timeout_s, reserve_s)
    raw = request_deadline_s - reserve_s - allowance
    if raw < MIN_CF_SLICE_S:
        allowance = 0.0
        raw = request_deadline_s - reserve_s
    return max(MIN_CF_SLICE_S, min(cf_timeout_s, raw))


def _public_seconds(value: float) -> int | float:
    rounded = round(float(value), 3)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _retry_after(seconds: float) -> str:
    return str(max(1, min(86400, math.ceil(seconds))))


def _with_retry(message: str, seconds: float) -> BackendUnavailable:
    err = BackendUnavailable(message)
    err.headers = {"Retry-After": _retry_after(seconds)}
    return err


def _ollama_service_state(ollama: OllamaBackend, info: dict[str, Any]) -> str:
    if info.get("unavailable") is True:
        return "unavailable"
    if ollama.degraded():
        return "degraded"
    if info.get("ready") is False:
        return "warming"
    return "ok"


def _vram_of(ollama: OllamaBackend) -> int | None:
    if ollama._vram is None:
        return None
    raw = ollama._vram.get(model_key(ollama.model))
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return None
    return raw


class FailoverBackend(Backend):
    """Primary plus one fallback. `id` stays the primary's id."""

    owns_deadline = True

    def __init__(self, primary: Backend, fallback: Backend):
        self.primary = primary
        self.fallback = fallback
        self.id = primary.id
        self._cf_primary = isinstance(primary, CloudflareBackend)

    @property
    def cloudflare_backend(self) -> CloudflareBackend | None:
        if isinstance(self.primary, CloudflareBackend):
            return self.primary
        if isinstance(self.fallback, CloudflareBackend):
            return self.fallback
        return None

    def start(self) -> None:
        for side in (self.primary, self.fallback):
            start = getattr(side, "start", None)
            if start is not None:
                start()

    def _local_ollama(self) -> OllamaBackend | None:
        if isinstance(self.primary, OllamaBackend):
            return self.primary
        if isinstance(self.fallback, OllamaBackend):
            return self.fallback
        return None

    async def health_snapshot(self) -> dict[str, Any]:
        ollama = self._local_ollama()
        if ollama is None:
            return {"degraded": False}
        return await ollama.health_snapshot()

    def degraded(self) -> bool:
        ollama = self._local_ollama()
        if ollama is None:
            return False
        return bool(ollama.degraded())

    async def info(self) -> dict[str, Any]:
        try:
            data = await self.primary.info()
        except BackendUnavailable:
            data = {
                "name": self.primary.id,
                "capabilities": [],
                "ready": False,
                "unavailable": True,
            }
        data = dict(data)
        data["_backends"] = await self._listing(data)
        return data

    def _plan(self, ollama_state: str) -> dict[str, Any]:
        settings = get_settings()
        reserve = (
            settings.ollama_warm_reserve_s
            if ollama_state == "warm"
            else settings.ollama_cold_reserve_s
        )
        deadline = settings.request_deadline_s
        cf = self.cloudflare_backend
        timeout = cf.timeout_s if cf is not None else settings.cf_timeout_s
        return {
            "request_deadline_s": deadline,
            "cf_slice_s": effective_cf_slice_s(deadline, timeout, reserve),
            "ollama_reserve_s": reserve,
            "ollama_state": ollama_state if ollama_state in ("warm", "loading", "cold") else "cold",
            "repair_allowance_s": repair_allowance_s(deadline, timeout, reserve),
        }

    async def _ollama_state(self) -> str:
        ollama = self._local_ollama()
        if ollama is None:
            return "cold"
        try:
            await asyncio.wait_for(ollama.health_snapshot(), timeout=1.0)
        except Exception:
            return "cold"
        return ollama.residency()

    async def _ollama_entry(self) -> dict[str, Any]:
        ollama = self._local_ollama()
        assert ollama is not None
        try:
            snap = await asyncio.wait_for(ollama.health_snapshot(), timeout=2.0)
        except Exception:
            snap = {}
        if not isinstance(snap, dict):
            snap = {}
        info = {"ready": ollama.ready}
        state = _ollama_service_state(ollama, info)
        if snap.get("degraded") is True:
            state = "degraded"
        entry: dict[str, Any] = {
            "id": "ollama",
            "state": state,
            "can_serve": True,
            "model": ollama.model,
        }
        vram = _vram_of(ollama)
        if vram is not None:
            entry["size_vram"] = vram
        models = snap.get("models") if isinstance(snap.get("models"), list) else None
        if not models:
            model_entry: dict[str, Any] = {
                "id": ollama.model,
                "default": True,
                "state": ollama.residency(),
            }
            if vram is not None:
                model_entry["size_vram"] = vram
            models = [model_entry]
        entry["models"] = models
        return entry

    async def _listing(self, primary_info: dict[str, Any]) -> dict[str, Any]:
        cf = self.cloudflare_backend
        assert cf is not None
        view = await cf.budget.snapshot()
        cf_ready = cf.can_serve()
        if self._cf_primary:
            blocked = not cf_ready
            state = await self._ollama_state()
            plan = self._plan(state)
            budget = view.as_dict()
            if not (cf.account_id and cf.token):
                budget["reason"] = "Cloudflare is not configured"
            primary: dict[str, Any] = {
                "id": "cloudflare",
                "state": "unavailable" if blocked else "ok",
                "can_serve": cf_ready,
                "model": cf.model,
                "budget": budget,
                "request_deadline_s": _public_seconds(plan["request_deadline_s"]),
                "cf_slice_s": _public_seconds(plan["cf_slice_s"]),
                "ollama_reserve_s": _public_seconds(plan["ollama_reserve_s"]),
                "ollama_state": plan["ollama_state"],
            }
            return {"primary": primary, "fallback": await self._ollama_entry()}
        primary = {
            "id": str(self.primary.id),
            "state": _ollama_service_state(self.primary, primary_info)
            if isinstance(self.primary, OllamaBackend)
            else ("unavailable" if primary_info.get("unavailable") else "ok"),
        }
        fallback = {
            "id": "cloudflare",
            "state": "ready" if cf_ready else "unavailable",
            "can_serve": cf_ready,
            "model": cf.model,
            "budget": view.as_dict(),
        }
        return {"primary": primary, "fallback": fallback}

    async def suggest(self, image_bytes: bytes, mime: str, context: dict[str, Any]) -> Any:
        if self._cf_primary:
            return await self._suggest_cf_primary(image_bytes, mime, context)
        return await self._suggest_ollama_primary(image_bytes, mime, context)

    async def _suggest_cf_primary(
        self, image_bytes: bytes, mime: str, context: dict[str, Any]
    ) -> Any:
        assert isinstance(self.primary, CloudflareBackend)
        settings = get_settings()
        deadline = time.monotonic() + settings.request_deadline_s
        state = await self._ollama_state()
        plan = self._plan(state)
        # Missing credentials, an unreadable budget file, or a used-up budget:
        # do not call Cloudflare. A 401/403 or Workers Paid block still tries,
        # so a later success can clear can_serve.
        creds = bool(self.primary.account_id and self.primary.token)
        if not creds or not self.primary.budget.can_call():
            return await self._ollama_only(image_bytes, mime, context, deadline)
        try:
            return await asyncio.wait_for(
                self.primary.suggest(image_bytes, mime, context, timeout_s=plan["cf_slice_s"]),
                timeout=plan["cf_slice_s"],
            )
        except ImageNotAccepted:
            return await self._ollama_only(image_bytes, mime, context, deadline)
        except BudgetExhausted:
            return await self._ollama_only(image_bytes, mime, context, deadline)
        except BackendError as exc:
            if _cf_client_error(exc):
                raise
            return await self._ollama_after_failure(image_bytes, mime, context, deadline)
        except (TimeoutError, PaidPlanRequired, RateLimited, BackendUnavailable):
            return await self._ollama_after_failure(image_bytes, mime, context, deadline)

    async def _ollama_only(
        self,
        image_bytes: bytes,
        mime: str,
        context: dict[str, Any],
        deadline: float,
    ) -> Any:
        """Cloudflare was not called. Ollama's own errors pass through."""
        return await self._run_ollama(image_bytes, mime, context, deadline, after_failure=False)

    async def _ollama_after_failure(
        self,
        image_bytes: bytes,
        mime: str,
        context: dict[str, Any],
        deadline: float,
    ) -> Any:
        return await self._run_ollama(image_bytes, mime, context, deadline, after_failure=True)

    async def _run_ollama(
        self,
        image_bytes: bytes,
        mime: str,
        context: dict[str, Any],
        deadline: float,
        *,
        after_failure: bool,
    ) -> Any:
        assert isinstance(self.fallback, OllamaBackend)
        state = await self._ollama_state()
        needed = self._plan(state)["ollama_reserve_s"]
        remaining = deadline - time.monotonic()
        if remaining < needed:
            raise _with_retry(NOT_ENOUGH_TIME, needed)
        try:
            return await asyncio.wait_for(
                self.fallback.suggest(image_bytes, mime, context),
                timeout=remaining,
            )
        except Busy:
            raise
        except TimeoutError:
            raise _with_retry(LOCAL_DEADLINE, needed) from None
        except BackendUnavailable:
            if after_failure:
                raise BackendUnavailable(BOTH_FAILED) from None
            raise

    async def _primary_degraded(self) -> bool:
        try:
            snap = await asyncio.wait_for(self.health_snapshot(), timeout=2.0)
        except Exception:
            return self.degraded()
        return isinstance(snap, dict) and snap.get("degraded") is True

    async def _call_cf_fallback(
        self, image_bytes: bytes, mime: str, context: dict[str, Any], deadline: float
    ) -> Any:
        assert isinstance(self.fallback, CloudflareBackend)
        if not self.fallback.budget.can_call():
            raise BudgetExhausted
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
        except BudgetExhausted:
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

    async def _suggest_ollama_primary(
        self, image_bytes: bytes, mime: str, context: dict[str, Any]
    ) -> Any:
        assert isinstance(self.fallback, CloudflareBackend)
        settings = get_settings()
        ollama_limit, _reserve = attempt_budgets(
            settings.backend_timeout_s, self.fallback.timeout_s
        )
        deadline = time.monotonic() + usable_budget_s(settings.backend_timeout_s)
        if await self._primary_degraded():
            try:
                return await self._call_cf_fallback(image_bytes, mime, context, deadline)
            except (ImageNotAccepted, BudgetExhausted):
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
            return await self._call_cf_fallback(image_bytes, mime, context, deadline)
        except (ImageNotAccepted, BudgetExhausted):
            raise original from None


def _cf_client_error(exc: BackendError) -> bool:
    """4xx from Cloudflare that is not 401/403. Those stay with the client.

    408 is raised as TimeoutError before this runs. 401 and 403, including
    Workers Paid, fall back to Ollama.
    """
    status = getattr(exc, "status", None)
    if status is None or status in (401, 403):
        return False
    return 400 <= status < 500

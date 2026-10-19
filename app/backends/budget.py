"""Daily Neuron budget for Cloudflare Workers AI.

CF replies report `usage.neurons` when they counted the call. That value
is what the budget charges. Token counts times the pricing-page rates are
used only when `neurons` is absent. The counter is this process only. The
state file never holds a token or an account id.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Pricing page, 2026-10-19, Neurons per 1,000,000 tokens. The 19-photo bench
# (bench/results/cf-workers-ai-2026-10-19) matches these rates times usage tokens.
# https://developers.cloudflare.com/workers-ai/platform/pricing/
GEMMA_MODEL = "@cf/google/gemma-4-26b-a4b-it"
MISTRAL_MODEL = "@cf/mistralai/mistral-small-3.1-24b-instruct"
QWEN_MODEL = "@cf/qwen/qwen3.8-27b"

# (input, output, cached). Cached falls back to the input rate when absent.
_RATES: dict[str, tuple[float, float, float | None]] = {
    GEMMA_MODEL: (9091.0, 27273.0, None),
    MISTRAL_MODEL: (31876.0, 50488.0, None),
    QWEN_MODEL: (40909.0, 290909.0, 4545.0),
}
# A model that is not in the table is charged as Qwen 3.8, the most expensive
# candidate, so an unknown id cannot slip through cheap.
_UNKNOWN_RATES = _RATES[QWEN_MODEL]

# What a reply with no usage is assumed to have cost, and what must remain
# before a call is started. Above the bench medians (about 440–470 in, under
# 100 out for Gemma with thinking off).
ASSUMED_PROMPT_TOKENS = 1500
ASSUMED_COMPLETION_TOKENS = 1024
RETRY_AFTER_CAP_S = 86400
DEFAULT_BUDGET_PATH = "/var/lib/autofill/cf-neuron-budget.json"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _day(now: datetime) -> str:
    return now.astimezone(UTC).date().isoformat()


def next_reset(now: datetime) -> datetime:
    local = now.astimezone(UTC)
    return datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)


def seconds_until_reset(now: datetime | None = None) -> int:
    now = utc_now() if now is None else now.astimezone(UTC)
    seconds = int((next_reset(now) - now).total_seconds())
    return min(RETRY_AFTER_CAP_S, max(1, seconds))


def reset_iso(now: datetime | None = None) -> str:
    now = utc_now() if now is None else now
    return next_reset(now).strftime("%Y-%m-%dT%H:%M:%SZ")


def rates_for(model: str) -> tuple[float, float, float | None]:
    if model in _RATES:
        return _RATES[model]
    if "/qwen/qwen3" in model:
        return _RATES[QWEN_MODEL]
    return _UNKNOWN_RATES


def estimate_neurons(model: str) -> float:
    """Conservative cost of one call when CF returns no usage."""
    inn, out, _cached = rates_for(model)
    return (ASSUMED_PROMPT_TOKENS * inn + ASSUMED_COMPLETION_TOKENS * out) / 1_000_000


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value < 0 or value != value:  # NaN
        return None
    return float(value)


def _usage_blob(data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(data, dict):
        return None
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("usage"), dict):
        return result["usage"]
    usage = data.get("usage")
    if isinstance(usage, dict):
        return usage
    return None


def neurons_from_usage(usage: dict[str, Any] | None, model: str) -> tuple[float, bool]:
    """Return (neurons, estimated).

    Workers AI reports Neurons on `usage.neurons` when it counted the call.
    That value wins, including when token counts are also present. The rate
    table is used only when `neurons` is missing and token counts are present.
    No usage at all charges the conservative estimate.
    """
    if not isinstance(usage, dict):
        return estimate_neurons(model), True
    reported = _number(usage.get("neurons"))
    if reported is not None:
        return reported, False
    prompt = _number(usage.get("prompt_tokens"))
    if prompt is None:
        prompt = _number(usage.get("input_tokens"))
    completion = _number(usage.get("completion_tokens"))
    if completion is None:
        completion = _number(usage.get("output_tokens"))
    if prompt is None and completion is None:
        return estimate_neurons(model), True
    prompt = 0.0 if prompt is None else prompt
    completion = 0.0 if completion is None else completion
    cached = 0.0
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = _number(details.get("cached_tokens")) or 0.0
    cached = min(cached, prompt)
    inn, out, cached_rate = rates_for(model)
    cached_rate = inn if cached_rate is None else cached_rate
    neurons = ((prompt - cached) * inn + cached * cached_rate + completion * out) / 1_000_000
    return neurons, False


@dataclass
class BudgetView:
    limit: float
    used: float
    remaining: float
    resets_at: str
    reason: str | None
    can_call: bool

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "limit": self.limit,
            "used": self.used,
            "remaining": self.remaining,
            "resets_at": self.resets_at,
        }
        if self.reason:
            out["reason"] = self.reason
        return out


class NeuronBudget:
    """Persisted UTC-day counter. Fail-safe: a bad state file spends the day."""

    def __init__(self, path: str, limit: float, model: str):
        self.path = Path(path)
        self.limit = limit
        self.model = model
        self.used = 0.0
        self.day = _day(utc_now())
        self.blocked_reason: str | None = None
        self._lock = asyncio.Lock()
        self._load()

    @property
    def expected(self) -> float:
        return estimate_neurons(self.model)

    async def snapshot(self) -> BudgetView:
        """A consistent view. `/v1/info` awaits this so it cannot race a call."""
        async with self._lock:
            return self.view()

    def view(self) -> BudgetView:
        now = utc_now()
        self._roll_day(now)
        if self.blocked_reason or self.limit <= 0:
            remaining = 0.0
        else:
            remaining = max(0.0, self.limit - self.used)
        reason = self.blocked_reason
        if reason is None and self.limit <= 0:
            reason = "Cloudflare is disabled because CF_DAILY_NEURON_BUDGET is 0"
        elif reason is None and remaining < self.expected:
            reason = "daily Cloudflare budget used up, resets 00:00 UTC"
        return BudgetView(
            limit=round(self.limit, 3),
            used=round(self.used, 3),
            remaining=round(remaining, 3),
            resets_at=reset_iso(now),
            reason=reason if not self._can_call_now() else None,
            can_call=self._can_call_now(),
        )

    def can_call(self) -> bool:
        return self.view().can_call

    def _can_call_now(self) -> bool:
        if self.blocked_reason or self.limit <= 0:
            return False
        return (self.limit - self.used) >= self.expected

    def _roll_day(self, now: datetime) -> None:
        day = _day(now)
        if self.blocked_reason or day == self.day:
            return
        previous_day, previous_used = self.day, self.used
        self.day = day
        self.used = 0.0
        if not self._persist():
            self.day, self.used = previous_day, previous_used
            self._block("budget state file could not be written")

    def _load(self) -> None:
        if not self.path.exists():
            self._create_fresh()
            return
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            self._block(f"budget state file is unreadable ({exc.__class__.__name__})")
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            self._block("budget state file is corrupt")
            return
        if not isinstance(data, dict):
            self._block("budget state file is corrupt")
            return
        day = data.get("day")
        used = data.get("used")
        if not isinstance(day, str) or _number(used) is None:
            self._block("budget state file is corrupt")
            return
        today = _day(utc_now())
        self.day = day
        self.used = float(used) if day == today else 0.0
        if day != today:
            self.day = today
            self._persist()

    def _create_fresh(self) -> None:
        """Missing file starts at 0 only when creating it succeeds."""
        self.day = _day(utc_now())
        self.used = 0.0
        if not self._persist():
            self._block("budget state file is missing and could not be created")

    def _block(self, reason: str, *, persist: bool = False) -> None:
        self.blocked_reason = reason
        self.used = self.limit if self.limit > 0 else 0.0
        log.warning("cloudflare budget unavailable: %s", reason)
        if persist:
            # Best effort so a later process still sees the day as spent.
            # A corrupt file is never rewritten (callers pass persist=False).
            self._persist()

    def _persist(self) -> bool:
        payload = json.dumps({"day": self.day, "used": round(self.used, 6)})
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as exc:
            log.warning(
                "cloudflare budget state file could not be written (%s)",
                exc.__class__.__name__,
            )
            return False
        return True

    async def begin(self) -> bool:
        """Reserve one call. False means do not call Cloudflare."""
        async with self._lock:
            self._roll_day(utc_now())
            if not self._can_call_now():
                return False
            self.used += self.expected
            if not self._persist():
                self._block("budget state file could not be written", persist=True)
                return False
            return True

    async def finish(self, data: dict[str, Any] | None) -> None:
        """Replace the reservation with the measured cost. A failed write spends the day."""
        usage = _usage_blob(data)
        neurons, estimated = neurons_from_usage(usage, self.model)
        if estimated:
            log.debug("cloudflare usage estimated neurons=%s", round(neurons, 3))
        async with self._lock:
            self.used += neurons - self.expected
            if self.used < 0:
                self.used = 0.0
            if not self._persist():
                self._block(
                    "budget state file could not be written after a call",
                    persist=True,
                )

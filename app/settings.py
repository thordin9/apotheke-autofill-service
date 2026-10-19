"""Settings read from environment variables at call time (no Docker assumed)."""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("app.settings")

DEFAULT_MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc


def parse_models(raw: str) -> tuple[str, ...]:
    """ALLOWED_MODELS: comma-separated model tags, e.g.
    "qwen2.5vl-jetson:3b,moondream". Blank entries and repeats are dropped.
    The backend's configured model (OLLAMA_MODEL / ECHO_MODEL) is always
    allowed and is the default, so it does not need to be listed."""
    out: list[str] = []
    for part in raw.split(","):
        if (name := part.strip()) and name not in out:
            out.append(name)
    return tuple(out)


MIN_IMAGE_SIZE = 64
MAX_IMAGE_SIZE = 8192
# One IMAGE_SIZES entry: ASCII digits, optionally one trailing '*'. Stricter
# than int(), which also takes "+448", "4_48" and non-ASCII digits.
_IMAGE_SIZE_ITEM = re.compile(r"\d+\*?", re.ASCII)


def parse_image_sizes(raw: str) -> tuple[tuple[int, bool], ...]:
    """IMAGE_SIZES: comma-separated long-edge presets in pixels, the
    recommended one marked with a trailing '*', e.g. "448,672*,1024".
    Returns (long_edge, recommended) pairs in ascending order. Without a '*'
    the largest size is recommended. Empty means the field is not advertised."""
    sizes: dict[int, bool] = {}
    marked = 0
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not _IMAGE_SIZE_ITEM.fullmatch(part):
            raise RuntimeError(
                f"IMAGE_SIZES entries must be integers (optionally ending in *), got {part!r}"
            )
        star = part.endswith("*")
        size = int(part.rstrip("*"))
        if not MIN_IMAGE_SIZE <= size <= MAX_IMAGE_SIZE:
            raise RuntimeError(
                f"IMAGE_SIZES entries must be {MIN_IMAGE_SIZE}..{MAX_IMAGE_SIZE}, got {size}"
            )
        marked += star
        sizes[size] = sizes.get(size, False) or star
    if marked > 1:
        raise RuntimeError("IMAGE_SIZES may mark only one size as recommended with *")
    if sizes and not any(sizes.values()):
        sizes[max(sizes)] = True
    return tuple(sorted(sizes.items()))


class Secret:
    """A credential that must not appear in logs, errors, or reprs."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __bool__(self) -> bool:
        return bool(self._value)

    def __repr__(self) -> str:
        return "Secret('***')"

    def __str__(self) -> str:
        return "***"


def _positive(name: str, value: float) -> float:
    """Reject NaN, infinities, zero, and negatives. The message does not echo the value."""
    if not math.isfinite(value) or value <= 0:
        raise RuntimeError(f"{name} must be a positive number")
    return value


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise RuntimeError(f"{name} must be true or false")


def _choice(name: str, default: str, allowed: tuple[str, ...]) -> str:
    raw = os.environ.get(name, "").strip().lower() or default
    if raw not in allowed:
        raise RuntimeError(f"{name} must be one of: {', '.join(allowed)}")
    return raw


@dataclass(frozen=True)
class Settings:
    backend: str
    token: str
    max_image_bytes: int
    backend_timeout_s: float
    allowed_models: tuple[str, ...] = ()
    image_sizes: tuple[tuple[int, bool], ...] = ()
    fallback_backend: str = "none"
    failover_on_cold: bool = False
    cf_account_id: Secret = Secret("")
    cf_api_token: Secret = Secret("")
    cf_model: str = "@cf/google/gemma-4-26b-a4b-it"
    cf_timeout_s: float = 20.0
    cf_daily_neuron_budget: float = 6000.0
    cf_budget_state_file: str = "/var/lib/autofill/cf-neuron-budget.json"
    request_deadline_s: float = 45.0
    ollama_warm_reserve_s: float = 15.0
    ollama_cold_reserve_s: float = 35.0


def get_settings() -> Settings:
    from .backends.budget import DEFAULT_BUDGET_PATH

    cf_timeout_s = _positive("CF_TIMEOUT_S", _float("CF_TIMEOUT_S", 20.0))
    budget = _float("CF_DAILY_NEURON_BUDGET", 6000.0)
    if not math.isfinite(budget) or budget < 0:
        raise RuntimeError("CF_DAILY_NEURON_BUDGET must be zero or positive")
    deadline = _positive("REQUEST_DEADLINE_S", _float("REQUEST_DEADLINE_S", 45.0))
    warm_reserve = _positive("OLLAMA_WARM_RESERVE_S", _float("OLLAMA_WARM_RESERVE_S", 15.0))
    cold_reserve = _positive("OLLAMA_COLD_RESERVE_S", _float("OLLAMA_COLD_RESERVE_S", 35.0))
    state_file = os.environ.get("CF_BUDGET_STATE_FILE", "").strip() or DEFAULT_BUDGET_PATH
    return Settings(
        backend=os.environ.get("BACKEND", "cloudflare").strip().lower() or "cloudflare",
        token=os.environ.get("AUTOFILL_TOKEN", "").strip(),
        max_image_bytes=_int("MAX_IMAGE_BYTES", DEFAULT_MAX_IMAGE_BYTES),
        backend_timeout_s=_float("BACKEND_TIMEOUT_S", 45.0),
        allowed_models=parse_models(os.environ.get("ALLOWED_MODELS", "")),
        image_sizes=parse_image_sizes(os.environ.get("IMAGE_SIZES", "")),
        fallback_backend=_choice("FALLBACK_BACKEND", "ollama", ("none", "cloudflare", "ollama")),
        failover_on_cold=_flag("FAILOVER_ON_COLD", False),
        cf_account_id=_credential("CF_ACCOUNT_ID", "CF_ACCOUNT_ID_FILE"),
        cf_api_token=_credential("CF_API_TOKEN", "CF_API_TOKEN_FILE"),
        cf_model=os.environ.get("CF_MODEL", "").strip() or "@cf/google/gemma-4-26b-a4b-it",
        cf_timeout_s=cf_timeout_s,
        cf_daily_neuron_budget=budget,
        cf_budget_state_file=state_file,
        request_deadline_s=deadline,
        ollama_warm_reserve_s=warm_reserve,
        ollama_cold_reserve_s=cold_reserve,
    )


def _credential(env_name: str, file_name: str) -> Secret:
    """Read a secret from the environment, or from a file path in another variable.

    A direct value wins. A missing, empty, or unreadable file is an empty
    secret. This does not raise and does not log the path or the contents.
    """
    direct = os.environ.get(env_name, "").strip()
    if direct:
        return Secret(direct)
    path = os.environ.get(file_name, "").strip()
    if not path:
        return Secret("")
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return Secret("")
    return Secret(text.strip())


def cloudflare_configured(settings: Settings) -> bool:
    """True when both Cloudflare credentials are present. Values are not logged."""
    return bool(settings.cf_account_id and settings.cf_api_token)


def validate_runtime(settings: Settings, backend_name: str) -> None:
    """Check the Cloudflare pair.

    Missing credentials do not crash when Ollama can answer. One warning names
    the missing variables and never includes their values. A Cloudflare primary
    with no Ollama fallback still refuses to start without credentials.
    """
    # Imported lazily: the cloudflare module imports settings.
    from .backends.cloudflare import model_rejected

    fallback = settings.fallback_backend
    needs_cf = backend_name == "cloudflare" or fallback == "cloudflare"
    if fallback == "cloudflare" and backend_name != "ollama":
        raise RuntimeError("FALLBACK_BACKEND=cloudflare requires BACKEND=ollama")
    if fallback == "ollama" and backend_name != "cloudflare":
        raise RuntimeError("FALLBACK_BACKEND=ollama requires BACKEND=cloudflare")
    configured = cloudflare_configured(settings)
    # A zero budget never calls Cloudflare, so credentials are not required.
    if needs_cf and settings.cf_daily_neuron_budget > 0 and not configured:
        if (backend_name == "cloudflare" and fallback == "ollama") or (
            backend_name == "ollama" and fallback == "cloudflare"
        ):
            log.warning(
                "Cloudflare is not configured: CF_ACCOUNT_ID or CF_API_TOKEN is unset "
                "or its file is missing or unreadable; serving from Ollama"
            )
        else:
            raise RuntimeError(
                "CF_ACCOUNT_ID and CF_API_TOKEN must be set when cloudflare is "
                "the primary backend or the fallback"
            )
    if needs_cf and configured and model_rejected(settings.cf_model):
        raise RuntimeError(
            "CF_MODEL is not allowed: Llama 3.2 Vision and Llama 4 Scout are rejected"
        )
    if backend_name == "cloudflare" and fallback == "ollama":
        _check_cf_ollama_deadline(settings)


def _check_cf_ollama_deadline(settings: Settings) -> None:
    """Refuse a Cloudflare-primary config that cannot finish before the client.

    The check runs only for BACKEND=cloudflare with FALLBACK_BACKEND=ollama.
    REQUEST_DEADLINE_S must be under Apotheke's 50 s client timeout and must
    fit in BACKEND_TIMEOUT_S. Each Ollama reserve must leave at least 5 s for
    the Cloudflare slice.
    """
    from .backends.failover import APOTHEKE_CLIENT_TIMEOUT_S, MIN_CF_SLICE_S

    deadline = settings.request_deadline_s
    if deadline >= APOTHEKE_CLIENT_TIMEOUT_S:
        raise RuntimeError("REQUEST_DEADLINE_S must be under Apotheke's 50 s client timeout")
    if deadline > settings.backend_timeout_s:
        raise RuntimeError(
            "REQUEST_DEADLINE_S must fit within BACKEND_TIMEOUT_S so the "
            "Cloudflare slice, a repair, and the Ollama fallback finish "
            "inside the service timeout"
        )
    for name, reserve in (
        ("OLLAMA_WARM_RESERVE_S", settings.ollama_warm_reserve_s),
        ("OLLAMA_COLD_RESERVE_S", settings.ollama_cold_reserve_s),
    ):
        if deadline - reserve < MIN_CF_SLICE_S:
            raise RuntimeError(
                f"{name} leaves less than {int(MIN_CF_SLICE_S)} s for Cloudflare "
                "inside REQUEST_DEADLINE_S. Lower the reserve or raise "
                "REQUEST_DEADLINE_S, and keep REQUEST_DEADLINE_S under 50 s"
            )

"""Model selection and cold-start admission shared by the backends.

A client may ask for a model with `context.model`. It is used when it is the
backend's default model, or listed in ALLOWED_MODELS and installed; anything
else falls back to the default. The response's `model_used` says which.
"""

from __future__ import annotations

from typing import Any

# A request may only wait for something when it fits in this share of
# BACKEND_TIMEOUT_S; the rest is headroom for the upload and the answer.
TIMEOUT_SHARE = 0.9


def model_key(name: str) -> str:
    """Comparison key: Ollama reports "moondream:latest" for "moondream"."""
    name = name.strip()
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def same_model(a: str, b: str) -> bool:
    return model_key(a) == model_key(b)


def allowlist(default: str, allowed: tuple[str, ...] | list[str]) -> list[str]:
    """The default model first, then the other ALLOWED_MODELS in their order."""
    out = [default]
    for m in allowed:
        if not any(same_model(m, o) for o in out):
            out.append(m)
    return out


def resolve_model(
    requested: Any, default: str, allowed: list[str], installed: set[str] | None
) -> str:
    """The model to run: `requested` in its allowlist spelling when it is
    allowlisted and installed (installed holds model_key()s; None means
    unknown, which counts as not installed), else `default`."""
    if not isinstance(requested, str) or not requested.strip():
        return default
    if same_model(requested, default):
        return default
    for m in allowed:
        if same_model(m, requested):
            return m if installed is not None and model_key(m) in installed else default
    return default


def cold_wait_fits(load_s: float, generate_s: float, timeout_s: float) -> bool:
    """Whether loading a model and then answering fits the timeout budget.
    When it does not, the request gets a fast 503 + Retry-After instead of
    running into a 504."""
    return load_s + generate_s <= timeout_s * TIMEOUT_SHARE

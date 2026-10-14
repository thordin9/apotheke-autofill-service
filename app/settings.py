"""Settings read from environment variables at call time (no Docker assumed)."""

from __future__ import annotations

import os
from dataclasses import dataclass

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


@dataclass(frozen=True)
class Settings:
    backend: str
    token: str
    max_image_bytes: int
    backend_timeout_s: float


def get_settings() -> Settings:
    return Settings(
        backend=os.environ.get("BACKEND", "echo").strip().lower() or "echo",
        token=os.environ.get("AUTOFILL_TOKEN", "").strip(),
        max_image_bytes=_int("MAX_IMAGE_BYTES", DEFAULT_MAX_IMAGE_BYTES),
        backend_timeout_s=_float("BACKEND_TIMEOUT_S", 25.0),
    )

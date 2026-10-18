"""Settings read from environment variables at call time (no Docker assumed)."""

from __future__ import annotations

import os
import re
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


@dataclass(frozen=True)
class Settings:
    backend: str
    token: str
    max_image_bytes: int
    backend_timeout_s: float
    allowed_models: tuple[str, ...] = ()
    image_sizes: tuple[tuple[int, bool], ...] = ()


def get_settings() -> Settings:
    return Settings(
        backend=os.environ.get("BACKEND", "echo").strip().lower() or "echo",
        token=os.environ.get("AUTOFILL_TOKEN", "").strip(),
        max_image_bytes=_int("MAX_IMAGE_BYTES", DEFAULT_MAX_IMAGE_BYTES),
        backend_timeout_s=_float("BACKEND_TIMEOUT_S", 25.0),
        allowed_models=parse_models(os.environ.get("ALLOWED_MODELS", "")),
        image_sizes=parse_image_sizes(os.environ.get("IMAGE_SIZES", "")),
    )

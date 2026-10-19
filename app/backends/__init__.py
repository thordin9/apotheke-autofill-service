from __future__ import annotations

from collections.abc import Callable

from ..settings import get_settings, validate_runtime
from .base import Backend, BackendUnavailable
from .cloudflare import CloudflareBackend
from .echo import EchoBackend
from .failover import FailoverBackend
from .ollama import OllamaBackend
from .placeholder import NotImplementedBackend

REGISTRY: dict[str, Callable[[], Backend]] = {
    "echo": EchoBackend,
    "ollama": OllamaBackend,
    "cloudflare": CloudflareBackend,
    "clip": lambda: NotImplementedBackend("clip"),
}


def create_backend(name: str) -> Backend:
    settings = get_settings()
    validate_runtime(settings, name)
    try:
        backend = REGISTRY[name]()
    except KeyError:
        raise RuntimeError(
            f"Unknown BACKEND={name!r}; choose one of: {', '.join(sorted(REGISTRY))}"
        ) from None
    if settings.fallback_backend == "cloudflare":
        return FailoverBackend(backend, CloudflareBackend())
    if settings.fallback_backend == "ollama":
        return FailoverBackend(backend, OllamaBackend())
    return backend


__all__ = ["Backend", "BackendUnavailable", "REGISTRY", "create_backend"]

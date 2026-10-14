from __future__ import annotations

from collections.abc import Callable

from .base import Backend, BackendUnavailable
from .echo import EchoBackend
from .placeholder import NotImplementedBackend

REGISTRY: dict[str, Callable[[], Backend]] = {
    "echo": EchoBackend,
    "ollama": lambda: NotImplementedBackend("ollama"),
    "clip": lambda: NotImplementedBackend("clip"),
}


def create_backend(name: str) -> Backend:
    try:
        return REGISTRY[name]()
    except KeyError:
        raise RuntimeError(
            f"Unknown BACKEND={name!r}; choose one of: {', '.join(sorted(REGISTRY))}"
        ) from None


__all__ = ["Backend", "BackendUnavailable", "REGISTRY", "create_backend"]

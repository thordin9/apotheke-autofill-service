from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

CAPABILITIES = ("name", "category", "tags", "description")


class BackendUnavailable(Exception):
    """Backend cannot serve requests (not implemented, not ready, upstream down)."""


class Backend(ABC):
    """Adapter interface every backend implements.

    `info()` returns at least {"name", "capabilities"}; the app adds version,
    backend id and max_image_bytes. `suggest()` returns a dict shaped like the
    Suggestion schema (all keys optional). Raise BackendUnavailable on failure.
    """

    id: str = "base"

    @abstractmethod
    async def info(self) -> dict[str, Any]: ...

    @abstractmethod
    async def suggest(self, image_bytes: bytes, mime: str, context: dict[str, Any]) -> Any: ...

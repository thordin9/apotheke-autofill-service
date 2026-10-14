from __future__ import annotations

from typing import Any

from .base import Backend, BackendUnavailable


class NotImplementedBackend(Backend):
    """Registered but not built yet: the service starts, /healthz is 200,
    and /v1/info + /v1/suggest return 503 backend_unavailable."""

    def __init__(self, backend_id: str):
        self.id = backend_id

    def _fail(self):
        raise BackendUnavailable(f"backend '{self.id}' is not implemented yet")

    async def info(self) -> dict[str, Any]:
        self._fail()

    async def suggest(self, image_bytes: bytes, mime: str, context: dict[str, Any]) -> Any:
        self._fail()

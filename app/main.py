"""Apotheke autofill service. Run: uvicorn app.main:app --port 8080"""

from __future__ import annotations

import asyncio
import hmac
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from .backends import BackendUnavailable, create_backend
from .backends.base import CAPABILITIES
from .backends.echo import MalformedOutput
from .errors import ApiError, error_response
from .imagetype import accepted_mime
from .settings import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    backend = create_backend(settings.backend)  # unknown BACKEND fails at startup

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if hasattr(backend, "start"):
            backend.start()  # background warm-up; never blocks startup
        yield

    app = FastAPI(
        title="Apotheke Autofill Service", version="1", docs_url="/docs", lifespan=lifespan
    )
    app.state.backend = backend

    @app.middleware("http")
    async def bearer_auth(request: Request, call_next):
        token = get_settings().token
        if token and request.url.path != "/healthz":
            header = request.headers.get("authorization", "")
            scheme, _, given = header.partition(" ")
            if scheme.lower() != "bearer" or not hmac.compare_digest(
                given.strip().encode(), token.encode()
            ):
                return error_response(
                    401,
                    "unauthorized",
                    "missing or invalid bearer token",
                    {"WWW-Authenticate": "Bearer"},
                )
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return error_response(exc.status, exc.code, exc.message, exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException):
        return error_response(exc.status_code, "bad_request", str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        return error_response(400, "bad_request", "invalid request")

    async def call_backend(coro):
        try:
            return await asyncio.wait_for(coro, timeout=get_settings().backend_timeout_s)
        except TimeoutError:
            raise ApiError(504, "backend_timeout", "backend timed out") from None
        except BackendUnavailable as exc:
            code = getattr(exc, "code", "backend_unavailable")
            raise ApiError(503, code, str(exc), getattr(exc, "headers", None)) from None

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    @app.get("/v1/info")
    async def info():
        data = await call_backend(backend.info())
        caps = [c for c in data.get("capabilities", []) if c in CAPABILITIES]
        out = {
            "name": str(data.get("name", backend.id)),
            "version": "1",
            "capabilities": list(dict.fromkeys(caps)),
            "backend": backend.id,
            "max_image_bytes": get_settings().max_image_bytes,
        }
        if isinstance(data.get("ready"), bool):
            out["ready"] = data["ready"]
        latency = data.get("avg_latency_ms")
        if isinstance(latency, int) and not isinstance(latency, bool) and latency >= 0:
            out["avg_latency_ms"] = latency
        return out

    @app.post("/v1/suggest")
    async def suggest(request: Request):
        s = get_settings()
        try:
            form = await request.form(max_part_size=s.max_image_bytes + 1024 * 1024)
        except Exception:
            raise ApiError(400, "bad_request", "expected multipart/form-data") from None
        image = form.get("image")
        if not isinstance(image, UploadFile):
            raise ApiError(400, "bad_request", "missing 'image' file part")
        data = await image.read(s.max_image_bytes + 1)
        if not data:
            raise ApiError(400, "bad_request", "'image' is empty")
        if len(data) > s.max_image_bytes:
            raise ApiError(413, "payload_too_large", f"image exceeds {s.max_image_bytes} bytes")
        mime = accepted_mime(data)
        if mime is None:
            raise ApiError(415, "unsupported_media_type", "image must be JPEG, PNG or WebP")
        context = parse_context(form.get("context"))
        try:
            result = await call_backend(backend.suggest(data, mime, context))
        except MalformedOutput:
            return Response("{not json", media_type="application/json")
        return JSONResponse(sanitize(result))

    return app


def parse_context(raw: Any) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, UploadFile):
        raise ApiError(400, "bad_request", "'context' must be a JSON string field")
    try:
        ctx = json.loads(raw)
    except ValueError:
        raise ApiError(400, "bad_request", "'context' is not valid JSON") from None
    if not isinstance(ctx, dict):
        raise ApiError(400, "bad_request", "'context' must be a JSON object")
    out: dict[str, Any] = {}
    for key in ("categories", "tags"):
        val = ctx.get(key, [])
        if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
            raise ApiError(400, "bad_request", f"'context.{key}' must be a list of strings")
        out[key] = val
    locale = ctx.get("locale")
    if locale is not None:
        if not isinstance(locale, str):
            raise ApiError(400, "bad_request", "'context.locale' must be a string")
        out["locale"] = locale
    return out


def _scored(v: Any) -> dict[str, Any] | None:
    if not isinstance(v, dict) or not isinstance(v.get("value"), str) or not v["value"].strip():
        return None
    out: dict[str, Any] = {"value": v["value"].strip()}
    c = v.get("confidence")
    if isinstance(c, int | float) and not isinstance(c, bool):
        out["confidence"] = min(1.0, max(0.0, float(c)))
    return out


def sanitize(result: Any) -> dict[str, Any]:
    """Drop anything a backend returns that doesn't match the contract."""
    if not isinstance(result, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("name", "category", "description"):
        if (v := _scored(result.get(key))) is not None:
            out[key] = v
    if isinstance(result.get("tags"), list):
        tags = [t for t in map(_scored, result["tags"]) if t is not None]
        if tags:
            out["tags"] = tags
    return out


app = create_app()

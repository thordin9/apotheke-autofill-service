from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SPEC = yaml.safe_load((Path(__file__).parents[1] / "openapi.yaml").read_text())

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 60
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 60
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 60
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 60
GIF = b"GIF89a" + b"\x00" * 60

_ENV = [
    "BACKEND",
    "AUTOFILL_TOKEN",
    "MAX_IMAGE_BYTES",
    "BACKEND_TIMEOUT_S",
    "ECHO_MODE",
    "ECHO_DELAY_MS",
    "OLLAMA_URL",
    "OLLAMA_MODEL",
    "OLLAMA_QUEUE_SIZE",
    "OLLAMA_RETRY_AFTER_S",
    "OLLAMA_WARMUP",
    "OLLAMA_NUM_PREDICT",
    "OLLAMA_TEMPERATURE",
    "OLLAMA_KEEP_ALIVE",
    "OLLAMA_JSON_FORMAT",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def make_client(monkeypatch):
    def _make(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        from app.main import create_app

        return TestClient(create_app())

    return _make


@pytest.fixture
def client(make_client):
    return make_client()


_registry = Registry().with_resource(
    "urn:spec", Resource.from_contents(SPEC, default_specification=DRAFT202012)
)


def _schema_for(path: str, method: str, status: int) -> dict:
    resp = SPEC["paths"][path][method]["responses"][str(status)]
    if "$ref" in resp:
        resp = SPEC["components"]["responses"][resp["$ref"].rsplit("/", 1)[-1]]
    schema = resp["content"]["application/json"]["schema"]
    return json.loads(json.dumps(schema).replace('"#/', '"urn:spec#/'))


def assert_contract(resp, path: str, method: str = "get") -> dict:
    """Validate status + body against openapi.yaml."""
    responses = SPEC["paths"][path][method]["responses"]
    assert str(resp.status_code) in responses, f"{resp.status_code} not documented"
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    Draft202012Validator(_schema_for(path, method, resp.status_code), registry=_registry).validate(
        body
    )
    return body


def post_image(client, data=JPEG, context=None, headers=None, filename="x.jpg", ctype=None):
    files = {"image": (filename, data, ctype or "image/jpeg")}
    form = {} if context is None else {"context": context}
    return client.post("/v1/suggest", files=files, data=form, headers=headers or {})

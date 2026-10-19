"""Echo backend behaviour and ECHO_* env knobs."""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from conftest import JPEG, PNG, post_image

from app.backends import REGISTRY, create_backend
from app.backends.base import Backend
from app.backends.echo import EchoBackend
from app.main import sanitize


def test_registry():
    assert set(REGISTRY) == {"echo", "ollama", "clip"}
    assert all(isinstance(create_backend(n), Backend) for n in REGISTRY)


def test_backend_is_abstract():
    with pytest.raises(TypeError):
        Backend()


def test_prefers_context(client):
    ctx = {"categories": ["Tools", "Kitchen"], "tags": ["bosch", "red", "18v", "spare"]}
    body = post_image(client, context=json.dumps(ctx)).json()
    assert body["category"]["value"] == "Tools"
    assert [t["value"] for t in body["tags"]] == ["bosch", "red", "18v"]


def test_defaults_without_context(client):
    body = post_image(client).json()
    assert body["category"]["value"] == "Misc"
    assert [t["value"] for t in body["tags"]] == ["echo", "sample"]


def test_deterministic_and_image_dependent(client):
    a, b = post_image(client).json(), post_image(client).json()
    assert a == b
    assert a["description"]["value"] == f"JPEG image, {len(JPEG)} bytes"
    c = post_image(client, PNG).json()
    assert c["name"]["value"] != a["name"]["value"]
    assert c["description"]["value"] == f"PNG image, {len(PNG)} bytes"


def test_ok_mode_explicit(make_client):
    assert post_image(make_client(ECHO_MODE="ok")).status_code == 200


def test_mode_read_per_request(client, monkeypatch):
    assert post_image(client).status_code == 200
    monkeypatch.setenv("ECHO_MODE", "error")
    assert post_image(client).status_code == 503


def test_malformed_mode(make_client):
    r = post_image(make_client(ECHO_MODE="malformed"))
    assert r.status_code == 200
    with pytest.raises(ValueError):
        json.loads(r.text)


def test_delay_ms(make_client):
    c = make_client(ECHO_DELAY_MS=300)
    t = time.monotonic()
    assert post_image(c).status_code == 200
    assert time.monotonic() - t >= 0.3


def test_bad_delay_value_ignored(make_client):
    assert post_image(make_client(ECHO_DELAY_MS="abc")).status_code == 200


def test_echo_info():
    info = asyncio.run(EchoBackend().info())
    assert info["name"] == "echo"


def test_sanitize_drops_bad_fields():
    out = sanitize(
        {
            "name": {"value": " Drill ", "confidence": 7},
            "category": {"value": ""},
            "description": "plain string",
            "tags": [{"value": "a", "confidence": -1}, {"nope": 1}, "x"],
            "extra": 1,
        }
    )
    assert out == {
        "name": {"value": "Drill", "confidence": 1.0},
        "tags": [{"value": "a", "confidence": 0.0}],
        "brand": "",
        "model_number": "",
        "serial_number": "",
    }
    assert sanitize(["not", "a", "dict"]) == {}


def test_bad_settings_env(make_client):
    with pytest.raises(RuntimeError, match="MAX_IMAGE_BYTES"):
        make_client(MAX_IMAGE_BYTES="ten")


def test_identity_missing_null_empty_long_and_unusual(make_client, monkeypatch):
    weird = "\x01\x07" + "עברית" + "\u200f" + "🔧" + '"quoted"'
    c = make_client(
        ECHO_BRAND="",
        ECHO_MODEL_NUMBER="json:null",
        ECHO_SERIAL_NUMBER=" " + ("m" * 200) + " ",
    )
    body = post_image(c).json()
    assert body["brand"] == ""
    assert body["model_number"] == ""
    assert body["serial_number"] == "m" * 128
    for key in ("ECHO_BRAND", "ECHO_MODEL_NUMBER", "ECHO_SERIAL_NUMBER"):
        monkeypatch.delenv(key, raising=False)
    bare = post_image(make_client()).json()
    assert bare["brand"] == bare["model_number"] == bare["serial_number"] == ""
    nested = post_image(
        make_client(
            ECHO_BRAND='json:{"value":"  Bosch  "}',
            ECHO_MODEL_NUMBER=weird,
            ECHO_SERIAL_NUMBER="json:null",
        )
    ).json()
    assert nested["brand"] == "Bosch"
    assert nested["model_number"] == weird[2:]  # C0 controls dropped, RLM kept
    assert nested["serial_number"] == ""


def test_echo_state_warm_when_idle_and_loading_during_a_switch(make_client):
    c = make_client(ALLOWED_MODELS="moondream")
    info = c.get("/v1/info").json()
    health = c.get("/healthz")
    assert health.status_code == 200 and health.json() == {"status": "ok"}

    def states(body):
        return {m["id"]: m["state"] for m in body["models"]}

    # The default is already resident, so it is warm before the first suggest.
    assert states(info) == {"echo": "warm", "moondream": "cold"}
    assert info["models"][0]["loaded"] is True
    post_image(c)
    assert states(c.get("/v1/info").json())["echo"] == "warm"
    # A switch still loading stays "loading" until that suggest finishes.
    slow = make_client(ALLOWED_MODELS="moondream", ECHO_LOAD_MS="5000", BACKEND_TIMEOUT_S="2")
    assert post_image(slow, context=json.dumps({"model": "moondream"})).status_code == 503
    during = {m["id"]: m for m in slow.get("/v1/info").json()["models"]}
    assert during["moondream"]["state"] == "loading" and during["moondream"]["loaded"] is False
    assert during["echo"]["state"] == "cold"
    assert slow.get("/healthz").json() == {"status": "ok"}
    slow.app.state.backend._ready_at = 0.0
    assert post_image(slow, context=json.dumps({"model": "moondream"})).status_code == 200
    assert states(slow.get("/v1/info").json())["moondream"] == "warm"

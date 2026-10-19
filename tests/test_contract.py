"""Every documented status code, validated against openapi.yaml."""

from __future__ import annotations

import json
import logging
import unicodedata

import pytest
from conftest import GIF, HEIC, JPEG, PNG, WEBP, assert_contract, post_image

CTX = json.dumps({"categories": ["Tools", "Kitchen"], "tags": ["bosch", "red", "18v", "x"]})


def test_healthz(client):
    assert assert_contract(client.get("/healthz"), "/healthz") == {"status": "ok"}


def test_info(client):
    body = assert_contract(client.get("/v1/info"), "/v1/info")
    assert body["version"] == "1"
    assert set(body["capabilities"]) == {"name", "category", "tags", "description"}
    assert body["backend"] == "echo"
    assert body["max_image_bytes"] == 10 * 1024 * 1024
    assert "avg_latency_ms" not in body  # echo without ECHO_DELAY_MS


def test_info_echo_reports_delay_as_latency(make_client):
    body = assert_contract(make_client(ECHO_DELAY_MS="1200").get("/v1/info"), "/v1/info")
    assert body["avg_latency_ms"] == 1200


@pytest.mark.parametrize("delay", ["0", "-5", "abc"])
def test_info_echo_omits_latency_without_delay(make_client, delay):
    assert "avg_latency_ms" not in make_client(ECHO_DELAY_MS=delay).get("/v1/info").json()


def _info_errors(body):
    from conftest import _registry, _schema_for
    from jsonschema import Draft202012Validator

    schema = _schema_for("/v1/info", "get", 200)
    return list(Draft202012Validator(schema, registry=_registry).iter_errors(body))


@pytest.mark.parametrize("value", [0, 1, 13540])
def test_info_schema_accepts_latency(value):
    base = {"name": "x", "version": "1", "capabilities": ["name"]}
    assert not _info_errors(base)  # absent is valid
    assert not _info_errors({**base, "avg_latency_ms": value})


@pytest.mark.parametrize("value", [-1, 1.5, "1000", None, True])
def test_info_schema_rejects_bad_latency(value):
    base = {"name": "x", "version": "1", "capabilities": ["name"]}
    assert _info_errors({**base, "avg_latency_ms": value})


@pytest.mark.parametrize("value", [-1, 1.5, "1000", None, True])
def test_info_drops_bad_latency_from_backend(make_client, monkeypatch, value):
    from app.backends.echo import EchoBackend

    async def info(self):
        return {"name": "echo", "capabilities": ["name"], "avg_latency_ms": value}

    monkeypatch.setattr(EchoBackend, "info", info)
    body = assert_contract(make_client().get("/v1/info"), "/v1/info")
    assert "avg_latency_ms" not in body


@pytest.mark.parametrize("data", [JPEG, PNG, WEBP])
def test_suggest_ok(client, data):
    assert_contract(post_image(client, data, CTX), "/v1/suggest", "post")


def test_suggest_ok_without_context(client):
    assert_contract(post_image(client), "/v1/suggest", "post")


def test_400_missing_image(client):
    r = client.post("/v1/suggest", data={"context": "{}"})
    assert r.status_code == 400
    assert assert_contract(r, "/v1/suggest", "post")["error"]["code"] == "bad_request"


def test_400_not_multipart(client):
    r = client.post("/v1/suggest", json={"image": "x"})
    assert r.status_code == 400
    assert_contract(r, "/v1/suggest", "post")


def test_400_empty_image(client):
    r = post_image(client, b"")
    assert r.status_code == 400
    assert_contract(r, "/v1/suggest", "post")


@pytest.mark.parametrize(
    "ctx", ["{not json", "[1,2]", '{"categories":"Tools"}', '{"tags":[1]}', '{"locale":5}']
)
def test_400_bad_context(client, ctx):
    r = post_image(client, context=ctx)
    assert r.status_code == 400
    assert_contract(r, "/v1/suggest", "post")


# A lone surrogate is valid JSON (\uD800) and cannot be encoded as UTF-8.
_SURROGATE = "\ud800"


@pytest.mark.parametrize(
    "ctx",
    [
        {"categories": [f"Tools{_SURROGATE}"]},
        {"categories": [_SURROGATE]},
        {"tags": [f"red{_SURROGATE}"]},
        {"locale": f"en{_SURROGATE}"},
        {"model": f"moondream{_SURROGATE}"},
    ],
)
def test_unencodable_context_is_400(client, caplog, ctx):
    with caplog.at_level(logging.DEBUG):
        r = post_image(client, context=json.dumps(ctx))
    assert r.status_code == 400
    body = assert_contract(r, "/v1/suggest", "post")
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "bad_request"
    assert _SURROGATE not in r.text
    assert "Traceback" not in caplog.text


def test_category_controls_never_come_back(client):
    raw = "Too\x00ls\x07\u0085\u202e\u2066"
    r = post_image(client, context=json.dumps({"categories": [raw], "tags": ["ok"]}))
    assert r.status_code == 200
    body = assert_contract(r, "/v1/suggest", "post")
    assert body["category"]["value"] == "Tools"
    text = r.content.decode("utf-8")
    assert "\x00" not in text
    assert "\u202e" not in text and "\u2066" not in text
    assert all(unicodedata.category(ch) != "Cc" for ch in text)


def test_category_of_only_controls_is_not_returned(client):
    r = post_image(client, context=json.dumps({"categories": ["\x00\u0007\u202e"]}))
    assert r.status_code == 200
    body = assert_contract(r, "/v1/suggest", "post")
    assert body["category"]["value"] == "Misc"
    assert "\x00" not in r.content.decode("utf-8")


@pytest.mark.parametrize(
    "headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic s3cret"}]
)
@pytest.mark.parametrize("path,method", [("/v1/info", "get"), ("/v1/suggest", "post")])
def test_401_auth_on(make_client, headers, path, method):
    c = make_client(AUTOFILL_TOKEN="s3cret")
    r = c.get(path, headers=headers) if method == "get" else post_image(c, headers=headers)
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"
    assert assert_contract(r, path, method)["error"]["code"] == "unauthorized"


def test_auth_on_valid_token(make_client):
    c = make_client(AUTOFILL_TOKEN="s3cret")
    h = {"Authorization": "Bearer s3cret"}
    assert assert_contract(c.get("/v1/info", headers=h), "/v1/info")["version"] == "1"
    r = post_image(c, headers=h)
    assert r.status_code == 200
    assert_contract(r, "/v1/suggest", "post")


def test_auth_is_service_wide_but_skips_healthz(make_client):
    c = make_client(AUTOFILL_TOKEN="s3cret")
    assert c.get("/healthz").status_code == 200
    assert c.get("/does-not-exist").status_code == 401
    assert c.get("/openapi.json").status_code == 401


def test_auth_off_ignores_header(client):
    r = client.get("/v1/info", headers={"Authorization": "Bearer anything"})
    assert r.status_code == 200


def test_auth_checked_before_validation(make_client):
    r = make_client(AUTOFILL_TOKEN="t").post("/v1/suggest")
    assert r.status_code == 401


def test_413_default_limit(client):
    r = post_image(client, JPEG + b"\x00" * (10 * 1024 * 1024))
    assert r.status_code == 413
    assert assert_contract(r, "/v1/suggest", "post")["error"]["code"] == "payload_too_large"


def test_413_env_limit(make_client):
    c = make_client(MAX_IMAGE_BYTES=100)
    assert post_image(c, JPEG).status_code == 200
    r = post_image(c, JPEG + b"\x00" * 100)
    assert r.status_code == 413
    assert_contract(r, "/v1/suggest", "post")
    assert c.get("/v1/info").json()["max_image_bytes"] == 100


@pytest.mark.parametrize("data", [GIF, b"hello world, not an image"])
def test_415_unsupported(client, data):
    r = post_image(client, data)
    assert r.status_code == 415
    assert_contract(r, "/v1/suggest", "post")


def test_415_ignores_spoofed_content_type(client):
    r = post_image(client, GIF, ctype="image/jpeg", filename="evil.jpg")
    assert r.status_code == 415


def test_sniffs_bytes_not_header(client):
    r = post_image(client, PNG, ctype="application/octet-stream", filename="blob")
    assert r.status_code == 200
    assert r.json()["description"]["value"].startswith("PNG")


def test_heic_415_without_pillow_heif(client, monkeypatch):
    monkeypatch.setattr("app.imagetype.heif_supported", lambda: False)
    r = post_image(client, HEIC, ctype="image/heic")
    assert r.status_code == 415
    assert_contract(r, "/v1/suggest", "post")


def test_heic_accepted_with_pillow_heif(client, monkeypatch):
    monkeypatch.setattr("app.imagetype.heif_supported", lambda: True)
    r = post_image(client, HEIC, ctype="image/heic")
    assert r.status_code == 200
    assert_contract(r, "/v1/suggest", "post")


@pytest.mark.parametrize("backend", ["clip"])
def test_503_placeholder_backends(make_client, backend):
    c = make_client(BACKEND=backend)
    assert c.get("/healthz").status_code == 200
    for r, path, m in [
        (c.get("/v1/info"), "/v1/info", "get"),
        (post_image(c), "/v1/suggest", "post"),
    ]:
        assert r.status_code == 503
        body = assert_contract(r, path, m)
        assert body["error"]["code"] == "backend_unavailable"
        assert "not implemented" in body["error"]["message"]


def test_unknown_backend_fails_at_startup(make_client):
    with pytest.raises(RuntimeError, match="Unknown BACKEND"):
        make_client(BACKEND="nope")


def test_503_echo_error(make_client):
    r = post_image(make_client(ECHO_MODE="error"))
    assert r.status_code == 503
    assert_contract(r, "/v1/suggest", "post")


def test_504_echo_slow(make_client):
    r = post_image(make_client(ECHO_MODE="slow", BACKEND_TIMEOUT_S="0.2"))
    assert r.status_code == 504
    assert assert_contract(r, "/v1/suggest", "post")["error"]["code"] == "backend_timeout"


def test_504_echo_delay_over_timeout(make_client):
    r = post_image(make_client(ECHO_DELAY_MS=500, BACKEND_TIMEOUT_S="0.1"))
    assert r.status_code == 504

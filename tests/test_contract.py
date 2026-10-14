"""Every documented status code, validated against openapi.yaml."""

from __future__ import annotations

import json

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


@pytest.mark.parametrize("backend", ["ollama", "clip"])
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

"""Cloudflare Workers AI backend and Ollama failover. No live Cloudflare calls."""

from __future__ import annotations

import base64
import io
import json
import logging
import threading
import time
from pathlib import Path

import httpx
import pytest
import respx
from conftest import GIF, JPEG, assert_contract, post_image
from ollama_mock import FakeOllama
from PIL import Image

from app.backends import REGISTRY, create_backend
from app.backends.cloudflare import (
    CF_IMAGE_MAX_BYTES,
    CF_LONG_EDGE,
    DEFAULT_MODEL,
    GUIDED_JSON_MODELS,
    RESPONSE_FORMAT_MODELS,
    CloudflareBackend,
    encode_for_cloudflare,
    model_rejected,
    structured_mode,
)
from app.backends.failover import BOTH_FAILED, attempt_budgets, client_budget_s, usable_budget_s
from app.backends.ollama import build_schema
from app.identity import clean_identity
from app.settings import get_settings

ACCOUNT = "acct-sentinel-9f3a-not-real"
TOKEN = "tok-sentinel-aa11-not-real"
MODEL = DEFAULT_MODEL
QWEN = "@cf/qwen/qwen3.8-27b"
MISTRAL = "@cf/mistralai/mistral-small-3.1-24b-instruct"
LLAMA_32 = "@cf/meta/llama-3.2-11b-vision-instruct"
LLAMA_4 = "@cf/meta/llama-4-scout-17b-16e-instruct"
UNLISTED = "@cf/example/unlisted-vision"
AUP_QUOTE = (
    "With respect to any multimodal models included in Llama 3.2, the rights granted "
    "under Section 1(a) of the Llama 3.2 Community License Agreement are not being "
    "granted to you if you are an individual domiciled in, or a company with a "
    "principal place of business in, the European Union."
)
DOC = Path(__file__).parents[1] / "docs" / "cloudflare-backend.md"

VALID = {
    "name": "Cordless drill",
    "category": "Tools",
    "tags": ["red"],
    "description": "A red cordless drill on a bench",
    "confidence": 0.8,
    "brand": "Acme",
    "model_number": "X1",
    "serial_number": "S9",
}


def _jpeg(width: int, height: int, color: tuple[int, int, int] = (20, 40, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (12, 80, 40)).save(buf, format="PNG")
    return buf.getvalue()


PHOTO = _jpeg(48, 32)
WIDE = _jpeg(1000, 400)
PNG_PHOTO = _png(800, 200)


def _cf_env(**extra: str) -> dict[str, str]:
    env = {
        "CF_ACCOUNT_ID": ACCOUNT,
        "CF_API_TOKEN": TOKEN,
        "CF_MODEL": MODEL,
        "CF_TIMEOUT_S": "5",
    }
    env.update(extra)
    return env


def _envelope(text: str, usage: dict | None = None) -> dict:
    result: dict = {"response": text}
    if usage:
        result["usage"] = usage
    return {"success": True, "result": result}


def _ok(text: str | None = None, usage: dict | None = None) -> httpx.Response:
    return httpx.Response(200, json=_envelope(json.dumps(VALID) if text is None else text, usage))


def _paid(code: int | str = 5035) -> httpx.Response:
    return httpx.Response(
        403,
        json={
            "success": False,
            "errors": [{"code": code, "message": f"plan-detail-marker {TOKEN} {ACCOUNT}"}],
            "result": {"usage": {"neurons": 7777}},
        },
    )


def _body(call) -> dict:
    return json.loads(call.request.content)


def _jpeg_from(call) -> Image.Image:
    content = _body(call)["messages"][0]["content"]
    image = next(part for part in content if part["type"] == "image_url")
    url = image["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")
    raw = base64.b64decode(url.split(",", 1)[1])
    assert raw.startswith(b"\xff\xd8\xff")
    assert len(raw) <= CF_IMAGE_MAX_BYTES
    img = Image.open(io.BytesIO(raw))
    img.load()
    return img


def _secrets_absent(text: str) -> None:
    assert TOKEN not in text
    assert ACCOUNT not in text


def _wait(cond, timeout: float = 3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def fake():
    server = FakeOllama()
    yield server
    server.close()


@pytest.fixture
def oclient(make_client, fake):
    def _make(**env):
        env.setdefault("OLLAMA_URL", fake.url)
        env.setdefault("OLLAMA_WARMUP", "0")
        return make_client(BACKEND="ollama", **env)

    return _make


@pytest.fixture
def cf_route():
    with respx.mock(assert_all_called=False) as router:
        router.route(host="127.0.0.1").pass_through()
        router.route(host="testserver").pass_through()
        router.route(host="localhost").pass_through()
        yield router.post(host="api.cloudflare.com")


# -- startup, models, image ---------------------------------------------------


def test_default_model_is_gemma():
    assert DEFAULT_MODEL == "@cf/google/gemma-4-26b-a4b-it"
    assert get_settings().cf_model == DEFAULT_MODEL


def test_missing_credentials_do_not_echo_values(monkeypatch):
    monkeypatch.setenv("FALLBACK_BACKEND", "none")
    monkeypatch.setenv("CF_ACCOUNT_ID", ACCOUNT)
    monkeypatch.delenv("CF_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError) as exc:
        create_backend("cloudflare")
    message = str(exc.value)
    assert "CF_ACCOUNT_ID" in message and "CF_API_TOKEN" in message
    _secrets_absent(message)
    assert ACCOUNT not in repr(exc.value)


def test_rejected_llama_models_refuse_startup(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CF_API_TOKEN", TOKEN)
    mixed = "@CF/META/Llama-4-Scout-17b-16e-instruct"
    for model in (LLAMA_32, LLAMA_4, mixed, f"@cf/meta/llama-4-scout-{TOKEN}"):
        monkeypatch.setenv("CF_MODEL", model)
        with pytest.raises(RuntimeError) as exc:
            create_backend("cloudflare")
        message = str(exc.value)
        assert "rejected" in message
        _secrets_absent(message)
        assert model not in message
        assert "@cf/meta" not in message


@pytest.mark.parametrize("name", ["echo", "cloudflare"])
def test_fallback_requires_ollama(monkeypatch, name):
    monkeypatch.setenv("FALLBACK_BACKEND", "cloudflare")
    monkeypatch.setenv("CF_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CF_API_TOKEN", TOKEN)
    with pytest.raises(RuntimeError, match="requires BACKEND=ollama"):
        create_backend(name)


def test_repr_hides_token_and_account(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CF_API_TOKEN", TOKEN)
    backend = CloudflareBackend()
    rendered = f"{backend!r} {get_settings()!r}"
    _secrets_absent(rendered)
    assert "Secret('***')" in repr(get_settings())
    assert backend.model == MODEL


def test_structured_modes_and_rejected_prefixes():
    assert structured_mode(MODEL) == "response_format"
    assert structured_mode(QWEN) == "response_format"
    assert structured_mode("@cf/qwen/qwen3-vl-2b") == "response_format"
    assert structured_mode(MISTRAL) == "response_format"
    assert structured_mode(UNLISTED) is None
    assert structured_mode(LLAMA_32) is None
    assert structured_mode(LLAMA_4) is None
    assert model_rejected(LLAMA_32) and model_rejected(LLAMA_4 + "-extra")
    assert not model_rejected(MODEL)
    for group in (RESPONSE_FORMAT_MODELS, GUIDED_JSON_MODELS):
        assert all("llama" not in model for model in group)


def test_encode_long_edge_and_byte_cap():
    wide = Image.open(io.BytesIO(encode_for_cloudflare(WIDE)))
    assert max(wide.size) == CF_LONG_EDGE
    assert wide.size == (560, 224)
    small = Image.open(io.BytesIO(encode_for_cloudflare(_jpeg(80, 40))))
    assert small.size == (80, 40)
    png = encode_for_cloudflare(PNG_PHOTO)
    assert png is not None and png.startswith(b"\xff\xd8\xff")
    assert encode_for_cloudflare(PHOTO, max_bytes=20) is None
    assert encode_for_cloudflare(JPEG) is None
    assert encode_for_cloudflare(WIDE) is not None
    assert len(encode_for_cloudflare(WIDE)) <= CF_IMAGE_MAX_BYTES


def test_exif_orientation_is_applied_before_the_long_edge():
    from PIL import ImageOps

    image = Image.new("RGB", (30, 80), (10, 20, 30))
    image.putpixel((0, 0), (255, 0, 0))
    exif = Image.Exif()
    exif[0x0112] = 6  # right side is the top of the scene
    buf = io.BytesIO()
    image.save(buf, format="JPEG", exif=exif)
    raw = buf.getvalue()
    upright = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    assert upright is not None and upright.size == (80, 30)
    encoded = encode_for_cloudflare(raw)
    assert encoded is not None
    out = Image.open(io.BytesIO(encoded))
    out.load()
    assert out.size == upright.size
    assert out.getpixel((0, 0)) == upright.convert("RGB").getpixel((0, 0))


def test_child_logger_secrets_are_scrubbed(monkeypatch, caplog):
    monkeypatch.setenv("CF_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CF_API_TOKEN", TOKEN)
    CloudflareBackend()
    child = logging.getLogger("app.backends.child_probe")
    with caplog.at_level(logging.INFO):
        child.info("outbound failed token=%s account=%s", TOKEN, ACCOUNT)
    assert TOKEN not in caplog.text
    assert ACCOUNT not in caplog.text
    assert "***" in caplog.text


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CF_TIMEOUT_S", "0"),
        ("CF_TIMEOUT_S", "-3"),
        ("CF_TIMEOUT_S", "nan"),
        ("CF_TIMEOUT_S", "inf"),
        ("REQUEST_DEADLINE_S", "0"),
        ("REQUEST_DEADLINE_S", "nan"),
        ("OLLAMA_WARM_RESERVE_S", "inf"),
        ("OLLAMA_COLD_RESERVE_S", "-1"),
    ],
)
def test_timeout_and_deadline_reject_non_positive_and_non_finite(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name) as caught:
        get_settings()
    if value in ("nan", "inf"):
        assert value not in str(caught.value).lower()


def test_attempt_budgets_stay_under_the_client_timeout():
    ollama_limit, reserve = attempt_budgets(45, 20)
    assert ollama_limit == 39
    assert reserve == 10
    assert ollama_limit + reserve < client_budget_s(45)
    assert ollama_limit + reserve <= usable_budget_s(45)
    for cf in (1, 5, 20, 40, 90):
        limit, kept = attempt_budgets(45, cf)
        assert kept <= cf
        assert limit + kept < 50
        assert limit + min(cf, usable_budget_s(45) - limit) < 50


# -- primary Cloudflare -------------------------------------------------------


def test_primary_info_and_suggest(make_client, cf_route):
    cf_route.return_value = _ok(usage={"neurons": 29})
    client = make_client(BACKEND="cloudflare", OLLAMA_LOG_RAW="1", **_cf_env())
    info = assert_contract(client.get("/v1/info"), "/v1/info")
    assert info["version"] == "1"
    assert info["backend"] == "cloudflare"
    assert info["backends"]["primary"]["id"] == "cloudflare"
    assert info["backends"]["primary"]["state"] == "ok"
    assert info["backends"]["primary"]["model"] == MODEL
    assert info["backends"]["primary"]["budget"]["limit"] == 6000
    assert info["backends"]["primary"]["budget"]["remaining"] == 6000
    assert "reason" not in info["backends"]["primary"]["budget"]
    assert info["backends"]["fallback"] == {"id": "none", "state": "off", "can_serve": False}
    assert client.get("/healthz").json() == {"status": "ok"}

    response = post_image(client, data=WIDE)
    body = assert_contract(response, "/v1/suggest", "post")
    assert body["backend_used"] == "cloudflare"
    assert body["model_used"] == MODEL
    assert body["category"]["value"] == "Tools"
    assert cf_route.call_count == 1
    call = cf_route.calls.last
    assert call.request.headers["authorization"] == f"Bearer {TOKEN}"
    assert ACCOUNT in str(call.request.url)
    assert "gemma-4-26b-a4b-it" in str(call.request.url)
    payload = _body(call)
    assert "guided_json" not in payload
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"] == build_schema({})
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}
    assert payload["max_tokens"] == 1024
    text = payload["messages"][0]["content"][0]["text"]
    assert "JSON schema:" not in text
    image = _jpeg_from(call)
    assert max(image.size) == CF_LONG_EDGE


def test_png_is_reencoded_to_jpeg(make_client, cf_route):
    cf_route.return_value = _ok()
    client = make_client(BACKEND="cloudflare", **_cf_env())
    assert post_image(client, data=PNG_PHOTO, filename="x.png").status_code == 200
    image = _jpeg_from(cf_route.calls.last)
    assert image.format == "JPEG"
    assert max(image.size) == CF_LONG_EDGE


def test_mistral_uses_response_format_not_guided_json(make_client, cf_route):
    cf_route.return_value = _ok()
    client = make_client(BACKEND="cloudflare", **_cf_env(CF_MODEL=MISTRAL))
    assert post_image(client, data=PHOTO).status_code == 200
    payload = _body(cf_route.calls.last)
    assert payload["response_format"]["json_schema"] == build_schema({})
    assert "guided_json" not in payload
    assert "chat_template_kwargs" not in payload


def test_unknown_model_repairs_once(make_client, cf_route):
    cf_route.side_effect = [_ok("???"), _ok()]
    client = make_client(BACKEND="cloudflare", **_cf_env(CF_MODEL=UNLISTED))
    body = post_image(client, data=PHOTO).json()
    assert body["backend_used"] == "cloudflare"
    assert body["model_used"] == UNLISTED
    assert cf_route.call_count == 2
    first, second = (_body(call) for call in cf_route.calls)
    assert "response_format" not in first and "guided_json" not in first
    assert "JSON schema:" in first["messages"][0]["content"][0]["text"]
    repair = second["messages"][0]["content"][0]["text"]
    assert "previous answer was not valid JSON" in repair


def test_unknown_model_repair_still_unusable(make_client, cf_route):
    cf_route.side_effect = [_ok("???"), _ok("???")]
    client = make_client(BACKEND="cloudflare", **_cf_env(CF_MODEL=UNLISTED))
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_error"
    assert cf_route.call_count == 2


def test_structured_output_does_not_repair(make_client, cf_route):
    cf_route.side_effect = [_ok("???"), _ok()]
    client = make_client(BACKEND="cloudflare", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert cf_route.call_count == 1


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors_hide_secrets(make_client, cf_route, caplog, status):
    cf_route.return_value = httpx.Response(
        status, json={"errors": [{"code": 1000, "message": f"leaked {TOKEN} {ACCOUNT}"}]}
    )
    client = make_client(BACKEND="cloudflare", OLLAMA_LOG_RAW="1", **_cf_env())
    with caplog.at_level(logging.DEBUG):
        response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "backend_error"
    assert "credentials" in body["error"]["message"]
    _secrets_absent(response.text)
    _secrets_absent(caplog.text)
    assert "leaked" not in caplog.text
    backend = client.app.state.backend
    assert backend.transient_failures == 0
    assert backend.plan_rejections == 0
    assert cf_route.call_count == 1


def test_rate_limit_honours_retry_after(make_client, cf_route):
    cf_route.return_value = httpx.Response(429, headers={"Retry-After": "9"}, json={})
    client = make_client(BACKEND="cloudflare", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "9"
    body = assert_contract(response, "/v1/suggest", "post")
    assert body["error"]["code"] == "backend_unavailable"
    assert "rate limit" in body["error"]["message"]


def test_upstream_5xx_counts_as_transient(make_client, cf_route):
    cf_route.return_value = httpx.Response(502, json={"errors": [{"code": 5000}]})
    client = make_client(BACKEND="cloudflare", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_error"
    assert "upstream error" in response.json()["error"]["message"]
    assert client.app.state.backend.transient_failures == 1
    assert client.app.state.backend.plan_rejections == 0
    assert cf_route.call_count == 1


@pytest.mark.parametrize(
    ("payload", "calls"),
    [
        (b"not-json", 1),
        ({"success": True, "result": {}}, 2),
        ({"result": {"response": "  "}}, 2),
        ({"result": ""}, 2),
    ],
)
def test_empty_reply_is_repaired_once(make_client, cf_route, payload, calls):
    """A non-JSON body cannot be repaired. An empty reply gets exactly one repair."""
    if isinstance(payload, bytes):
        cf_route.return_value = httpx.Response(200, content=payload)
    else:
        cf_route.return_value = httpx.Response(200, json=payload)
    client = make_client(BACKEND="cloudflare", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_error"
    assert "unusable" in response.json()["error"]["message"]
    assert cf_route.call_count == calls
    assert client.app.state.backend.transient_failures == 0


def test_undecodable_image_is_not_sent(make_client, cf_route):
    client = make_client(BACKEND="cloudflare", **_cf_env())
    response = post_image(client, data=JPEG)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "backend_error"
    assert cf_route.call_count == 0


@pytest.mark.parametrize("code", [5035, "5035"])
def test_paid_plan_as_primary_is_not_retried(make_client, cf_route, caplog, code):
    cf_route.side_effect = [_paid(code), _ok()]
    client = make_client(BACKEND="cloudflare", OLLAMA_LOG_RAW="1", **_cf_env(CF_MODEL=UNLISTED))
    with caplog.at_level(logging.DEBUG):
        response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "backend_unavailable"
    assert body["error"]["message"] == "the configured Cloudflare model requires Workers Paid"
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    _secrets_absent(response.text)
    assert "plan-detail-marker" not in response.text
    backend = client.app.state.backend
    assert backend.transient_failures == 0
    assert backend.plan_rejections == 1
    assert cf_route.call_count == 1
    paid_logs = [r for r in caplog.records if "requires Workers Paid" in r.getMessage()]
    assert len(paid_logs) == 1
    assert UNLISTED in paid_logs[0].getMessage()
    _secrets_absent(caplog.text)
    assert "plan-detail-marker" not in caplog.text
    assert "7777" not in caplog.text


def test_usage_counts_are_logged_and_brand_is_not(make_client, cf_route, caplog):
    raw_brand = "  Acme\u202eHidden\x00" + ("Z" * 200)
    answer = dict(VALID)
    answer["brand"] = raw_brand
    answer["model_number"] = "\u0007"
    answer["serial_number"] = "sn-should-not-log"
    cf_route.return_value = _ok(json.dumps(answer), usage={"neurons": 29, "note": "secret-text"})
    client = make_client(BACKEND="cloudflare", OLLAMA_LOG_RAW="1", **_cf_env())
    with caplog.at_level(logging.DEBUG):
        body = post_image(client, data=PHOTO).json()
    assert body["brand"] == clean_identity(raw_brand)
    assert "\u202e" not in body["brand"] and "\x00" not in body["brand"]
    assert len(body["brand"]) <= 128
    assert body["model_number"] == ""
    assert body["serial_number"] == "sn-should-not-log"
    assert "neurons=29" in caplog.text
    usage_logs = [rec for rec in caplog.records if "cloudflare usage" in rec.getMessage()]
    assert usage_logs and usage_logs[0].levelno == logging.INFO
    assert MODEL in usage_logs[0].getMessage()
    assert "secret-text" not in caplog.text
    assert "AcmeHidden" not in caplog.text
    assert "sn-should-not-log" not in caplog.text
    _secrets_absent(caplog.text)
    encoded = base64.b64encode(encode_for_cloudflare(PHOTO)).decode()
    assert encoded[:48] not in caplog.text


# -- failover -----------------------------------------------------------------


def test_failover_when_ollama_is_unreachable(oclient, cf_route):
    cf_route.return_value = _ok()
    client = oclient(OLLAMA_URL="http://127.0.0.1:9", FALLBACK_BACKEND="cloudflare", **_cf_env())
    body = post_image(client, data=PHOTO).json()
    assert body["backend_used"] == "cloudflare"
    assert cf_route.call_count == 1


def test_failover_when_degraded_then_back_to_ollama(oclient, fake, cf_route):
    fake.size_vram = 0
    cf_route.return_value = _ok()
    client = oclient(FALLBACK_BACKEND="cloudflare", **_cf_env())
    first = post_image(client, data=PHOTO)
    assert first.status_code == 200
    assert first.json()["backend_used"] == "cloudflare"
    assert fake.chats() == []
    assert cf_route.call_count == 1
    fake.size_vram = 8_000_000_000
    second = post_image(client, data=PHOTO)
    assert second.status_code == 200
    assert second.json()["backend_used"] == "ollama"
    assert len(fake.chats()) == 1
    assert cf_route.call_count == 1


def test_failover_on_timeout_stays_inside_the_client_budget(oclient, fake, cf_route):
    fake.release.clear()
    cf_route.return_value = _ok()
    client = oclient(
        FALLBACK_BACKEND="cloudflare",
        BACKEND_TIMEOUT_S="2",
        **_cf_env(CF_TIMEOUT_S="1"),
    )
    started = time.monotonic()
    try:
        response = post_image(client, data=PHOTO)
    finally:
        fake.release.set()
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert response.json()["backend_used"] == "cloudflare"
    assert cf_route.call_count == 1
    assert elapsed < client_budget_s(2)


def test_failover_on_upstream_5xx(oclient, fake, cf_route):
    fake.chat_status = 502
    cf_route.return_value = _ok()
    client = oclient(FALLBACK_BACKEND="cloudflare", **_cf_env())
    body = post_image(client, data=PHOTO).json()
    assert body["backend_used"] == "cloudflare"
    assert cf_route.call_count == 1


def test_failover_when_queue_is_full(oclient, fake, cf_route):
    cf_route.return_value = _ok()
    client = oclient(
        OLLAMA_QUEUE_SIZE="0",
        BACKEND_TIMEOUT_S="60",
        FALLBACK_BACKEND="cloudflare",
        **_cf_env(),
    )
    fake.release.clear()
    held: dict[str, int] = {}

    def go():
        held["status"] = post_image(client, data=PHOTO).status_code

    thread = threading.Thread(target=go)
    thread.start()
    assert _wait(lambda: len(fake.chats()) == 1)
    try:
        response = post_image(client, data=PHOTO)
    finally:
        fake.release.set()
        thread.join(10)
    assert response.status_code == 200
    assert response.json()["backend_used"] == "cloudflare"
    assert held["status"] == 200
    assert cf_route.call_count == 1


@pytest.mark.parametrize("status", [400, 413, 415])
def test_no_failover_on_ollama_4xx(oclient, fake, cf_route, status):
    fake.chat_status = status
    client = oclient(FALLBACK_BACKEND="cloudflare", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert f"HTTP {status}" in response.json()["error"]["message"]
    assert response.json()["error"]["message"] != BOTH_FAILED
    assert cf_route.call_count == 0


def test_no_failover_on_client_4xx(oclient, cf_route):
    client = oclient(FALLBACK_BACKEND="cloudflare", MAX_IMAGE_BYTES="100", **_cf_env())
    empty = client.post("/v1/suggest", data={})
    too_big = post_image(client, data=PHOTO)
    unsupported = post_image(client, data=GIF, filename="x.gif")
    assert empty.status_code == 400
    assert too_big.status_code == 413
    assert unsupported.status_code == 415
    assert cf_route.call_count == 0


def test_cold_warmup_does_not_fail_over_by_default(oclient, fake, cf_route):
    fake.warm_status = 500
    fake.warm_delay = 0.5
    client = oclient(OLLAMA_WARMUP="1", FALLBACK_BACKEND="cloudflare", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.headers["retry-after"]
    assert "warming up" in response.json()["error"]["message"]
    assert cf_route.call_count == 0
    warm = client.app.state.backend.primary._warm_thread
    assert warm is not None and warm.is_alive()


def test_cold_failover_keeps_warmup_and_returns_to_ollama(oclient, fake, cf_route):
    fake.warm_status = 500
    fake.warm_delay = 0.4
    cf_route.return_value = _ok()
    client = oclient(
        OLLAMA_WARMUP="1",
        FALLBACK_BACKEND="cloudflare",
        FAILOVER_ON_COLD="true",
        **_cf_env(),
    )
    first = post_image(client, data=PHOTO)
    assert first.status_code == 200
    assert first.json()["backend_used"] == "cloudflare"
    warm = client.app.state.backend.primary._warm_thread
    assert warm is not None and warm.is_alive()
    fake.warm_status = 200
    fake.warm_delay = 0
    assert _wait(lambda: client.app.state.backend.primary.ready, timeout=8)
    second = post_image(client, data=PHOTO)
    assert second.status_code == 200
    assert second.json()["backend_used"] == "ollama"
    assert cf_route.call_count == 1


def test_fallback_off_sends_nothing_to_cloudflare(oclient, fake, cf_route):
    fake.chat_status = 500
    client = oclient(**_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert "HTTP 500" in response.json()["error"]["message"]
    assert cf_route.call_count == 0
    client_none = oclient(FALLBACK_BACKEND="none", **_cf_env())
    assert post_image(client_none, data=PHOTO).status_code == 503
    assert cf_route.call_count == 0


def test_image_over_cap_does_not_call_cloudflare(oclient, fake, cf_route, monkeypatch):
    monkeypatch.setattr("app.backends.cloudflare.encode_for_cloudflare", lambda *_a, **_k: None)
    fake.chat_status = 500
    client = oclient(FALLBACK_BACKEND="cloudflare", **_cf_env())
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    assert response.json()["error"]["message"] == "ollama returned HTTP 500"
    assert cf_route.call_count == 0


def test_degraded_image_over_cap_stays_local(oclient, fake, cf_route, monkeypatch):
    monkeypatch.setattr("app.backends.cloudflare.encode_for_cloudflare", lambda *_a, **_k: None)
    fake.size_vram = 0
    client = oclient(FALLBACK_BACKEND="cloudflare", **_cf_env())
    body = post_image(client, data=PHOTO).json()
    assert body["backend_used"] == "ollama"
    assert cf_route.call_count == 0


def test_both_backends_down_uses_the_existing_503_shape(oclient, cf_route):
    cf_route.return_value = httpx.Response(500, text="upstream blew up")
    client = oclient(
        OLLAMA_URL="http://127.0.0.1:9",
        FALLBACK_BACKEND="cloudflare",
        **_cf_env(),
    )
    response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == "backend_unavailable"
    assert body["error"]["message"] == BOTH_FAILED
    _secrets_absent(response.text)


@pytest.mark.parametrize("code", [5035, "5035"])
def test_paid_plan_as_fallback_is_both_failed_and_not_retried(
    oclient, fake, cf_route, caplog, code
):
    fake.chat_status = 502
    cf_route.side_effect = [_paid(code), _ok()]
    client = oclient(
        FALLBACK_BACKEND="cloudflare",
        OLLAMA_LOG_RAW="1",
        **_cf_env(CF_MODEL=UNLISTED),
    )
    with caplog.at_level(logging.DEBUG):
        response = post_image(client, data=PHOTO)
    assert response.status_code == 503
    body = assert_contract(response, "/v1/suggest", "post")
    assert body["error"]["code"] == "backend_unavailable"
    assert body["error"]["message"] == BOTH_FAILED
    assert "Workers Paid" not in response.text
    _secrets_absent(response.text)
    assert "plan-detail-marker" not in response.text
    fallback = client.app.state.backend.fallback
    assert fallback.transient_failures == 0
    assert fallback.plan_rejections == 1
    assert cf_route.call_count == 1
    paid_logs = [r for r in caplog.records if "requires Workers Paid" in r.getMessage()]
    assert len(paid_logs) == 1
    assert UNLISTED in paid_logs[0].getMessage()
    _secrets_absent(caplog.text)
    assert "plan-detail-marker" not in caplog.text
    assert "7777" not in caplog.text


def test_fallback_5xx_matches_paid_plan_client_shape(oclient, fake, cf_route):
    fake.chat_status = 502
    cf_route.return_value = httpx.Response(502, json={"errors": [{"code": 5000}]})
    client = oclient(FALLBACK_BACKEND="cloudflare", **_cf_env())
    response = post_image(client, data=PHOTO)
    body = response.json()
    assert response.status_code == 503
    assert body["error"]["message"] == BOTH_FAILED
    assert body["error"]["code"] == "backend_unavailable"
    assert client.app.state.backend.fallback.transient_failures == 1
    assert client.app.state.backend.fallback.plan_rejections == 0


def test_healthz_and_info_contract(oclient, fake, cf_route):
    fake.size_vram = 1024
    client = oclient(FALLBACK_BACKEND="cloudflare", **_cf_env())
    assert client.get("/healthz").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}
    info = assert_contract(client.get("/v1/info"), "/v1/info")
    assert info["backends"]["primary"] == {"id": "ollama", "state": "ok"}
    fallback = info["backends"]["fallback"]
    assert fallback["id"] == "cloudflare"
    assert fallback["state"] == "ready"
    assert fallback["can_serve"] is True
    assert fallback["model"] == MODEL
    assert fallback["budget"]["limit"] == 6000
    assert fallback["budget"]["remaining"] == 6000
    fake.size_vram = 0
    degraded = client.get("/healthz")
    assert degraded.status_code == 503
    assert degraded.json() == {"status": "degraded", "serving": "fallback"}
    assert_contract(degraded, "/healthz")
    local = oclient(FALLBACK_BACKEND="none")
    only_local = local.get("/healthz")
    assert only_local.status_code == 503
    assert only_local.json() == {"status": "degraded"}
    assert "serving" not in only_local.json()


def test_registry_includes_cloudflare(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("CF_API_TOKEN", TOKEN)
    assert "cloudflare" in REGISTRY
    assert isinstance(create_backend("cloudflare"), CloudflareBackend)


# -- docs ---------------------------------------------------------------------


def test_docs_reject_llama_and_do_not_offer_it():
    text = DOC.read_text()
    models, _, rejected = text.partition("## Rejected models")
    assert rejected
    # "Ollama" contains the letters llama; the rejected ids must stay out of the candidate table.
    assert "llama-3" not in models.lower() and "llama-4" not in models.lower()
    assert "llama 3" not in models.lower() and "llama 4" not in models.lower()
    assert LLAMA_32 in rejected and LLAMA_4 in rejected
    assert AUP_QUOTE in rejected
    folded = " ".join(rejected.split())
    assert "same restriction" in folded
    assert "They are not an opt-in." in folded
    assert "/agree" not in text
    assert "opt-in-only" not in text
    assert "provisional" not in text.lower()
    assert "confirmed default" in text.lower()
    assert MODEL in text
    assert QWEN in text and MISTRAL in text
    assert "not recommended" in text.lower()
    assert "image input was confirmed" in text.lower()
    assert "2026-07-28-models-require-workers-paid" in text
    assert "estimate" in text
    assert "29 Neurons" not in text or "estimate" in text
    assert "DevOps" in text

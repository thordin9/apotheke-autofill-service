from __future__ import annotations

import base64
import json

import pytest
from conftest import JPEG, assert_contract, post_image
from ollama_mock import FakeOllama

from app.backends.ollama import MAX_TAG, MAX_TAGS, build_prompt, clamp, extract_json


@pytest.fixture
def fake():
    f = FakeOllama()
    yield f
    f.close()


@pytest.fixture
def oclient(make_client, fake):
    def _make(**env):
        env.setdefault("OLLAMA_URL", fake.url)
        return make_client(BACKEND="ollama", **env)

    return _make


def test_happy_path(oclient, fake):
    r = post_image(oclient())
    assert r.status_code == 200
    body = assert_contract(r, "/v1/suggest", "post")
    assert body["name"] == {"value": "Cordless drill", "confidence": 0.8}
    assert [t["value"] for t in body["tags"]] == ["bosch", "red"]
    req = fake.chats()[-1]
    assert req["model"] == "moondream"
    assert req["stream"] is False and req["format"] == "json"
    assert base64.b64decode(req["messages"][0]["images"][0]) == JPEG


def test_model_env(oclient, fake):
    r = post_image(oclient(OLLAMA_MODEL="qwen2-vl-2b"))
    assert r.status_code == 200
    assert fake.chats()[-1]["model"] == "qwen2-vl-2b"


def test_context_in_prompt_and_exact_spelling(oclient, fake):
    ctx = {"categories": ["Tools", "Kitchen"], "tags": ["Bosch"], "locale": "de-DE"}
    r = post_image(oclient(), context=json.dumps(ctx))
    body = assert_contract(r, "/v1/suggest", "post")
    prompt = fake.chats()[-1]["messages"][0]["content"]
    assert '"Tools", "Kitchen"' in prompt and '"Bosch"' in prompt and "de-DE" in prompt
    assert body["category"]["value"] == "Tools"  # model said "tools"
    assert body["tags"][0]["value"] == "Bosch"


@pytest.mark.parametrize(
    "content",
    ["not json at all", "[1,2]", '{"name": 5}', "{broken", '{"tags": "x"}'],
)
def test_malformed_model_output(oclient, fake, content):
    fake.chat_content = content
    r = post_image(oclient())
    assert r.status_code == 503
    assert assert_contract(r, "/v1/suggest", "post")["error"]["code"] == "backend_error"


def test_upstream_http_error(oclient, fake):
    fake.chat_status = 500
    r = post_image(oclient())
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "backend_error"


def test_upstream_non_json_body(oclient, fake):
    fake.chat_raw = b"<html>"
    r = post_image(oclient())
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "backend_error"


def test_unreachable(oclient):
    r = post_image(oclient(OLLAMA_URL="http://127.0.0.1:9"))
    assert r.status_code == 503
    assert assert_contract(r, "/v1/suggest", "post")["error"]["code"] == "backend_unavailable"


def test_timeout(oclient, fake):
    fake.chat_delay = 1.5
    r = post_image(oclient(BACKEND_TIMEOUT_S="0.5"))
    assert r.status_code == 504
    assert assert_contract(r, "/v1/suggest", "post")["error"]["code"] == "backend_timeout"


def test_fenced_json_with_prose_is_extracted():
    assert extract_json('Sure!\n```json\n{"name":{"value":"x"}}\n```') == {"name": {"value": "x"}}


def test_clamps():
    raw = {
        "name": {"value": "n" * 500, "confidence": 7},
        "category": "Tools",
        "description": {"value": "  lots   of\n space ", "confidence": -1},
        "tags": [{"value": f"t{i}"} for i in range(30)] + [{"value": "x" * 99}],
    }
    out = clamp(raw, {})
    assert len(out["name"]["value"]) <= 120 and out["name"]["confidence"] == 1.0
    assert out["category"] == {"value": "Tools"}
    assert out["description"] == {"value": "lots of space", "confidence": 0.0}
    assert len(out["tags"]) == MAX_TAGS
    assert all(len(t["value"]) <= MAX_TAG for t in out["tags"])


def test_clamp_dedupes_tags_case_insensitively():
    out = clamp({"tags": ["Red", "red", {"value": "RED"}, "blue"]}, {"tags": ["red"]})
    assert [t["value"] for t in out["tags"]] == ["red", "blue"]


def test_clamps_via_http(oclient, fake):
    fake.chat_content = json.dumps({"tags": [f"tag{i}" for i in range(25)]})
    body = assert_contract(post_image(oclient()), "/v1/suggest", "post")
    assert len(body["tags"]) == MAX_TAGS


def test_prompt_without_context_has_defaults():
    p = build_prompt({})
    assert "locale 'en'" in p and "Existing" not in p

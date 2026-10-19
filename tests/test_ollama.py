from __future__ import annotations

import base64
import json
import logging

import pytest
from conftest import JPEG, assert_contract, post_image
from ollama_mock import FakeOllama

from app.backends.ollama import (
    CATEGORY_SYNONYMS,
    MAX_TAG,
    MAX_TAGS,
    PLAIN_PROMPT,
    RAW_LOG_LIMIT,
    build_prompt,
    build_schema,
    caption_name,
    clamp,
    extract_json,
    match_key,
    snap,
    suggestion_from_text,
    truncate_for_log,
)
from app.identity import clean_identity, identity_fields, redact_for_log

LOGGER = "app.backends.ollama"
IMAGE = b"\xff\xd8\xff\xe0" + bytes(range(256)) * 4  # distinctive base64


@pytest.fixture
def fake():
    f = FakeOllama()
    yield f
    f.close()


@pytest.fixture
def oclient(make_client, fake):
    def _make(**env):
        env.setdefault("OLLAMA_URL", fake.url)
        env.setdefault("OLLAMA_WARMUP", "0")
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
    assert req["stream"] is False and req["format"] == build_schema({})
    assert base64.b64decode(req["messages"][0]["images"][0]) == JPEG


def test_default_generation_options(oclient, fake):
    assert post_image(oclient()).status_code == 200
    req = fake.chats()[-1]
    assert req["options"] == {"temperature": 0.0, "num_predict": 256}
    assert req["keep_alive"] == "30m"
    assert req["format"]["type"] == "object"


def test_generation_options_from_env(oclient, fake):
    c = oclient(
        OLLAMA_NUM_PREDICT="96",
        OLLAMA_TEMPERATURE="0.2",
        OLLAMA_KEEP_ALIVE="-1",
        OLLAMA_JSON_FORMAT="0",
    )
    assert post_image(c).status_code == 200
    req = fake.chats()[-1]
    assert req["options"] == {"temperature": 0.2, "num_predict": 96}
    assert req["keep_alive"] == -1
    assert "format" not in req


def test_num_predict_zero_removes_cap(oclient, fake):
    assert post_image(oclient(OLLAMA_NUM_PREDICT="0")).status_code == 200
    assert "num_predict" not in fake.chats()[-1]["options"]


def test_model_env(oclient, fake):
    fake.loaded = ["qwen2-vl-2b:latest"]
    r = post_image(oclient(OLLAMA_MODEL="qwen2-vl-2b"))
    assert r.status_code == 200
    assert fake.chats()[-1]["model"] == "qwen2-vl-2b"


def test_context_in_prompt_and_exact_spelling(oclient, fake):
    ctx = {"categories": ["Tools", "Kitchen"], "tags": ["Bosch"], "locale": "de-DE"}
    r = post_image(oclient(), context=json.dumps(ctx))
    body = assert_contract(r, "/v1/suggest", "post")
    prompt = fake.chats()[-1]["messages"][0]["content"]
    assert prompt == build_prompt(ctx)
    assert "de-DE" in prompt and "prefer:" not in prompt
    schema = fake.chats()[-1]["format"]
    assert schema == build_schema(ctx)
    assert "brand" in schema["required"]
    assert body["category"]["value"] == "Tools"  # model said "tools"
    assert body["tags"][0]["value"] == "Bosch"


@pytest.mark.parametrize(
    "content",
    [
        "",
        "   \n\n  ",
        "{}",
        "[1,2]",
        '{"name": 5}',
        "{broken",
        '{"tags": []}',
        '{"name": "str"}',
        "I'm sorry, I can't help with that.",
        "12 34 ?!",
    ],
)
def test_malformed_model_output(oclient, fake, content):
    fake.chat_content = content  # the plain-text retry gets the same junk
    r = post_image(oclient())
    assert r.status_code == 503
    assert assert_contract(r, "/v1/suggest", "post")["error"]["code"] == "backend_error"


def _log_text(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.name == LOGGER)


def test_unusable_output_logs_raw_at_warning(oclient, fake, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    fake.chat_content = '{"name": 5}\n{"tags": []}'
    c = oclient(AUTOFILL_TOKEN="s3cret-token", OLLAMA_PLAIN_RETRY="0")
    r = post_image(c, data=IMAGE, headers={"Authorization": "Bearer s3cret-token"})
    assert r.status_code == 503
    warnings = [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.WARNING]
    assert warnings
    text = _log_text(caplog)
    assert '{"name": 5}\\n{"tags": []}' in text  # repr: one line per record
    assert "model=moondream" in text
    assert base64.b64encode(IMAGE).decode()[:40] not in text
    assert "s3cret-token" not in text


def test_raw_log_is_truncated(oclient, fake, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    fake.chat_content = "{" + "x" * 10_000
    assert post_image(oclient(OLLAMA_PLAIN_RETRY="0")).status_code == 503
    rec = next(r for r in caplog.records if r.name == LOGGER)
    assert len(rec.getMessage()) < RAW_LOG_LIMIT + 300
    assert "more chars]" in rec.getMessage()


def test_truncate_for_log():
    assert truncate_for_log("abc", 5) == "abc"
    assert truncate_for_log("abcdefgh", 5) == "abcde...[3 more chars]"


def test_good_output_not_logged_by_default(oclient, fake, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    assert post_image(oclient()).status_code == 200
    assert not [r for r in caplog.records if r.name == LOGGER]


def test_identity_fields_trim_cap_and_aliases():
    raw = {
        "name": "drill",
        "brand": "  Makita  ",
        "modelNumber": {"value": "  DHP453  ", "confidence": 0.4},
        "serialNumber": None,
    }
    assert identity_fields(raw) == {
        "brand": "Makita",
        "model_number": "DHP453",
        "serial_number": "",
    }
    assert clean_identity("n/a") == ""
    assert clean_identity("  " + ("x" * 300)) == "x" * 128
    weird = '\x01עברית\u200f🔧"q"'
    assert clean_identity(f"  {weird}  ") == weird
    out = clamp(
        {
            "name": "drill",
            "category": "Tools",
            "description": "a cordless drill on the bench",
            "tags": ["drill"],
            "manufacturer": {"value": " Makita "},
            "model": "DHP453",
            "serial_no": "  SN-1  ",
        },
        {},
    )
    assert out["brand"] == "Makita"
    assert out["model_number"] == "DHP453"
    assert out["serial_number"] == "SN-1"


def test_identity_values_are_redacted_in_raw_logs(oclient, fake, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    secret_brand = "BoschSecret"
    secret_model = "GSB-18V-99"
    secret_serial = "SN-99881-KEEP-OUT"
    fake.chat_content = json.dumps(
        {
            "name": {"value": "Cordless drill", "confidence": 0.8},
            "category": {"value": "Tools", "confidence": 0.9},
            "tags": ["drill"],
            "description": {"value": "A red cordless drill", "confidence": 0.7},
            "confidence": 0.8,
            "brand": secret_brand,
            "model_number": {"value": secret_model},
            "serial_number": secret_serial,
        }
    )
    c = oclient(OLLAMA_LOG_RAW="1")
    body = post_image(c).json()
    assert body["brand"] == secret_brand
    assert body["model_number"] == secret_model
    assert body["serial_number"] == secret_serial
    text = _log_text(caplog)
    assert secret_brand not in text and secret_model not in text and secret_serial not in text
    assert "[redacted]" in text and "Cordless drill" in text
    # Unusable output is logged too, and still must not keep the values.
    caplog.clear()
    fake.chat_content = (
        '{"name": "str", "brand": "' + secret_brand + '", "serial_number": "' + secret_serial + '"}'
    )
    assert post_image(oclient(OLLAMA_PLAIN_RETRY="0")).status_code == 503
    text = _log_text(caplog)
    assert secret_brand not in text and secret_serial not in text
    assert "[redacted]" in text
    raw = '{"brand": "Hidden", "note": "still here"} broken'
    assert "Hidden" not in redact_for_log(raw) and "still here" in redact_for_log(raw)


def test_log_raw_flag_logs_every_output_at_debug(oclient, fake, caplog):
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    c = oclient(OLLAMA_LOG_RAW="1")
    assert post_image(c, data=IMAGE).status_code == 200
    debug = [r for r in caplog.records if r.name == LOGGER and r.levelno == logging.DEBUG]
    assert len(debug) == 1
    assert "Cordless drill" in debug[0].getMessage()
    assert base64.b64encode(IMAGE).decode()[:40] not in _log_text(caplog)


def test_upstream_error_body_logged(oclient, fake, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    fake.chat_status = 404
    fake.chat_raw = b'{"error":"model \'moondream\' not found"}'
    assert post_image(oclient()).status_code == 503
    assert "not found" in _log_text(caplog)


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


def test_prompt_is_the_v3_lean_text():
    p = build_prompt({})
    assert "locale 'en'" in p and "prefer:" not in p
    assert "exactly one of: General, Tools, Electronics, Kitchen, Documents, Hobby, " in p
    assert "Clothing, Shoes, Books, Lighting" in p
    assert "up to 3 short lowercase tags" in p
    assert "under 12 words" in p
    assert 'use "" for any of them you cannot read' in p
    assert "brand, model_number, serial_number" in p
    assert "brand or a person's name" in p and "object only" in p
    assert len(p) < 800  # small models do better with short prompts


def test_prompt_interpolates_only_the_locale():
    plain = build_prompt({})
    with_lists = build_prompt({"categories": ["Tools", "Kitchen"], "tags": ["bosch"]})
    assert plain == with_lists
    german = build_prompt({"locale": "de-DE"})
    assert "locale 'de-DE'" in german and "locale 'en'" not in german


@pytest.mark.parametrize("ctx", [{}, {"categories": ["Tools", "Kitchen"], "tags": ["bosch"]}])
def test_prompt_has_no_json_template_to_echo(ctx):
    # S1: Moondream copied '{"name":"...",...}' back on 5 of 9 photos.
    p = build_prompt(ctx)
    assert "{" not in p and "..." not in p and '"name"' not in p


def test_schema_is_the_v3_lean_schema():
    s = build_schema({"categories": ["Tools", " ", "Kitchen", "Tools"]})
    assert s == build_schema({})
    assert s["properties"]["category"]["enum"] == [
        "General",
        "Tools",
        "Electronics",
        "Kitchen",
        "Documents",
        "Hobby",
        "Clothing",
        "Shoes",
        "Books",
        "Lighting",
    ]
    assert s["properties"]["tags"]["maxItems"] == 3
    assert s["properties"]["description"]["maxLength"] == 100
    for key in ("brand", "model_number", "serial_number"):
        assert s["properties"][key] == {"type": "string", "maxLength": 30}
    assert set(s["required"]) == {
        "name",
        "category",
        "tags",
        "description",
        "confidence",
        "brand",
        "model_number",
        "serial_number",
    }
    assert s["properties"]["name"]["minLength"] >= 2
    assert s["properties"]["description"]["minLength"] >= 10


def test_json_format_modes(oclient, fake):
    assert post_image(oclient(OLLAMA_JSON_FORMAT="json")).status_code == 200
    assert fake.chats()[-1]["format"] == "json"
    assert post_image(oclient(OLLAMA_JSON_FORMAT="1")).status_code == 200
    assert fake.chats()[-1]["format"]["type"] == "object"
    assert post_image(oclient(OLLAMA_JSON_FORMAT="0")).status_code == 200
    assert "format" not in fake.chats()[-1]


@pytest.mark.parametrize(
    ("name", "description", "want"),
    [
        # Raw schema answers from Moondream on the Jetson (Oct 18, 2026).
        ("bose", "A pair of black headphones with a silver headband.", "Black headphones"),
        (
            "reebok",
            "A white and red sneaker with a Reebok logo on the side.",
            "White and red sneaker",
        ),
        ("John", "A man with a beard and grey shirt holding a microphone.", "Man"),
        # The name agrees with the description: kept as is.
        ("Laptop", "A black laptop computer with a silver logo on its lid.", "Laptop"),
        ("bulb", "A broken light bulb on a brown surface.", "bulb"),
        ("Knives", "A knife with a black handle and silver blade.", "Knives"),
    ],
)
def test_name_taken_from_description_when_they_disagree(name, description, want):
    out = clamp({"name": name, "category": "x", "description": description, "confidence": 0.8}, {})
    assert out["name"]["value"] == want
    if want.casefold() != name.casefold():
        assert out["name"]["confidence"] == 0.5  # derived, so capped
    else:
        assert out["name"]["confidence"] == 0.8


def test_short_description_does_not_replace_the_name():
    out = clamp({"name": "Drill", "description": "Red tool"}, {})
    assert out["name"]["value"] == "Drill"


def test_caption_name_limits():
    assert caption_name("A white ceramic coffee mug on a desk.") == "white ceramic coffee mug"
    assert caption_name("A big shiny white ceramic coffee mug.") == "white ceramic coffee mug"
    assert caption_name("Several old kitchen utensils scattered loosely across it") is None
    assert caption_name("John.") is None


def _wait(cond, timeout=5.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_warmup_ready_false_then_true(oclient, fake):
    fake.warm_delay = 0.5
    c = oclient(OLLAMA_WARMUP="1", OLLAMA_RETRY_AFTER_S="7")
    with c:  # runs lifespan -> background warm-up
        info = assert_contract(c.get("/v1/info"), "/v1/info")
        assert info["ready"] is False and info["backend"] == "ollama"
        r = post_image(c)
        assert r.status_code == 503 and r.headers["Retry-After"] == "7"
        assert "warming up" in assert_contract(r, "/v1/suggest", "post")["error"]["message"]
        assert _wait(lambda: c.get("/v1/info").json()["ready"])
        assert post_image(c).status_code == 200
    warm = [b for p, b in fake.requests if p == "/api/generate"]
    assert warm and warm[0]["model"] == "moondream"
    assert warm[0]["keep_alive"] == "30m"


def test_warmup_retries_until_ollama_answers(oclient, fake):
    fake.warm_status = 500
    c = oclient(OLLAMA_WARMUP="1")
    assert c.get("/v1/info").json()["ready"] is False  # lazily started without lifespan
    assert _wait(lambda: sum(p == "/api/generate" for p, _ in fake.requests) >= 1)
    fake.warm_status = 200
    assert _wait(lambda: c.get("/v1/info").json()["ready"], timeout=8)


def test_warmup_disabled_is_ready(oclient):
    assert oclient().get("/v1/info").json()["ready"] is True


def test_queue_full_returns_503_retry_after(oclient, fake):
    import threading

    # 60 s: with nothing measured yet (15 s assumed per answer) one queued
    # request fits, so the queue limit is what refuses the third.
    c = oclient(OLLAMA_RETRY_AFTER_S="3", BACKEND_TIMEOUT_S="60")
    backend = c.app.state.backend
    fake.release.clear()  # hold the in-flight request inside fake Ollama
    results: dict[str, int] = {}

    def go(key):
        results[key] = post_image(c).status_code

    a = threading.Thread(target=go, args=("a",))
    a.start()
    assert _wait(lambda: len(fake.chats()) == 1)
    b = threading.Thread(target=go, args=("b",))
    b.start()
    assert _wait(lambda: backend._waiting == 1)
    r = post_image(c)
    assert r.status_code == 503 and r.headers["Retry-After"] == "3"
    body = assert_contract(r, "/v1/suggest", "post")
    assert body["error"]["code"] == "backend_unavailable" and "busy" in body["error"]["message"]
    fake.release.set()
    a.join(10)
    b.join(10)
    assert results == {"a": 200, "b": 200}
    assert len(fake.chats()) == 2


def test_queue_size_zero_rejects_second(oclient, fake):
    import threading

    c = oclient(OLLAMA_QUEUE_SIZE="0")
    fake.release.clear()
    t = threading.Thread(target=lambda: post_image(c))
    t.start()
    assert _wait(lambda: len(fake.chats()) == 1)
    assert post_image(c).status_code == 503
    fake.release.set()
    t.join(10)


# -- lenient parsing of what small models actually send ----------------------


@pytest.mark.parametrize(
    "text",
    [
        '{"name":{"value":"Drill","confidence":0.8},"description":{"value":"A red dri',
        '{"name":{"value":"Drill","confidence":0.8},"tags":[{"value":"red"},{"val',
        '{"name":{"value":"Drill","confidence":0.8},"descr',
        '{"name":{"value":"Drill","confidence":0.8},',
    ],
)
def test_truncated_json_is_repaired(text):
    assert extract_json(text)["name"] == {"value": "Drill", "confidence": 0.8}


def test_truncated_json_keeps_complete_tags():
    out = extract_json('{"name":"Drill","tags":[{"value":"red"},{"value":"bo')
    assert out == {"name": "Drill", "tags": [{"value": "red"}]}


def test_truncated_output_via_http(oclient, fake):
    fake.chat_content = '{"name":{"value":"Cordless drill","confidence":0.8},"category":{"val'
    body = assert_contract(post_image(oclient()), "/v1/suggest", "post")
    assert body["name"]["value"] == "Cordless drill" and "category" not in body


def test_flat_and_renamed_keys():
    raw = {
        "Object": "Coffee mug",
        "type": "Kitchen",
        "caption": "A white ceramic mug.",
        "keywords": "white, ceramic; mug",
    }
    out = clamp(raw, {"categories": ["kitchen"]})
    assert out["name"] == {"value": "Coffee mug"}
    assert out["category"] == {"value": "kitchen"}
    assert out["description"] == {"value": "A white ceramic mug."}
    assert [t["value"] for t in out["tags"]] == ["white", "ceramic", "mug"]


def test_canonical_key_wins_over_alias():
    assert clamp({"title": "B", "name": "A"}, {})["name"] == {"value": "A"}


def test_single_wrapper_object_is_unwrapped():
    out = clamp({"item": {"name": "Lamp", "category": "Lighting"}}, {})
    assert out == {
        "name": {"value": "Lamp"},
        "category": {"value": "Lighting"},
        "brand": "",
        "model_number": "",
        "serial_number": "",
    }


def test_schema_placeholders_are_dropped():
    raw = {"name": {"value": "str", "confidence": "0..1"}, "category": "string", "tags": ["..."]}
    with pytest.raises(Exception, match="no usable fields"):
        clamp(raw, {})


@pytest.mark.parametrize(("c", "want"), [("0.7", 0.7), ("85%", 0.85), ("high", None), (2, 1.0)])
def test_confidence_coercion(c, want):
    out = clamp({"name": {"value": "x", "confidence": c}}, {})
    assert out["name"].get("confidence") == want


# -- plain-text fallback ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "name"),
    [
        (
            "The image shows a red Bosch cordless drill on a wooden table.",
            "Red Bosch cordless drill",
        ),
        ("A white ceramic coffee mug with a blue handle.", "White ceramic coffee mug"),
        (
            "In this image, there is a pair of black headphones lying on a desk.",
            "Black headphones",
        ),
        ("This is a stainless steel water bottle. It has a cap.", "Stainless steel water bottle"),
        ("A close-up of an old brass key", "Old brass key"),
    ],
)
def test_name_from_plain_text(text, name):
    out = suggestion_from_text(text, {})
    assert out["name"] == {"value": name, "confidence": 0.3}
    assert out["description"]["confidence"] == 0.4


def test_plain_text_description_is_first_sentence():
    out = suggestion_from_text("a red drill. It sits on a table.", {})
    assert out["description"]["value"] == "A red drill."


def test_long_phrase_gives_description_only():
    out = suggestion_from_text(
        "Several mismatched old kitchen utensils scattered loosely across the countertop", {}
    )
    assert "name" not in out and "description" in out


def test_plain_text_matches_existing_categories_and_tags():
    ctx = {"categories": ["Tools", "Kitchen"], "tags": ["Bosch", "blue", "red"]}
    out = suggestion_from_text("The image shows a red Bosch drill among other tools.", ctx)
    assert out["category"] == {"value": "Tools", "confidence": 0.3}
    assert [t["value"] for t in out["tags"]] == ["Bosch", "red"]


def test_prose_answer_to_json_prompt_is_used(oclient, fake, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    fake.chat_content = "The image shows a red Bosch cordless drill on a wooden table."
    body = assert_contract(post_image(oclient()), "/v1/suggest", "post")
    assert body["name"] == {"value": "Red Bosch cordless drill", "confidence": 0.3}
    assert len(fake.chats()) == 1  # no retry needed
    assert "ignored the JSON instructions" in _log_text(caplog)


def test_empty_json_retries_with_plain_prompt(oclient, fake, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    fake.chat_queue = ["{}", "A white ceramic coffee mug on a desk."]
    body = assert_contract(post_image(oclient()), "/v1/suggest", "post")
    assert body["name"]["value"] == "White ceramic coffee mug"
    first, second = fake.chats()
    assert first["format"]["type"] == "object"
    assert "format" not in second
    assert second["messages"][0]["content"] == PLAIN_PROMPT
    assert second["messages"][0]["images"] == first["messages"][0]["images"]
    assert second["options"]["num_predict"] == 256
    assert "unusable (json prompt)" in _log_text(caplog)


def test_plain_retry_disabled(oclient, fake):
    fake.chat_queue = ["{}", "A white ceramic coffee mug."]
    r = post_image(oclient(OLLAMA_PLAIN_RETRY="0"))
    assert r.status_code == 503 and r.json()["error"]["code"] == "backend_error"
    assert len(fake.chats()) == 1


def test_no_plain_retry_when_time_budget_spent(oclient, fake):
    fake.chat_queue = ["{}", "A white ceramic coffee mug."]
    fake.chat_delay = 0.5
    r = post_image(oclient(BACKEND_TIMEOUT_S="1"))  # 0.5 s used > 40% of 1 s
    assert r.status_code == 503 and r.json()["error"]["code"] == "backend_error"
    assert len(fake.chats()) == 1


def test_both_attempts_unusable_is_backend_error(oclient, fake, caplog):
    caplog.set_level(logging.WARNING, logger=LOGGER)
    fake.chat_queue = ["{}", "   "]
    r = post_image(oclient())
    assert r.status_code == 503 and r.json()["error"]["code"] == "backend_error"
    assert len(fake.chats()) == 2
    assert "unusable (plain prompt)" in _log_text(caplog)


CATS = ["Tools", "Electronics", "Clothing", "Books", "Kitchen", "Lighting", "Shoes", "Other"]


@pytest.mark.parametrize(
    ("model", "want"),
    [
        ("tools", "Tools"),
        ("ELECTRONICS", "Electronics"),
        ("clothes", "Clothing"),
        ("book", "Books"),
        ("shoe", "Shoes"),
        ("Cooking utensils", "Kitchen"),
        ("cookware", "Kitchen"),
        ("headphones", "Electronics"),
        ("laptop", "Electronics"),
        ("Phone", "Electronics"),
        ("desk lamp", "Lighting"),
        ("bulb", "Lighting"),
        ("sneakers", "Shoes"),
        ("artists", None),
        ("sculptor", None),
        ("furniture", None),
    ],
)
def test_snap_category(model, want):
    assert snap(model, CATS, CATEGORY_SYNONYMS) == want


def test_synonym_only_applies_when_target_is_listed():
    assert snap("cookware", ["Tools", "Other"], CATEGORY_SYNONYMS) is None
    assert snap("cookware", ["Tools", "kitchen"], CATEGORY_SYNONYMS) == "kitchen"


@pytest.mark.parametrize(
    ("s", "key"),
    [
        ("T-Shirts", "t shirt"),
        ("Clothes", "clothing"),
        ("Batteries", "battery"),
        ("Boxes", "box"),
        ("glass", "glass"),
        ("Electronics", "electronic"),
        ("", ""),
    ],
)
def test_match_key(s, key):
    assert match_key(s) == key


def test_off_list_category_is_dropped_flat_and_nested():
    ctx = {"categories": CATS}
    nested = {"name": "x", "category": {"value": "artists", "confidence": 0.64}}
    flat = {"name": "x", "category": "artists"}
    for raw in (nested, flat):
        assert "category" not in clamp(raw, ctx)


def test_nested_category_is_snapped_with_its_confidence():
    raw = {"name": "x", "category": {"value": "Cooking utensils", "confidence": 0.7}}
    out = clamp(raw, {"categories": CATS})
    assert out["category"] == {"value": "Kitchen", "confidence": 0.7}


def test_category_free_without_context_categories():
    assert clamp({"category": "artists"}, {})["category"] == {"value": "artists"}


@pytest.mark.parametrize(
    "v",
    [
        {"value": {"value": "Lamp", "confidence": 0.5}},
        [{"value": "Lamp", "confidence": 0.5}],
        {"value": ["Lamp"], "confidence": 0.5},
    ],
)
def test_odd_nesting_is_unwrapped(v):
    out = clamp({"name": v}, {})
    assert out["name"]["value"] == "Lamp" and out["name"]["confidence"] == 0.5


def test_tags_prefer_existing_spelling_but_allow_new():
    raw = {"tags": [{"value": "knives", "confidence": 0.6}, "Bosch", "stainless steel"]}
    out = clamp(raw, {"tags": ["Knife", "bosch"]})
    assert [t["value"] for t in out["tags"]] == ["Knife", "bosch", "stainless steel"]
    assert out["tags"][0]["confidence"] == 0.6


def test_snapped_tags_are_deduped():
    out = clamp({"tags": ["knives", "knife", "Knife"]}, {"tags": ["knife"]})
    assert out["tags"] == [{"value": "knife"}]


def test_top_level_confidence_fills_missing_ones():
    raw = {
        "name": "power drill",
        "confidence": 0.61,
        "category": {"value": "tools", "confidence": 0.9},
        "tags": ["red"],
    }
    out = clamp(raw, {"categories": CATS})
    assert out["name"] == {"value": "power drill", "confidence": 0.61}
    assert out["category"] == {"value": "Tools", "confidence": 0.9}
    assert out["tags"] == [{"value": "red"}]


@pytest.mark.parametrize("name", ["John", "Maria", "Nikos"])
def test_lone_first_name_is_not_a_name(name):
    out = clamp({"name": name, "category": "Books"}, {})
    assert "name" not in out and out["category"] == {"value": "Books"}


@pytest.mark.parametrize("name", ["Knives", "John Deere mower", "Jack", "Rose", "bose", "Lamp"])
def test_object_names_are_kept(name):
    assert clamp({"name": name}, {})["name"] == {"value": name}


def test_description_echoing_name_or_category_is_dropped():
    raw = {"name": "bulb", "category": {"value": "artists"}, "description": "Artists"}
    assert "description" not in clamp(raw, {})
    raw["description"] = {"value": "bulb", "confidence": 0.67}
    assert "description" not in clamp(raw, {})
    raw["description"] = "A frosted light bulb."
    assert clamp(raw, {})["description"] == {"value": "A frosted light bulb."}


# --- S2: the model was unloaded after the first warm-up --------------------


def test_load_time_does_not_use_up_the_retry_budget(oclient, fake):
    # 0.5 s of a 1 s timeout spent, but 0.45 s of it was Ollama loading the
    # model: generating took 0.05 s, so the plain retry still runs.
    fake.chat_queue = ["{}", "A white ceramic coffee mug."]
    fake.chat_delay = 0.25
    fake.load_duration_ns = 200_000_000
    r = post_image(oclient(BACKEND_TIMEOUT_S="1.5"))
    assert r.status_code == 200, r.text
    assert r.json()["name"]["value"] == "White ceramic coffee mug"
    assert len(fake.chats()) == 2


def test_no_retry_when_too_little_time_remains(oclient, fake):
    # Most of the time went on loading, but what is left is too short for a retry.
    fake.chat_queue = ["{}", "A white ceramic coffee mug."]
    fake.chat_delay = 0.9
    fake.load_duration_ns = 850_000_000
    r = post_image(oclient(BACKEND_TIMEOUT_S="1"))
    assert r.status_code == 503 and r.json()["error"]["code"] == "backend_error"
    assert len(fake.chats()) == 1


def test_info_not_ready_when_ollama_unloaded_the_model(oclient, fake):
    c = oclient()
    assert c.get("/v1/info").json()["ready"] is True
    fake.loaded = []  # keep_alive expired, Ollama restarted, ...
    assert c.get("/v1/info").json()["ready"] is False
    # The service reloads it in the background.
    assert _wait(lambda: any(p == "/api/generate" for p, _ in fake.requests))
    fake.loaded = ["moondream:latest"]
    assert c.get("/v1/info").json()["ready"] is True


def test_info_matches_tagged_model_names(oclient, fake):
    fake.loaded = ["qwen2.5vl:3b"]
    assert oclient(OLLAMA_MODEL="qwen2.5vl:3b").get("/v1/info").json()["ready"] is True
    assert oclient(OLLAMA_MODEL="moondream").get("/v1/info").json()["ready"] is False


def test_info_ready_when_ps_is_unavailable(oclient, fake):
    fake.loaded = None  # GET /api/ps answers 404
    assert oclient().get("/v1/info").json()["ready"] is True


def test_cold_model_gets_503_at_once_and_is_reloaded(oclient, fake):
    # Nothing measured yet, so 15 s load + 15 s answer is assumed, which does
    # not fit in 0.9 x 25 s: waiting would end in a 504. Answer 503 with
    # Retry-After straight away instead.
    fake.loaded = []
    c = oclient(BACKEND_TIMEOUT_S="25", OLLAMA_RETRY_AFTER_S="5")
    r = post_image(c)
    # 15 s assumed load + max(2 s, 30%) margin, rounded up.
    assert r.status_code == 503 and r.headers["Retry-After"] == "20"
    body = assert_contract(r, "/v1/suggest", "post")
    assert body["error"]["code"] == "backend_unavailable"
    assert "loading" in body["error"]["message"]
    assert fake.chats() == []
    assert _wait(lambda: any(p == "/api/generate" for p, _ in fake.requests))
    fake.loaded = ["moondream:latest"]
    assert post_image(c).status_code == 200


def test_cold_model_waits_for_load_when_it_fits(oclient, fake):
    # Jetson: 45 s timeout, so the assumed 15 s load + 15 s answer fits in
    # 40.5 s. The request goes straight to Ollama, which loads the model.
    fake.loaded = []
    fake.load_duration_ns = 300_000_000
    c = oclient(BACKEND_TIMEOUT_S="45")
    r = post_image(c)
    assert r.status_code == 200
    assert len(fake.chats()) == 1
    assert not any(p == "/api/generate" for p, _ in fake.requests)


def test_cold_wait_uses_measured_load_and_generation_times(oclient, fake):
    # 40 s timeout (limit 36 s): the 15 s + 15 s defaults fit, a measured
    # 18 s load plus a slow 19 s answer does not.
    c = oclient(BACKEND_TIMEOUT_S="40")
    backend = c.app.state.backend
    assert backend._cold_wait_fits()
    backend._record_load(18.0)
    backend._record(19.0)
    assert not backend._cold_wait_fits()
    fake.loaded = []
    r = post_image(c)
    assert r.status_code == 503 and "loading" in r.json()["error"]["message"]
    assert fake.chats() == []


def test_cold_check_assumes_at_least_15_s_load(oclient, fake):
    # Board regression: a reload timed at 10 s plus 11.3 s average answer
    # looked like it fit 0.9 x 25 s, and the cold request ran into a 504.
    # The load estimate never drops below 15 s, and the slowest recent answer
    # counts, not the average.
    c = oclient(BACKEND_TIMEOUT_S="25")
    backend = c.app.state.backend
    backend._record_load(10.0)
    for s_ in (13.5, 13.7, 5.9):
        backend._record(s_)
    assert backend._avg_s < 11.5 and backend._load_estimate_s() == 15.0
    assert not backend._cold_wait_fits()
    fake.loaded = []
    assert post_image(c).status_code == 503
    assert fake.chats() == []


def test_queue_check_uses_slowest_recent_answer(oclient, fake):
    # Board regression: one fast 5.9 s prompt-cache hit pulled the average
    # to ~11.3 s, so 2 x avg (22.3 s) slipped under 0.9 x 25 s and the queued
    # request was admitted and timed out. The slowest of the last answers
    # (13.7 s) decides: 27.4 s > 22.5 s, so 503 at once.
    c = oclient(BACKEND_TIMEOUT_S="25", OLLAMA_QUEUE_SIZE="1")
    backend = c.app.state.backend
    for s_ in (13.5, 13.7, 5.9):
        backend._record(s_)
    fake.release.clear()
    results: list = []
    t = _post_in_thread(c, results)
    assert _wait(lambda: len(fake.chats()) == 1)
    r = post_image(c)
    assert r.status_code == 503 and "would time out" in r.json()["error"]["message"]
    assert int(r.headers["Retry-After"]) >= 12
    fake.release.set()
    t.join(5)
    assert results[0].status_code == 200


def test_queue_check_forgets_old_slow_answers(oclient):
    c = oclient(BACKEND_TIMEOUT_S="25")
    backend = c.app.state.backend
    backend._record(30.0)
    for _ in range(5):
        backend._record(4.0)
    assert backend._slow_generate_s() == 4.0


def test_warm_load_time_is_not_mistaken_for_a_load(oclient, fake):
    # Ollama reports a few ms of load_duration on a loaded model; that must
    # not replace the load estimate.
    fake.load_duration_ns = 5_000_000
    c = oclient()
    assert post_image(c).status_code == 200
    assert c.app.state.backend._load_s is None


def test_cold_check_tolerates_old_ollama(oclient, fake):
    fake.loaded = None  # no /api/ps: assume loaded, as before
    assert post_image(oclient()).status_code == 200


def _post_in_thread(c, results):
    import threading

    t = threading.Thread(target=lambda: results.append(post_image(c)))
    t.start()
    return t


def test_queued_request_refused_when_it_would_time_out(oclient, fake):
    # Learned generation time 0.6 s, timeout 1 s: a queued request would need
    # about 1.2 s, so it gets 503 + Retry-After at once instead of a 504.
    fake.chat_delay = 0.6
    c = oclient(BACKEND_TIMEOUT_S="1", OLLAMA_QUEUE_SIZE="1", OLLAMA_RETRY_AFTER_S="1")
    assert post_image(c).status_code == 200  # learns the 0.6 s
    fake.release.clear()
    results: list = []
    t = _post_in_thread(c, results)
    assert _wait(lambda: len(fake.chats()) == 2)
    r = post_image(c)
    assert r.status_code == 503 and "would time out" in r.json()["error"]["message"]
    assert int(r.headers["Retry-After"]) >= 1
    fake.release.set()
    t.join(5)
    assert results[0].status_code == 200


def test_queued_request_admitted_when_it_fits(oclient, fake):
    fake.chat_delay = 0.1
    c = oclient(BACKEND_TIMEOUT_S="5", OLLAMA_QUEUE_SIZE="1")
    assert post_image(c).status_code == 200
    fake.release.clear()
    results: list = []
    t1 = _post_in_thread(c, results)
    assert _wait(lambda: len(fake.chats()) == 2)
    t2 = _post_in_thread(c, results)
    assert _wait(lambda: c.app.state.backend._waiting == 1)
    fake.release.set()
    t1.join(5)
    t2.join(5)
    assert [r.status_code for r in results] == [200, 200]


def test_info_latency_absent_before_first_request(oclient):
    body = assert_contract(oclient().get("/v1/info"), "/v1/info")
    assert body["ready"] is True and "avg_latency_ms" not in body


def test_info_latency_after_requests(oclient, fake):
    c = oclient()
    fake.chat_delay = 0.2
    assert post_image(c).status_code == 200
    first = assert_contract(c.get("/v1/info"), "/v1/info")["avg_latency_ms"]
    assert isinstance(first, int) and 180 <= first < 1500
    fake.chat_delay = 0.6
    assert post_image(c).status_code == 200
    second = assert_contract(c.get("/v1/info"), "/v1/info")["avg_latency_ms"]
    # Moving average: moves toward the slower request, but only 30% of the way.
    assert first < second < 600
    assert abs(second - (0.7 * first + 0.3 * 600)) < 150


def test_info_latency_excludes_model_load(oclient, fake):
    c = oclient()
    fake.chat_delay = 0.5
    fake.load_duration_ns = 400_000_000  # 0.4 s of the 0.5 s was loading
    assert post_image(c).status_code == 200
    assert c.get("/v1/info").json()["avg_latency_ms"] < 400


def test_info_latency_is_rounded_ms(oclient):
    c = oclient()
    c.app.state.backend._avg_s = 13.5404
    assert c.get("/v1/info").json()["avg_latency_ms"] == 13540

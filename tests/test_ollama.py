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
    clamp,
    extract_json,
    match_key,
    snap,
    suggestion_from_text,
    truncate_for_log,
)

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
    assert req["stream"] is False and req["format"] == "json"
    assert base64.b64decode(req["messages"][0]["images"][0]) == JPEG


def test_default_generation_options(oclient, fake):
    assert post_image(oclient()).status_code == 200
    req = fake.chats()[-1]
    assert req["options"] == {"temperature": 0.0, "num_predict": 256}
    assert req["keep_alive"] == "30m"
    assert req["format"] == "json"


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


def test_prompt_without_context_has_defaults():
    p = build_prompt({})
    assert "locale 'en'" in p and "Prefer these" not in p and "choose exactly" not in p


def test_prompt_lists_categories_as_a_closed_choice():
    p = build_prompt({"categories": ["Tools", " ", "Kitchen"], "tags": ["bosch"]})
    assert 'choose exactly one of ["Tools", "Kitchen"]' in p
    assert 'Prefer these: ["bosch"]' in p
    assert "brand or a person's name" in p and "object only" in p
    assert len(p) < 800  # small models do better with short prompts


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

    c = oclient(OLLAMA_RETRY_AFTER_S="3")
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
    assert out == {"name": {"value": "Lamp"}, "category": {"value": "Lighting"}}


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
            "Pair of black headphones",
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
    assert first["format"] == "json"
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

"""Regression tests from real Moondream answers captured on the Jetson.

tests/fixtures/moondream_jetson_raw.json holds the raw /api/chat content for
the nine benchmark photos, sent with the category list in its "context".
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import assert_contract, post_image
from ollama_mock import FakeOllama

from app.backends.ollama import parse_suggestion

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "moondream_jetson_raw.json").read_text())
CONTEXT = FIXTURE["context"]
RAW = {o["photo"]: o["raw"] for o in FIXTURE["outputs"]}

# What the service returns for each answer: (name, category, tags, description).
# None means the field is absent. Wrong-but-valid picks (a drill in Electronics,
# a t-shirt in Books) are the model's; the parser can only keep it on the list.
EXPECTED = {
    "cordless_drill.jpg": ("power drill", "Electronics", None, None),
    "desk_lamp.jpg": ("bulb", "Lighting", ["bulb"], None),
    "hammer.jpg": ("sculptor", None, ["sculptor", "artists"], None),
    "headphones.jpg": ("bose", "Electronics", ["bose"], None),
    "kitchen_knife.jpg": ("Knives", "Kitchen", ["knives", "kitchen"], None),
    "laptop.jpg": ("smartphone", "Other", None, None),
    "paperback_book.jpg": ("books", "Books", ["books"], None),
    "sneakers.jpg": ("reebok", "Clothing", ["reebok"], None),
    "t-shirt.jpg": (None, "Books", ["books"], None),
}


def _value(out, key):
    return out[key]["value"] if key in out else None


def test_fixture_covers_every_photo():
    assert set(RAW) == set(EXPECTED) and len(RAW) == 9


@pytest.mark.parametrize("photo", sorted(EXPECTED))
def test_raw_answer_parses_to_expected(photo):
    out, how = parse_suggestion(RAW[photo], CONTEXT)
    name, category, tags, description = EXPECTED[photo]
    assert how == "json"
    assert _value(out, "name") == name
    assert _value(out, "category") == category
    assert ([t["value"] for t in out["tags"]] if "tags" in out else None) == tags
    assert _value(out, "description") == description


@pytest.mark.parametrize("photo", sorted(EXPECTED))
def test_category_is_always_on_the_clients_list(photo):
    out, _ = parse_suggestion(RAW[photo], CONTEXT)
    assert "category" not in out or out["category"]["value"] in CONTEXT["categories"]


@pytest.mark.parametrize("photo", sorted(EXPECTED))
def test_confidences_survive_flat_and_nested_answers(photo):
    out, _ = parse_suggestion(RAW[photo], CONTEXT)
    for key in ("name", "category"):
        if key in out:
            assert 0.6 <= out[key]["confidence"] <= 0.7


@pytest.fixture
def fake():
    f = FakeOllama()
    yield f
    f.close()


@pytest.mark.parametrize(
    ("photo", "category"),
    [("hammer.jpg", None), ("sneakers.jpg", "Clothing"), ("desk_lamp.jpg", "Lighting")],
)
def test_off_list_categories_never_reach_the_client(make_client, fake, photo, category):
    fake.chat_content = RAW[photo]
    client = make_client(BACKEND="ollama", OLLAMA_URL=fake.url, OLLAMA_WARMUP="0")
    body = assert_contract(post_image(client, context=json.dumps(CONTEXT)), "/v1/suggest", "post")
    assert body.get("category", {}).get("value") == category

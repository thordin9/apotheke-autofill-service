"""Regression tests from real qwen2.5vl-jetson:3b answers captured on the
Jetson (the default model there). Qwen follows the schema and is right on all
nine photos, so the parser must pass its answers through unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backends.ollama import build_prompt, clean_tags, parse_suggestion

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "qwen_jetson_raw.json").read_text())
CONTEXT = FIXTURE["context"]
RAW = {o["photo"]: o["raw"] for o in FIXTURE["outputs"]}

# (name, category) the service returns for each photo.
EXPECTED = {
    "cordless_drill.jpg": ("drill", "Tools"),
    "desk_lamp.jpg": ("blue light", "Lighting"),
    "hammer.jpg": ("hammer", "Tools"),
    "headphones.jpg": ("headphones", "Electronics"),
    "kitchen_knife.jpg": ("knife", "Kitchen"),
    "laptop.jpg": ("laptop", "Electronics"),
    # The description says "unbound white paper sheets", but Qwen is sure
    # (0.95) of "book", so its name is kept.
    "paperback_book.jpg": ("book", "Books"),
    "sneakers.jpg": ("sneaker", "Shoes"),
    "t-shirt.jpg": ("T-shirt", "Clothing"),
}


def test_fixture_covers_all_photos():
    assert set(RAW) == set(EXPECTED)


@pytest.mark.parametrize("photo", sorted(EXPECTED))
def test_qwen_answer_passes_through(photo):
    out, how = parse_suggestion(RAW[photo], CONTEXT)
    assert how == "json"
    assert (out["name"]["value"], out["category"]["value"]) == EXPECTED[photo]
    assert out["name"]["confidence"] == 0.95
    assert out["description"]["value"]
    assert 1 <= len(out["tags"]) <= 5


# QA Q1: with "(material, colour" in the prompt, Qwen answered those words as
# tags on 4 of 9 photos. Tags expected after cleaning, per photo.
LEAK = FIXTURE["prompt_leak"]
LEAK_EXPECTED = {
    "sneakers.jpg": ["leather", "white", "red"],
    "desk_lamp.jpg": [],
    "paperback_book.jpg": [],
    "t-shirt.jpg": [],
}


@pytest.mark.parametrize("photo", sorted(LEAK_EXPECTED))
def test_prompt_words_do_not_leak_into_tags(photo):
    raw = {o["photo"]: o["raw"] for o in LEAK["outputs"]}[photo]
    out, how = parse_suggestion(raw, LEAK["context"])
    assert how == "json"
    assert [t["value"] for t in out.get("tags", [])] == LEAK_EXPECTED[photo]
    assert out["name"]["value"] and out["category"]["value"]


@pytest.mark.parametrize(
    "tags,expected",
    [
        (["material", "colour", "sneaker"], ["sneaker"]),
        (["material: leather", "sport"], ["leather", "sport"]),
        (["colour: white, red"], ["white", "red"]),
        (["Material", "COLOR", "Brand", "tag", "tags", "category"], []),
        (["name", "description", "item", "object", "red"], ["red"]),
        (["material:", "brand: ", "colour: ,"], []),
        (["leather", "material: leather", "Leather", "white"], ["leather", "white"]),
        (["brand: reebok", "colour: white"], ["reebok", "white"]),
        # Not a prompt word before the colon: left alone.
        (["size: 42", "10:30 clock"], ["size: 42", "10:30 clock"]),
        # Words that only contain a prompt word stay.
        (["materials", "multicolour", "itemized"], ["materials", "multicolour", "itemized"]),
    ],
)
def test_clean_tags(tags, expected):
    assert clean_tags(tags) == expected


def test_clean_tags_keeps_scored_shape():
    tags = [{"value": "material: leather", "confidence": 0.6}, {"value": "colour"}, 5]
    assert clean_tags(tags) == [{"value": "leather", "confidence": 0.6}, 5]


def test_prompt_lists_no_example_tag_words():
    prompt = build_prompt({"tags": ["bosch", "red"]}).casefold()
    tag_line = next(line for line in prompt.splitlines() if line.startswith("tags"))
    assert "material" not in tag_line and "colour" not in tag_line
    assert "brand" not in tag_line
    assert "prefer: bosch, red" in tag_line

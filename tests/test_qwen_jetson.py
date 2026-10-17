"""Regression tests from real qwen2.5vl-jetson:3b answers captured on the
Jetson (the default model there). Qwen follows the schema and is right on all
nine photos, so the parser must pass its answers through unchanged."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.backends.ollama import parse_suggestion

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

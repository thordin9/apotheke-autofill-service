"""Prompt and schema variants for the brand / model number / serial number experiment.

Each variant starts from the service's own prompt and schema (app.backends.ollama.build_prompt /
build_schema, passed in so this module needs no app import) and adds three optional fields. All
variants tell the model to copy only text it can read on the item and never to guess.

  base     the service prompt and schema, unchanged (the reference for name/category/latency)
  v1_empty the three fields are required; "" when the text is not printed or not readable
  v2_omit  the three fields are optional: the model leaves out what it cannot read (fewest
           tokens when nothing is readable); stricter "copy character by character" wording
  v3_lean  like v1_empty, but tags are cut to 3 and the description to about 12 words, to win
           back the extra output tokens

Print the exact prompt and schema of a variant:  python3 bench/brand_variants.py v1_empty
"""

import copy
import json
import sys

FIELDS = ("brand", "model_number", "serial_number")
FIELD_MAX = 30
LOCALE_PREFIX = "Write the values in the language of locale"

V1_TEXT = (
    "brand, model_number, serial_number (copy only text that is printed on the item and clearly "
    'readable in this photo; use "" for any of them you cannot read; never guess).'
)
V2_TEXT = (
    "Only if printed on the item and clearly readable in this photo, also give: brand (the maker's "
    "name as written), model_number and serial_number (copied character by character, with dashes "
    "and spaces as printed). Leave out any of these three you cannot read; do not infer them from "
    "the shape, style or kind of object."
)
V3_TAGS = "tags (up to 3 short lowercase tags for the item),"
V3_DESC = "description (under 12 words, about the object only),"

VARIANTS = ("base", "v1_empty", "v2_omit", "v3_lean")


def _insert_before_locale(prompt, line):
    lines = prompt.split("\n")
    for i, text in enumerate(lines):
        if text.startswith(LOCALE_PREFIX):
            return "\n".join(lines[:i] + [line] + lines[i:])
    return prompt + "\n" + line


def _add_fields(schema, required):
    out = copy.deepcopy(schema)
    for f in FIELDS:
        out["properties"][f] = {"type": "string", "maxLength": FIELD_MAX}
    if required:
        out["required"] = list(out["required"]) + list(FIELDS)
    return out


def build(variant, base_prompt, base_schema):
    """Return (prompt, schema) for a variant, built from the service's prompt and schema."""
    if variant == "base":
        return base_prompt, copy.deepcopy(base_schema)
    if variant == "v1_empty":
        return _insert_before_locale(base_prompt, V1_TEXT), _add_fields(base_schema, True)
    if variant == "v2_omit":
        return _insert_before_locale(base_prompt, V2_TEXT), _add_fields(base_schema, False)
    if variant == "v3_lean":
        lines = []
        for text in base_prompt.split("\n"):
            if text.startswith("tags ("):
                text = V3_TAGS
            elif text.startswith("description ("):
                text = V3_DESC
            lines.append(text)
        schema = _add_fields(base_schema, True)
        schema["properties"]["tags"]["maxItems"] = 3
        schema["properties"]["description"]["maxLength"] = 100
        return _insert_before_locale("\n".join(lines), V1_TEXT), schema
    raise ValueError(f"unknown variant {variant!r}; known: {', '.join(VARIANTS)}")


if __name__ == "__main__":
    from app.backends.ollama import build_prompt, build_schema  # needs PYTHONPATH=<repo root>

    cats = ["General", "Tools", "Electronics", "Kitchen", "Documents"]
    cats += ["Hobby", "Clothing", "Shoes", "Books", "Lighting"]
    ctx = {"categories": cats, "locale": "en"}
    p, s = build(sys.argv[1], build_prompt(ctx), build_schema(ctx))
    print(p)
    print(json.dumps(s, indent=2))

"""Brand, model number and serial number on a suggestion.

The model is asked to copy only text printed on the item. These values are
trimmed, stripped of controls and lone surrogates, hard-cut to 128
characters, and they are never written to logs.
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any

IDENTITY_FIELDS = ("brand", "model_number", "serial_number")
MAX_IDENTITY = 128

# Folded keys (letters and digits only, lower case) the model may use.
_ALIASES: dict[str, tuple[str, ...]] = {
    "brand": ("brand", "brandname", "manufacturer", "make"),
    "model_number": ("modelnumber", "modelno", "modelnum", "productmodel", "model"),
    "serial_number": ("serialnumber", "serialno", "serialnum", "serial", "sn"),
}

# Tokens that mean "not readable", not a value copied off the item.
_UNREADABLE = {
    "str",
    "string",
    "...",
    "…",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "undefined",
}

_SENSITIVE = {alias for aliases in _ALIASES.values() for alias in aliases}
_MISSING = object()
_SECRET_KEY = (
    "brand|brand_name|brandName|manufacturer|make|"
    "model_number|modelNumber|model_no|model_num|product_model|productModel|model|"
    "serial_number|serialNumber|serial_no|serial_num|serial|sn"
)
_SECRET_STRING = r'"(?:\\.|[^"\\])*"'
# Keys as the model may spell them ("Brand Name", "Serial #", "S/N"), any value
# shape up to the next top-level comma or the end of a truncated answer.
_REDACT_VALUE = re.compile(
    r'("[^"]*?(?:brand|manufacturer|make|model|serial|s/?n)[^"]*"\s*:\s*)'
    rf"(?:{_SECRET_STRING}|\[[^\]]*\]?|\{{[^}}]*\}}?|[^,}}\]]*)",
    re.IGNORECASE,
)


def _norm_key(key: Any) -> str:
    return re.sub(r"[\W_]+", "", str(key).strip().casefold())


def _as_text(value: Any, depth: int = 0) -> str | None:
    """Plain string, or the same shapes name/category already unwrap."""
    if isinstance(value, str):
        return value
    if depth > 3:
        return None
    if isinstance(value, dict) and "value" in value:
        return _as_text(value.get("value"), depth + 1)
    if isinstance(value, list):
        for item in value:
            text = _as_text(item, depth + 1)
            if text and text.strip():
                return text
    return None


_LINE_BREAK = re.compile(r"[\r\n\u2028\u2029\v\f]+")


def clean_category(text: str) -> str:
    """Fold line breaks, then drop C0/C1 controls and bidi overrides.

    The same control classes as the brand fields. Line breaks become a space
    first so a category name stays one prompt line. Callers that must reject
    lone surrogates check that the text encodes as UTF-8 before this runs:
    a surrogate is a control class here and would otherwise disappear.
    """
    return _printable(_LINE_BREAK.sub(" ", text)).strip()


def _printable(text: str) -> str:
    """Drop C0/C1 controls, lone surrogates (they made the response 500) and the
    bidi embedding/override/isolate controls. RLM/LRM, ZWJ/ZWNJ stay."""
    return "".join(
        ch
        for ch in text
        if unicodedata.category(ch) not in ("Cc", "Cs")
        and not ("\u202a" <= ch <= "\u202e" or "\u2066" <= ch <= "\u2069")
    )


def _cut(text: str, limit: int) -> str:
    """At most `limit` code points, never ending inside a combining or ZWJ sequence."""
    if len(text) <= limit:
        return text
    end = limit
    while end > 0 and (
        unicodedata.combining(text[end])
        or text[end] == "\u200d"
        or text[end - 1] == "\u200d"
        or "\ufe00" <= text[end] <= "\ufe0f"
    ):
        end -= 1
    return text[:end].rstrip()


def clean_identity(value: Any) -> str:
    """Trim, drop unreadable placeholders, hard-cut to MAX_IDENTITY characters."""
    text = _as_text(value)
    if text is None:
        return ""
    text = _printable(text).strip()
    if not text or text.casefold() in _UNREADABLE:
        return ""
    return _cut(text, MAX_IDENTITY)


def _sources(raw: dict[str, Any]) -> list[dict[str, Any]]:
    sources = [raw]
    if len(raw) == 1:
        inner = next(iter(raw.values()))
        if isinstance(inner, dict):
            sources.append(inner)
    return sources


def _lookup(source: dict[str, Any], aliases: tuple[str, ...]) -> Any:
    folded: dict[str, Any] = {}
    for key, value in source.items():
        folded.setdefault(_norm_key(key), value)
    for alias in aliases:
        if alias in folded:
            return folded[alias]
    return _MISSING


def identity_fields(raw: Any) -> dict[str, str]:
    """brand, model_number, serial_number. Missing or unreadable → \"\"."""
    sources = _sources(raw) if isinstance(raw, dict) else []
    out: dict[str, str] = {}
    for key, aliases in _ALIASES.items():
        found: Any = _MISSING
        for source in sources:
            found = _lookup(source, aliases)
            if found is not _MISSING:
                break
        out[key] = "" if found is _MISSING else clean_identity(found)
    return out


def _redact_inplace(obj: Any) -> None:
    if isinstance(obj, dict):
        for key, value in list(obj.items()):
            if _norm_key(key) in _SENSITIVE:
                obj[key] = "[redacted]"
            else:
                _redact_inplace(value)
    elif isinstance(obj, list):
        for item in obj:
            _redact_inplace(item)


def redact_for_log(text: str) -> str:
    """Remove brand, model number and serial values before anything is logged."""
    body = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", body, re.DOTALL)
    if fenced:
        body = fenced.group(1).strip()
    try:
        obj = json.loads(body)
    except ValueError:
        obj = None
    if isinstance(obj, dict):
        _redact_inplace(obj)
        return json.dumps(obj, ensure_ascii=False)
    return _REDACT_VALUE.sub(r'\1"[redacted]"', text)

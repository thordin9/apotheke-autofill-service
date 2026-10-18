"""Ollama backend: asks a local vision model (Moondream by default) for a
Suggestion as strict JSON, then validates and clamps the answer.

Env (read when the backend is created):
  OLLAMA_URL    default http://ollama:11434
  OLLAMA_MODEL  default moondream (qwen2-vl-2b style tags also work)
  OLLAMA_WARMUP          1 (default) loads the model in the background at startup;
                         /v1/info reports ready:false and /v1/suggest answers 503
                         with Retry-After until it has loaded. 0 skips warm-up.
  OLLAMA_QUEUE_SIZE      requests allowed to wait behind the one in flight (default 1)
  OLLAMA_RETRY_AFTER_S   Retry-After seconds on 503 busy/warming (default 5; busy
                         answers use the recent generation time if it is longer).
                         A model that Ollama unloaded gets its own load estimate
                         instead: max(15, last measured load) minus the time its
                         background load has already run, at least 1.
                         A request that finds the model unloaded waits for the load
                         when last load time + recent generation time (15 s each
                         until measured) fit in 90% of BACKEND_TIMEOUT_S; otherwise
                         it gets 503 at once and the model reloads in the background.
  ALLOWED_MODELS         comma-separated models a client may pick with context.model
                         (OLLAMA_MODEL is always allowed and is the default). Only
                         models Ollama has installed (GET /api/tags, cached 30 s) are
                         offered in /v1/info and used; others fall back to the default.
                         A model other than the loaded one is a cold start (above).
  OLLAMA_NUM_PREDICT     max tokens the model may generate per request (default 256;
                         0 or less removes the cap). Stops runaway output, e.g. a
                         small model padding format=json output with whitespace.
  OLLAMA_TEMPERATURE     sampling temperature (default 0, deterministic)
  OLLAMA_KEEP_ALIVE      how long Ollama keeps the model loaded after a request
                         (default 30m; -1 keeps it loaded forever)
  OLLAMA_JSON_FORMAT     schema (default, also 1) sends a JSON schema as `format`, so
                         Ollama constrains the answer to it (category = enum of the
                         request's categories); json sends format=json; 0 lets the
                         model answer freely
  OLLAMA_PLAIN_RETRY     1 (default): if the JSON answer is unusable and less than 40%
                         of BACKEND_TIMEOUT_S was spent generating (model load time
                         does not count), ask once more with a short plain-text
                         prompt and derive name/description from the text
  OLLAMA_LOG_RAW         1 logs every raw model answer at DEBUG (default 0). Unusable
                         answers are always logged at WARNING. Logged text is cut to
                         2 KB; the image and the request token are never logged.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import json
import logging
import math
import os
import re
import threading
import time
from typing import Any

import httpx

from ..settings import get_settings
from .base import CAPABILITIES, Backend, BackendUnavailable, Busy
from .selection import allowlist, cold_wait_fits, model_key, resolve_model, same_model

log = logging.getLogger(__name__)

DEFAULT_URL = "http://ollama:11434"
DEFAULT_MODEL = "moondream"
DEFAULT_NUM_PREDICT = 256
DEFAULT_KEEP_ALIVE = "30m"
# Estimates for the cold-model check until the service has measured them:
# Qwen2.5-VL 3B on a Xavier NX loads in about 15 s and answers in about 13.5 s.
DEFAULT_LOAD_S = 15.0
DEFAULT_GENERATE_S = 15.0
# A load_duration below this is a model that was already in memory.
MIN_LOAD_S = 0.5
# How many recent generation times the admission checks look at.
RECENT_WINDOW = 5

MAX_TAGS = 10
MAX_NAME = 120
MAX_CATEGORY = 80
MAX_TAG = 40
MAX_DESCRIPTION = 500
MAX_CONTEXT_ITEMS = 50
RAW_LOG_LIMIT = 2048


class BackendError(BackendUnavailable):
    """Upstream answered but the answer was unusable (maps to backend_error)."""

    code = "backend_error"


def build_prompt(context: dict[str, Any]) -> str:
    """Short prompt that names the fields in words. It deliberately shows no
    JSON template: small models (Moondream) copy a template's placeholders or
    example values back instead of looking at the photo. The reply's shape
    comes from the JSON schema sent as Ollama's `format` (see build_schema)."""
    cats = _context_list(context, "categories")
    tags = _context_list(context, "tags")
    locale = context.get("locale") or "en"
    parts = [
        "What is the main object in this photo? Answer in JSON with:",
        "name (the kind of object in 1-4 plain words, never a brand or a person's name),",
    ]
    if cats:
        parts.append("category (exactly one of: " + ", ".join(cats) + "),")
    else:
        parts.append("category (one general word),")
    # Tags are described in neutral words only: Qwen copied listed examples
    # ("material", "colour") back as tags.
    tag_part = "tags (up to 5 short lowercase tags for the item"
    if tags:
        tag_part += "; prefer: " + ", ".join(tags)
    parts += [
        tag_part + "),",
        "description (one short sentence about the object only, not people or background),",
        "confidence (0 to 1, how sure you are).",
        f"Write the values in the language of locale '{locale}'.",
    ]
    return "\n".join(parts)


def _context_list(context: dict[str, Any], key: str) -> list[str]:
    seen: set[str] = set()
    out = []
    for v in context.get(key, []) or []:
        if isinstance(v, str) and v.strip() and v.strip() not in seen:
            seen.add(v.strip())
            out.append(v.strip())
    return out[:MAX_CONTEXT_ITEMS]


def build_schema(context: dict[str, Any]) -> dict[str, Any]:
    """JSON schema for Ollama's `format`: Ollama only lets the model produce
    text matching it, so there is nothing to echo and, when the client sends
    categories, the category is always one of them."""
    category: dict[str, Any] = {"type": "string"}
    if cats := _context_list(context, "categories"):
        category["enum"] = cats
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 2, "maxLength": 40},
            "category": category,
            "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
            "description": {"type": "string", "minLength": 10, "maxLength": 200},
            "confidence": {"type": "number"},
        },
        "required": ["name", "category", "tags", "description", "confidence"],
    }


def _repair_truncated(text: str, start: int) -> dict[str, Any] | None:
    """Close a JSON object cut off mid-way (e.g. by num_predict).

    Tries, newest first, every point where a value had just ended, closing
    the brackets still open there; a value cut mid-string is dropped.
    """
    stack: list[str] = []
    cuts: list[tuple[int, str]] = []
    in_str = esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if not stack or stack.pop() != ch or not stack:
                return None  # mismatched, or a complete object raw_decode rejected
            cuts.append((i + 1, "".join(reversed(stack))))
        elif ch == ",":
            cuts.append((i, "".join(reversed(stack))))
    if not stack:
        return None
    candidates = [text[start:].rstrip().rstrip(",") + "".join(reversed(stack))]
    candidates += [text[start:pos] + closers for pos, closers in reversed(cuts)]
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj:
            return obj
    return None


def extract_json(text: str) -> dict[str, Any]:
    """Find the first JSON object in model output (tolerates fences/prose and
    an object truncated by the token cap)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = decoder.raw_decode(text, m.start())
        except ValueError:
            # Only an object still open at the end of the text is repaired, so
            # a nested object is never mistaken for the whole answer.
            obj = _repair_truncated(text, m.start())
        if isinstance(obj, dict):
            return obj
    raise BackendError("model output was not a JSON object")


def truncate_for_log(text: str, limit: int = RAW_LOG_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[{len(text) - limit} more chars]"


def _enable_debug_logging() -> None:
    # OLLAMA_LOG_RAW should work without extra logging config: uvicorn leaves
    # app loggers at WARNING with no handler, so give this one its own.
    if log.getEffectiveLevel() > logging.DEBUG:
        log.setLevel(logging.DEBUG)
    if not log.hasHandlers():
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        log.addHandler(h)


def _clip(s: str, limit: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


# Values small models copy from the prompt's schema instead of answering.
PLACEHOLDERS = {"str", "string", "...", "…", "0..1", "n/a", "na", "none", "null", "unknown"}

# Other keys small models use for our fields (Moondream often renames them).
ALIASES = {
    "name": ("item", "object", "product", "title", "label", "item_name", "product_name"),
    "category": ("type", "kind", "class", "item_category"),
    "description": ("caption", "summary", "details", "desc"),
    "tags": ("keywords", "labels", "attributes"),
}
FIELDS = tuple(ALIASES)


def _confidence(c: Any) -> float | None:
    if isinstance(c, str):
        try:
            c = float(c.strip().rstrip("%")) / (100 if c.strip().endswith("%") else 1)
        except ValueError:
            return None
    if isinstance(c, int | float) and not isinstance(c, bool) and c == c:  # not NaN
        return min(1.0, max(0.0, float(c)))
    return None


def _unwrap(v: Any, depth: int = 0) -> tuple[str | None, Any]:
    """Return (text, confidence) from a plain string, a {"value", "confidence"}
    object (nested any way a small model likes), or a list of those."""
    if isinstance(v, str):
        return v, None
    if depth > 3:
        return None, None
    if isinstance(v, dict) and "value" in v:
        text, c = _unwrap(v["value"], depth + 1)
        return text, v.get("confidence", c)
    if isinstance(v, list):
        for item in v:
            text, c = _unwrap(item, depth + 1)
            if text and text.strip():
                return text, c
    return None, None


# Singular/plural matching for category and tag snapping. Deliberately small:
# it only has to make "clothes" meet "Clothing" and "tools" meet "Tools".
_IRREGULAR = {"clothes": "clothing", "knives": "knife", "shelves": "shelf", "people": "person"}


def _singular(word: str) -> str:
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def match_key(s: str) -> str:
    """Case-, punctuation- and plural-insensitive key ("T-Shirts" -> "t shirt")."""
    words = re.sub(r"[\W_]+", " ", s.casefold()).split()
    return " ".join([*words[:-1], _singular(words[-1])]) if words else ""


# Model category (as match_key) -> category it means (as match_key). Used only
# when that category is in the request's list; English only, like the prompt.
_SYNONYMS_BY_TARGET = {
    "kitchen": "cooking utensil|kitchen utensil|utensil|cookware|kitchenware|knife|cutlery|pan|pot",
    "electronic": "headphone|earphone|headset|laptop|computer|phone|smartphone|mobile phone"
    "|cell phone|tablet|camera|gadget|electronic device",
    "lighting": "lamp|light|bulb|light bulb|lantern",
    "shoe": "sneaker|boot|footwear|sandal|trainer",
    "clothing": "apparel|garment|shirt|t shirt|jacket|sweater",
    "tool": "hardware|power tool|drill|hammer|screwdriver|wrench",
    "book": "novel|paperback|hardcover",
}
CATEGORY_SYNONYMS = {
    word: target for target, words in _SYNONYMS_BY_TARGET.items() for word in words.split("|")
}


def snap(value: str, choices: list[str], synonyms: dict[str, str] | None = None) -> str | None:
    """Return the entry of choices that value means, in its spelling, or None.

    Tries exact (case-insensitive), then singular/plural, then synonyms (whole
    phrase, then its last word) whose target is one of the choices.
    """
    choices = [c for c in choices if isinstance(c, str) and c.strip()]
    exact = {c.casefold(): c for c in choices}
    if (hit := exact.get(value.casefold())) is not None:
        return hit
    by_key: dict[str, str] = {}
    for c in choices:
        by_key.setdefault(match_key(c), c)
    key = match_key(value)
    if (hit := by_key.get(key)) is not None:
        return hit
    if synonyms and key:
        for k in (key, key.split()[-1]):
            if (target := synonyms.get(k)) is not None and target in by_key:
                return by_key[target]
    return None


def _scored(
    v: Any,
    limit: int,
    choices: list[str] | None = None,
    *,
    strict: bool = False,
    synonyms: dict[str, str] | None = None,
    default_confidence: float | None = None,
) -> dict[str, Any] | None:
    """Unwrap, clip and clean one field. With choices, a value meaning one of
    them takes its spelling; with strict, any other value is dropped."""
    text, c = _unwrap(v)
    if text is None:
        return None
    value = _clip(text, limit)
    if not value or value.casefold() in PLACEHOLDERS:
        return None
    if choices:
        hit = snap(value, choices, synonyms)
        if hit is None and strict:
            return None
        value = hit or value
    out: dict[str, Any] = {"value": value}
    if (conf := _confidence(c)) is not None or (conf := default_confidence) is not None:
        out["confidence"] = conf
    return out


def normalize(raw: dict[str, Any]) -> dict[str, Any]:
    """Map renamed keys onto ours and unwrap a single wrapper object."""
    lowered = {str(k).strip().casefold(): v for k, v in raw.items()}
    if not any(k in lowered for k in FIELDS) and len(lowered) == 1:
        (inner,) = lowered.values()
        if isinstance(inner, dict):
            return normalize(inner)
    out: dict[str, Any] = {}
    for key, aliases in ALIASES.items():
        for k in (key, *aliases):
            if lowered.get(k) not in (None, "", [], {}):
                out[key] = lowered[k]
                break
    if "confidence" in lowered:
        out["confidence"] = lowered["confidence"]
    tags = out.get("tags")
    if isinstance(tags, str):
        out["tags"] = [t for t in re.split(r"[,;]", tags) if t.strip()]
    elif isinstance(tags, dict):
        out["tags"] = [tags]
    return out


# Moondream sometimes "names" a photo of someone holding or wearing the item
# after the person ("John"). A lone capitalised common first name is never an
# object name; words that are also objects (Jack, Rose, Penny...) are left out.
_FIRST_NAMES = """
    john james michael david robert william richard thomas peter paul george daniel matthew
    andrew christopher joseph kevin brian steven eric tom mike dave steve chris nick alex
    sam ben tim dan jim joe tony anthony charles edward henry jacob ethan noah liam lucas
    oliver leon mary maria anna sarah emma laura julia lisa linda susan jennifer elizabeth
    emily olivia sophia sophie hannah jessica michelle anne jane alice helen kate karen
    nancy chloe mia ella amelia charlotte hans klaus stefan markus andreas nikos giorgos
    eleni dimitris yannis kostas
"""
FIRST_NAMES = frozenset(_FIRST_NAMES.split())


def _is_person_name(value: str) -> bool:
    return value.isalpha() and value.istitle() and value.casefold() in FIRST_NAMES


def _words(text: str) -> set[str]:
    return {_singular(w) for w in re.findall(r"[^\W\d_]+", text.casefold())}


def _name_agreeing_with(
    name: dict[str, Any] | None, description: dict[str, Any] | None, overall: float | None
) -> dict[str, Any] | None:
    """Moondream's name is often a brand or a person ("bose", "reebok", "John")
    while its description names the object ("A pair of black headphones...").
    When the name shares no word with the description's object noun phrase,
    take that phrase instead."""
    if description is None or len(description["value"].split()) < 4:
        return name
    derived = caption_name(description["value"])
    if derived is None:
        return name
    # Compare with the object phrase, not the whole sentence: a brand often
    # appears later in it ("...with a Reebok logo").
    if name is not None and _words(name["value"]) & _words(derived):
        return name
    # A model that is sure of its name (Qwen answers 0.95) is trusted: its
    # "book" for "a stack of unbound paper sheets" is the better name.
    if name is not None and (name.get("confidence") or 0) >= TRUSTED_NAME_CONFIDENCE:
        return name
    out: dict[str, Any] = {"value": _cap(derived)}
    conf = name.get("confidence") if name else overall
    if conf is not None:
        out["confidence"] = min(conf, DERIVED_NAME_CONFIDENCE)
    else:
        out["confidence"] = DERIVED_NAME_CONFIDENCE
    return out


# Words from the prompt itself; a tag that is only one of these says nothing.
PROMPT_WORDS = frozenset(
    {
        "material",
        "colour",
        "color",
        "brand",
        "tag",
        "tags",
        "category",
        "name",
        "description",
        "item",
        "object",
    }
)


def clean_tags(tags: list[Any]) -> list[Any]:
    """Keep prompt words out of tags: drop a tag that is only a prompt word
    ("material"), turn "material: leather" into "leather" (a comma-separated
    value such as "colour: white, red" becomes "white" and "red"), and drop
    duplicates, keeping the first. Tags given as {"value", ...} keep their
    other keys."""
    out: list[Any] = []
    seen: set[str] = set()
    for t in tags:
        value, rest = (t.get("value"), t) if isinstance(t, dict) else (t, None)
        if not isinstance(value, str):
            out.append(t)
            continue
        values = [value]
        head, sep, tail = value.partition(":")
        if sep and head.strip().casefold() in PROMPT_WORDS:
            values = tail.split(",")
        for v in values:
            v = v.strip()
            key = v.casefold()
            if not v or key in PROMPT_WORDS or key in seen:
                continue
            seen.add(key)
            out.append({**rest, "value": v} if rest is not None else v)
    return out


def clamp(raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    raw = normalize(raw)
    # Moondream often answers flat with a single top-level confidence; it
    # stands in for fields that carry none of their own.
    overall = _confidence(raw.get("confidence"))
    out: dict[str, Any] = {}
    name = _scored(raw.get("name"), MAX_NAME, default_confidence=overall)
    if name is not None and _is_person_name(name["value"]):
        name = None
    # When the client lists its categories, the answer must be one of them.
    if (
        s := _scored(
            raw.get("category"),
            MAX_CATEGORY,
            context.get("categories"),
            strict=True,
            synonyms=CATEGORY_SYNONYMS,
            default_confidence=overall,
        )
    ) is not None:
        out["category"] = s
    # A description that only repeats the name or category ("bulb", "artists")
    # says nothing; drop it.
    echoes = {(_unwrap(raw.get(k))[0] or "").strip().casefold() for k in ("name", "category")}
    if (
        s := _scored(raw.get("description"), MAX_DESCRIPTION, default_confidence=overall)
    ) is not None and s["value"].casefold() not in echoes:
        out["description"] = s
    name = _name_agreeing_with(name, out.get("description"), overall)
    if name is not None:
        out = {"name": name, **out}
    tags_raw = raw.get("tags")
    if isinstance(tags_raw, list):
        seen: set[str] = set()
        tags = []
        for t in clean_tags(tags_raw):
            # New tags are allowed; existing ones keep the client's spelling.
            s = _scored(t, MAX_TAG, context.get("tags"))
            if s is None or s["value"].casefold() in seen:
                continue
            seen.add(s["value"].casefold())
            tags.append(s)
            if len(tags) >= MAX_TAGS:
                break
        if tags:
            out["tags"] = tags
    if not out:
        raise BackendError("model output had no usable fields")
    return out


# Second-chance prompt: small models describe far more reliably than they
# follow a JSON schema.
PLAIN_PROMPT = "Describe the main object in this photo in one short sentence."
TEXT_NAME_CONFIDENCE = 0.3
TEXT_DESCRIPTION_CONFIDENCE = 0.4
DERIVED_NAME_CONFIDENCE = 0.5
TRUSTED_NAME_CONFIDENCE = 0.9
MAX_NAME_WORDS = 4
MAX_PHRASE_WORDS = 6

_REFUSAL = re.compile(r"^(?:i'?m sorry|sorry|i cannot|i can'?t|i am unable|unable to)\b", re.I)
_LEADS = re.compile(
    r"^(?:"
    r"(?:in|from) (?:the|this) (?:image|photo|picture),?\s*"
    r"|(?:the|this) (?:image|photo|picture)\s+"
    r"(?:shows|features|depicts|contains|displays|captures|is of)\s+"
    r"|(?:there is|there are|this is|these are|it is|it's|i see|we can see|you can see)\s+"
    r"|(?:a |an )?(?:close-up|photo|picture|image)(?: of)?\s+"
    r"|(?:a|an|the|some|one|two|three|several)\s+"
    r"|(?:pair|set|couple|stack|pile|piece|bunch) of\s+"
    r")",
    re.I,
)
_NAME_END = re.compile(
    r"[,.;:!?()]|\s(?:with|on|in|inside|sitting|lying|placed|resting|standing|that|which"
    r"|next|against|under|over|near|beside|is|are|has|being)\b",
    re.I,
)


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def caption_name(text: str) -> str | None:
    """The object noun phrase at the start of a caption sentence, at most
    MAX_NAME_WORDS words ("A pair of black headphones with..." -> "black
    headphones"), or None."""
    sentence = re.split(r"(?<=[.!?])\s", " ".join(text.split()), maxsplit=1)[0]
    body = sentence
    while (stripped := _LEADS.sub("", body, count=1)) != body:
        body = stripped
    name = _NAME_END.split(body, maxsplit=1)[0].strip()
    words = name.split()
    if not words or not re.search(r"[^\W\d_]{2,}", name):
        return None
    if len(words) > MAX_PHRASE_WORDS:
        return None  # not a noun phrase ("utensils scattered loosely across...")
    if len(words) > MAX_NAME_WORDS:
        # Keep the head noun: the last words of the phrase.
        words = words[-MAX_NAME_WORDS:]
    name = " ".join(words)
    return None if _is_person_name(name) else name


def suggestion_from_text(text: str, context: dict[str, Any]) -> dict[str, Any]:
    """Derive a low-confidence Suggestion from a plain-text description."""
    text = " ".join(text.split())
    if not re.search(r"[^\W\d_]{2,}", text) or text.startswith(("{", "[")) or _REFUSAL.match(text):
        raise BackendError("model output had no usable text")
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    name = caption_name(sentence)
    out: dict[str, Any] = {
        "description": {"value": _cap(sentence), "confidence": TEXT_DESCRIPTION_CONFIDENCE}
    }
    if name:
        out["name"] = {"value": _cap(name), "confidence": TEXT_NAME_CONFIDENCE}
    low = sentence.casefold()

    def mentioned(v: str) -> bool:
        return bool(v.strip()) and re.search(rf"\b{re.escape(v.casefold())}\b", low) is not None

    if cats := [c for c in context.get("categories", []) if mentioned(c)]:
        out["category"] = {"value": cats[0], "confidence": TEXT_NAME_CONFIDENCE}
    if tags := [t for t in context.get("tags", []) if mentioned(t)]:
        out["tags"] = [{"value": t, "confidence": TEXT_NAME_CONFIDENCE} for t in tags]
    return clamp(out, context)


def parse_suggestion(content: str, context: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Return (suggestion, "json" | "text"); JSON first, then plain text."""
    try:
        return clamp(extract_json(content), context), "json"
    except BackendError as json_exc:
        try:
            return suggestion_from_text(content, context), "text"
        except BackendError:
            raise json_exc from None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc


def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _keep_alive(raw: str) -> str | int:
    # Ollama takes a duration string ("30m") or a number of seconds (-1 = forever).
    raw = raw.strip() or DEFAULT_KEEP_ALIVE
    try:
        return int(raw)
    except ValueError:
        return raw


def _format_mode(raw: str) -> str:
    """OLLAMA_JSON_FORMAT -> "schema" (default), "json" or "none"."""
    raw = raw.strip().lower()
    if raw in ("json",):
        return "json"
    if raw in ("0", "false", "no", "off", "none"):
        return "none"
    return "schema"


# How long the list of installed models (GET /api/tags) is reused.
INSTALLED_TTL_S = 30.0


class ModelStats:
    """Timings for one model: admission decisions and avg_latency_ms."""

    def __init__(self) -> None:
        # Moving average of recent generation times (model load excluded),
        # reported as avg_latency_ms and used for Retry-After.
        self.avg_s: float | None = None
        # The last few generation times. Admission decisions use the slowest
        # of them, not the average: one fast prompt-cache hit must not let in
        # a request that then times out.
        self.recent: collections.deque[float] = collections.deque(maxlen=RECENT_WINDOW)
        # Last observed load time, for deciding whether a request that finds
        # the model unloaded can wait for the load.
        self.load_s: float | None = None

    def record(self, seconds: float) -> None:
        seconds = max(0.0, seconds)
        self.avg_s = seconds if self.avg_s is None else 0.7 * self.avg_s + 0.3 * seconds
        self.recent.append(seconds)

    def slow_generate_s(self) -> float:
        """Slowest recent generation time (15 s until measured)."""
        return max(self.recent) if self.recent else DEFAULT_GENERATE_S

    def load_estimate_s(self) -> float:
        """Expected load time: the last measured load, but never less than
        DEFAULT_LOAD_S. Timed loads can come out short (a load already under
        way, files in the page cache) while the next one is slow."""
        return max(DEFAULT_LOAD_S, self.load_s or 0.0)

    def record_load(self, seconds: float) -> None:
        if seconds >= MIN_LOAD_S:
            self.load_s = seconds


class OllamaBackend(Backend):
    id = "ollama"

    def __init__(self, url: str | None = None, model: str | None = None):
        self.url = (url or os.environ.get("OLLAMA_URL") or DEFAULT_URL).rstrip("/")
        self.model = model or os.environ.get("OLLAMA_MODEL", "").strip() or DEFAULT_MODEL
        self.queue_size = max(0, _env_int("OLLAMA_QUEUE_SIZE", 1))
        self.retry_after = max(1, _env_int("OLLAMA_RETRY_AFTER_S", 5))
        self.warmup_enabled = os.environ.get("OLLAMA_WARMUP", "1").strip() not in ("0", "false")
        self.num_predict = _env_int("OLLAMA_NUM_PREDICT", DEFAULT_NUM_PREDICT)
        self.temperature = _env_float("OLLAMA_TEMPERATURE", 0.0)
        self.keep_alive = _keep_alive(os.environ.get("OLLAMA_KEEP_ALIVE", ""))
        self.format_mode = _format_mode(os.environ.get("OLLAMA_JSON_FORMAT", ""))
        self.log_raw = _env_flag("OLLAMA_LOG_RAW", False)
        self.plain_retry = _env_flag("OLLAMA_PLAIN_RETRY", True)
        # Models a client may pick; the default (OLLAMA_MODEL) comes first.
        self.allowed_configured = bool(get_settings().allowed_models)
        self.models = allowlist(self.model, get_settings().allowed_models)
        if self.log_raw:
            _enable_debug_logging()
        self.ready = not self.warmup_enabled
        self.warmup_error: str | None = None
        self._warm_thread: threading.Thread | None = None
        self._reload_thread: threading.Thread | None = None
        # Background load under way: (model, monotonic start), else None.
        self._loading: tuple[str, float] | None = None
        # Model to load once the running background load finishes (one slot,
        # the latest request wins), and whether the load thread is still
        # taking work. Both change under _start_lock only.
        self._pending: str | None = None
        self._reload_active = False
        self._start_lock = threading.Lock()
        self._in_flight = 0
        self._waiting = 0
        # Model of the request in flight (or the last one), for the queue check.
        self._current = self.model
        self._stats_by_model: dict[str, ModelStats] = {}
        self._installed: set[str] | None = None
        self._installed_at = -math.inf

    # -- per-model timings ---------------------------------------------------
    def stats(self, model: str | None = None) -> ModelStats:
        return self._stats_by_model.setdefault(model_key(model or self.model), ModelStats())

    # The default model's timings, under the names the admission code and
    # tests have always used.
    @property
    def _avg_s(self) -> float | None:
        return self.stats().avg_s

    @_avg_s.setter
    def _avg_s(self, value: float | None) -> None:
        self.stats().avg_s = value

    @property
    def _recent(self) -> collections.deque[float]:
        return self.stats().recent

    @property
    def _load_s(self) -> float | None:
        return self.stats().load_s

    def _record(self, seconds: float, model: str | None = None) -> None:
        self.stats(model).record(seconds)

    def _record_load(self, seconds: float, model: str | None = None) -> None:
        self.stats(model).record_load(seconds)

    def _slow_generate_s(self, model: str | None = None) -> float:
        return self.stats(model).slow_generate_s()

    def _load_estimate_s(self, model: str | None = None) -> float:
        return self.stats(model).load_estimate_s()

    def _cold_wait_fits(self, model: str | None = None) -> bool:
        """Whether loading the model and then answering fits in 90% of
        BACKEND_TIMEOUT_S, judging by its load estimate (at least 15 s) and
        its slowest recent generation (15 s until measured)."""
        s = self.stats(model)
        return cold_wait_fits(
            s.load_estimate_s(), s.slow_generate_s(), get_settings().backend_timeout_s
        )

    # -- warm-up (thread, so it is independent of any event loop) ----------
    def start(self) -> None:
        with self._start_lock:
            if self.ready or self._warm_thread is not None:
                return
            self._warm_thread = threading.Thread(target=self._warm, daemon=True)
            self._warm_thread.start()

    def _load_payload(self, model: str) -> dict[str, Any]:
        return {"model": model, "prompt": "", "stream": False, "keep_alive": self.keep_alive}

    def _warm(self) -> None:
        delay = 1.0
        payload = self._load_payload(self.model)
        while True:
            try:
                t0 = time.monotonic()
                r = httpx.post(f"{self.url}/api/generate", json=payload, timeout=600)
                if r.status_code < 400:
                    self._record_load(time.monotonic() - t0)
                    self.ready, self.warmup_error = True, None
                    return
                self.warmup_error = f"HTTP {r.status_code}"
            except httpx.HTTPError as exc:
                self.warmup_error = str(exc) or type(exc).__name__
            time.sleep(delay)
            delay = min(delay * 2, 30.0)

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.url, timeout=get_settings().backend_timeout_s)

    async def info(self) -> dict[str, Any]:
        self.start()
        ready = self.ready
        loaded = await self._loaded_models() if ready or self.allowed_configured else None
        if ready and loaded is not None and not any(model_key(m) in loaded for m in self.models):
            # Ollama unloaded the model (keep_alive expired, restart, another
            # model took the memory). Report not ready and reload the default,
            # so the next suggestion does not pay the load time. A model the
            # admin allowed and a client picked counts as loaded: reloading
            # the default would only evict it again.
            ready = False
            if not self._switch_or_load_in_flight():
                self._reload(self.model)
        out: dict[str, Any] = {
            "name": f"ollama-{self.model}",
            "capabilities": list(CAPABILITIES),
            "ready": ready,
        }
        if self._avg_s is not None:
            # Typical generation time of the default model (load excluded),
            # for clients that show an estimated progress bar. Absent until a
            # request finished.
            out["avg_latency_ms"] = round(self._avg_s * 1000)
        if self.allowed_configured:
            out["models"] = await self._model_entries(loaded)
        return out

    async def _model_entries(self, loaded: set[str] | None) -> list[dict[str, Any]]:
        installed = await self._installed_models()
        entries = []
        for m in self.models:
            is_default = m == self.model
            if not is_default and (installed is None or model_key(m) not in installed):
                continue
            entry: dict[str, Any] = {"id": m, "default": is_default}
            if loaded is not None:
                entry["loaded"] = model_key(m) in loaded
            if (avg := self.stats(m).avg_s) is not None:
                entry["avg_latency_ms"] = round(avg * 1000)
            entries.append(entry)
        return entries

    async def _installed_models(self) -> set[str] | None:
        """model_key()s Ollama has installed (GET /api/tags), cached for
        INSTALLED_TTL_S. On an error the last good answer is reused; None
        until one arrived, which offers the default model only."""
        if time.monotonic() - self._installed_at < INSTALLED_TTL_S:
            return self._installed
        try:
            async with httpx.AsyncClient(base_url=self.url, timeout=2.0) as client:
                r = await client.get("/api/tags")
            models = r.json().get("models") if r.status_code < 400 else None
        except (httpx.HTTPError, ValueError, AttributeError):
            models = None
        if isinstance(models, list):
            self._installed = {
                model_key(str(m.get("name") or m.get("model") or ""))
                for m in models
                if isinstance(m, dict)
            }
            self._installed_at = time.monotonic()
        return self._installed

    async def _loaded_models(self) -> set[str] | None:
        """model_key()s Ollama has in memory (GET /api/ps), or None when that
        is unknown (error, old Ollama)."""
        try:
            async with httpx.AsyncClient(base_url=self.url, timeout=2.0) as client:
                r = await client.get("/api/ps")
            models = r.json().get("models") if r.status_code < 400 else None
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        if not isinstance(models, list):
            return None
        return {
            model_key(str(m.get("name") or m.get("model") or ""))
            for m in models
            if isinstance(m, dict)
        }

    async def _model_loaded(self, model: str | None = None) -> bool:
        """Whether Ollama has the model in memory (GET /api/ps). Any error
        counts as loaded: the check must never make a working service look
        broken, and an unreachable Ollama surfaces on /v1/suggest."""
        loaded = await self._loaded_models()
        return loaded is None or model_key(model or self.model) in loaded

    def _switch_or_load_in_flight(self) -> bool:
        """Whether a model is on its way into Ollama's memory: a background
        load is running, or a request is in flight (its chat call makes Ollama
        load its model, which /api/ps may not list until the load is done).
        /v1/info must not queue a reload of the default then: with one model
        slot it would evict the model being loaded."""
        reloading = self._reload_active or (
            self._reload_thread is not None and self._reload_thread.is_alive()
        )
        return reloading or self._loading is not None or self._in_flight > 0

    def _reload(self, model: str | None = None) -> None:
        """Load the model in the background. While another background load
        runs, the model is queued and loaded right after it, so a client told
        "model X is loading" finds X loading or loaded when it retries. Loads
        run one at a time, so Ollama still holds one model."""
        model = model or self.model
        with self._start_lock:
            if self._reload_active:
                loading = self._loading
                if loading is not None and same_model(loading[0], model):
                    return
                self._pending = model
                return
            self._reload_active = True
            self._pending = None
            self._loading = (model, time.monotonic())
            self._reload_thread = threading.Thread(
                target=self._load_once, args=(model,), daemon=True
            )
            self._reload_thread.start()

    def _load_once(self, model: str | None = None) -> None:
        next_model: str | None = model or self.model
        while next_model is not None:
            model = next_model
            try:
                t0 = time.monotonic()
                r = httpx.post(
                    f"{self.url}/api/generate", json=self._load_payload(model), timeout=600
                )
                if r.status_code < 400:
                    self._record_load(time.monotonic() - t0, model)
            except httpx.HTTPError as exc:
                log.warning("ollama model reload failed (model=%s): %s", model, exc)
            finally:
                with self._start_lock:
                    next_model, self._pending = self._pending, None
                    if next_model is not None and same_model(next_model, model):
                        next_model = None
                    if next_model is None:
                        self._loading, self._reload_active = None, False
                    else:
                        self._loading = (next_model, time.monotonic())

    def _cold_retry_after(self, model: str) -> int:
        """Retry-After for a request that found its model unloaded: that
        model's load estimate (the last measured load, at least 15 s), minus
        how long its background load has been running, at least 1 s. A model
        queued behind another background load also waits for the rest of
        that load."""
        remaining = self._load_estimate_s(model)
        loading, pending = self._loading, self._pending
        if loading is not None and same_model(loading[0], model):
            remaining -= time.monotonic() - loading[1]
        elif loading is not None and pending is not None and same_model(pending, model):
            elapsed = time.monotonic() - loading[1]
            remaining += max(0.0, self._load_estimate_s(loading[0]) - elapsed)
        return max(1, math.ceil(remaining))

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._client() as client:
                r = await client.post(path, json=payload)
        except httpx.TimeoutException:
            raise TimeoutError from None
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"ollama unreachable at {self.url}: {exc}") from None
        if r.status_code >= 400:
            log.warning(
                "ollama %s returned HTTP %d: %r",
                path,
                r.status_code,
                truncate_for_log(r.text),
            )
            raise BackendError(f"ollama returned HTTP {r.status_code}")
        try:
            data = r.json()
        except ValueError:
            log.warning("ollama %s returned a non-JSON body: %r", path, truncate_for_log(r.text))
            raise BackendError("ollama returned a non-JSON body") from None
        if not isinstance(data, dict):
            raise BackendError("ollama returned an unexpected body")
        return data

    def options(self) -> dict[str, Any]:
        opts: dict[str, Any] = {"temperature": self.temperature}
        if self.num_predict > 0:
            opts["num_predict"] = self.num_predict
        return opts

    async def _chat(
        self,
        prompt: str,
        image_b64: str,
        fmt: str | dict[str, Any] | None,
        model: str | None = None,
    ) -> tuple[str, float]:
        """Return (content, seconds Ollama spent loading the model)."""
        model = model or self.model
        payload: dict[str, Any] = {
            "model": model,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": self.options(),
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
        }
        if fmt is not None:
            payload["format"] = fmt
        data = await self._post("/api/chat", payload)
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str):
            log.warning("ollama response had no message content (keys: %s)", sorted(data))
            raise BackendError("ollama response had no message content")
        if self.log_raw:
            log.debug(
                "ollama raw output (model=%s, %d chars): %r",
                model,
                len(content),
                truncate_for_log(content),
            )
        load = data.get("load_duration")
        load_s = load / 1e9 if isinstance(load, (int, float)) and load > 0 else 0.0
        self._record_load(load_s, model)
        return content, load_s

    def _parse(
        self, content: str, context: dict[str, Any], attempt: str, model: str | None = None
    ) -> dict[str, Any]:
        model = model or self.model
        try:
            out, how = parse_suggestion(content, context)
        except BackendError as exc:
            log.warning(
                "ollama output unusable (%s prompt): %s (model=%s, %d chars): %r",
                attempt,
                exc,
                model,
                len(content),
                truncate_for_log(content),
            )
            raise
        if how == "text" and attempt == "json":
            log.warning(
                "ollama ignored the JSON instructions; derived fields from plain text "
                "(model=%s, %d chars): %r",
                model,
                len(content),
                truncate_for_log(content),
            )
        return out

    async def generate(
        self, image_bytes: bytes, context: dict[str, Any], model: str | None = None
    ) -> dict[str, Any]:
        model = model or self.model
        started = time.monotonic()
        image_b64 = base64.b64encode(image_bytes).decode()
        fmt: str | dict[str, Any] | None = None
        if self.format_mode == "schema":
            fmt = build_schema(context)
        elif self.format_mode == "json":
            fmt = "json"
        content, load_s = await self._chat(build_prompt(context), image_b64, fmt, model)
        self._record(time.monotonic() - started - load_s, model)
        try:
            return self._parse(content, context, "json", model)
        except BackendError:
            if not self.plain_retry or not self._retry_fits(time.monotonic() - started, load_s):
                raise
        content, _ = await self._chat(PLAIN_PROMPT, image_b64, None, model)
        return self._parse(content, context, "plain", model)

    @staticmethod
    def _retry_fits(elapsed: float, load_s: float) -> bool:
        """Retry when generating took at most 40% of the timeout. Model load
        time is left out: it is paid once, and the retry runs on the loaded
        model. Some time must still remain for the retry itself."""
        timeout = get_settings().backend_timeout_s
        generating = max(0.0, elapsed - load_s)
        remaining = timeout - elapsed
        return generating <= timeout * 0.4 and remaining >= max(generating, timeout * 0.2)

    async def resolve(self, requested: Any) -> str:
        """The model a request runs on: context.model when allowed and
        installed, else the default."""
        if not self.allowed_configured or not isinstance(requested, str):
            return self.model
        if not requested.strip() or same_model(requested, self.model):
            return self.model
        return resolve_model(requested, self.model, self.models, await self._installed_models())

    async def suggest(self, image_bytes: bytes, mime: str, context: dict[str, Any]) -> Any:
        self.start()
        if not self.ready:
            raise Busy(f"model '{self.model}' is warming up", self.retry_after)
        model = await self.resolve(context.get("model"))
        timeout = get_settings().backend_timeout_s
        if not self._in_flight:
            if not self._cold_wait_fits(model) and not await self._model_loaded(model):
                # Ollama does not have the model in memory (restart, eviction,
                # keep_alive, or the client picked another model than the
                # loaded one) and loading plus answering would not fit
                # BACKEND_TIMEOUT_S (Qwen on the Jetson: 15 s + 13.5 s > 0.9 x
                # 25 s), so answer 503 at once and load it now. When it fits
                # (45 s), the request simply waits: the chat call makes Ollama
                # load the model.
                self._reload(model)
                raise Busy(f"model '{model}' is loading", self._cold_retry_after(model))
        else:
            # One request in flight, up to queue_size waiting; the rest get
            # 503. A request is also refused when, judging by the slowest
            # recent generation times, it would time out while waiting its
            # turn. A request for another model than the one in flight also
            # pays that model's load (Ollama holds one model at a time).
            # Plain counters + polling keep this independent of the event loop.
            if self._waiting >= self.queue_size:
                raise Busy("backend busy, queue full", self._busy_retry_after())
            ahead = (self._waiting + 1) * self._slow_generate_s(self._current)
            need = ahead + self._slow_generate_s(model)
            switch = not same_model(model, self._current)
            if switch:
                need += self._load_estimate_s(model)
            if need > timeout * 0.9:
                reason = "a model switch" if switch else "a queued request"
                raise Busy(f"backend busy, {reason} would time out", self._busy_retry_after())
        self._waiting += 1
        try:
            while self._in_flight:
                await asyncio.sleep(0.02)
        finally:
            self._waiting -= 1
        self._in_flight += 1
        self._current = model
        try:
            out = await self.generate(image_bytes, context, model)
        finally:
            self._in_flight -= 1
        return {**out, "model_used": model}

    def _busy_retry_after(self) -> int:
        avg = self.stats(self._current).avg_s
        if avg is None:
            return self.retry_after
        return max(self.retry_after, math.ceil(avg * (self._waiting + 1)))

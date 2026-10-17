"""Ollama backend: asks a local vision model (Moondream by default) for a
Suggestion as strict JSON, then validates and clamps the answer.

Env (read when the backend is created):
  OLLAMA_URL    default http://ollama:11434
  OLLAMA_MODEL  default moondream (qwen2-vl-2b style tags also work)
  OLLAMA_WARMUP          1 (default) loads the model in the background at startup;
                         /v1/info reports ready:false and /v1/suggest answers 503
                         with Retry-After until it has loaded. 0 skips warm-up.
  OLLAMA_QUEUE_SIZE      requests allowed to wait behind the one in flight (default 1)
  OLLAMA_RETRY_AFTER_S   Retry-After seconds on 503 busy/warming (default 5)
  OLLAMA_NUM_PREDICT     max tokens the model may generate per request (default 256;
                         0 or less removes the cap). Stops runaway output, e.g. a
                         small model padding format=json output with whitespace.
  OLLAMA_TEMPERATURE     sampling temperature (default 0, deterministic)
  OLLAMA_KEEP_ALIVE      how long Ollama keeps the model loaded after a request
                         (default 30m; -1 keeps it loaded forever)
  OLLAMA_JSON_FORMAT     1 (default) sends format=json; 0 lets the model answer freely
  OLLAMA_PLAIN_RETRY     1 (default): if the JSON answer is unusable and less than 40%
                         of BACKEND_TIMEOUT_S has passed, ask once more with a short
                         plain-text prompt and derive name/description from the text
  OLLAMA_LOG_RAW         1 logs every raw model answer at DEBUG (default 0). Unusable
                         answers are always logged at WARNING. Logged text is cut to
                         2 KB; the image and the request token are never logged.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import threading
import time
from typing import Any

import httpx

from ..settings import get_settings
from .base import CAPABILITIES, Backend, BackendUnavailable

log = logging.getLogger(__name__)

DEFAULT_URL = "http://ollama:11434"
DEFAULT_MODEL = "moondream"
DEFAULT_NUM_PREDICT = 256
DEFAULT_KEEP_ALIVE = "30m"

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
    cats = [c for c in context.get("categories", []) if c.strip()][:MAX_CONTEXT_ITEMS]
    tags = [t for t in context.get("tags", []) if t.strip()][:MAX_CONTEXT_ITEMS]
    locale = context.get("locale") or "en"
    lines = [
        "You catalogue household inventory items from a single photo.",
        "Reply with ONLY one JSON object, no prose, no markdown, of this exact shape:",
        '{"name":{"value":str,"confidence":0..1},"category":{"value":str,"confidence":0..1},'
        '"tags":[{"value":str,"confidence":0..1}],"description":{"value":str,"confidence":0..1}}',
        "Omit any field you are unsure about. name: short product name (max 6 words).",
        f"description: one short sentence. Use at most {MAX_TAGS} tags, lowercase, 1-2 words.",
        f"Write all values in the language for locale '{locale}'.",
    ]
    if cats:
        lines.append(
            "Existing categories (use one of these with EXACT spelling if it fits): "
            + json.dumps(cats, ensure_ascii=False)
        )
    if tags:
        lines.append(
            "Existing tags (prefer these with EXACT spelling over new ones): "
            + json.dumps(tags, ensure_ascii=False)
        )
    return "\n".join(lines)


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


def clamp(raw: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    raw = normalize(raw)
    # Moondream often answers flat with a single top-level confidence; it
    # stands in for fields that carry none of their own.
    overall = _confidence(raw.get("confidence"))
    out: dict[str, Any] = {}
    if (s := _scored(raw.get("name"), MAX_NAME, default_confidence=overall)) is not None:
        out["name"] = s
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
    if (
        s := _scored(raw.get("description"), MAX_DESCRIPTION, default_confidence=overall)
    ) is not None:
        out["description"] = s
    tags_raw = raw.get("tags")
    if isinstance(tags_raw, list):
        seen: set[str] = set()
        tags = []
        for t in tags_raw:
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

_REFUSAL = re.compile(r"^(?:i'?m sorry|sorry|i cannot|i can'?t|i am unable|unable to)\b", re.I)
_LEADS = re.compile(
    r"^(?:"
    r"(?:in|from) (?:the|this) (?:image|photo|picture),?\s*"
    r"|(?:the|this) (?:image|photo|picture)\s+"
    r"(?:shows|features|depicts|contains|displays|captures|is of)\s+"
    r"|(?:there is|there are|this is|these are|it is|it's|i see|we can see|you can see)\s+"
    r"|(?:a |an )?(?:close-up|photo|picture|image)(?: of)?\s+"
    r"|(?:a|an|the|some|one)\s+"
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


def suggestion_from_text(text: str, context: dict[str, Any]) -> dict[str, Any]:
    """Derive a low-confidence Suggestion from a plain-text description."""
    text = " ".join(text.split())
    if not re.search(r"[^\W\d_]{2,}", text) or text.startswith(("{", "[")) or _REFUSAL.match(text):
        raise BackendError("model output had no usable text")
    sentence = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    body = sentence
    while (stripped := _LEADS.sub("", body, count=1)) != body:
        body = stripped
    name = _NAME_END.split(body, maxsplit=1)[0].strip()
    out: dict[str, Any] = {
        "description": {"value": _cap(sentence), "confidence": TEXT_DESCRIPTION_CONFIDENCE}
    }
    if 1 <= len(name.split()) <= 6:
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


class Busy(BackendUnavailable):
    def __init__(self, message: str, retry_after: int):
        super().__init__(message)
        self.headers = {"Retry-After": str(retry_after)}


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
        self.json_format = _env_flag("OLLAMA_JSON_FORMAT", True)
        self.log_raw = _env_flag("OLLAMA_LOG_RAW", False)
        self.plain_retry = _env_flag("OLLAMA_PLAIN_RETRY", True)
        if self.log_raw:
            _enable_debug_logging()
        self.ready = not self.warmup_enabled
        self.warmup_error: str | None = None
        self._warm_thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._in_flight = 0
        self._waiting = 0

    # -- warm-up (thread, so it is independent of any event loop) ----------
    def start(self) -> None:
        with self._start_lock:
            if self.ready or self._warm_thread is not None:
                return
            self._warm_thread = threading.Thread(target=self._warm, daemon=True)
            self._warm_thread.start()

    def _warm(self) -> None:
        delay = 1.0
        payload = {
            "model": self.model,
            "prompt": "",
            "stream": False,
            "keep_alive": self.keep_alive,
        }
        while True:
            try:
                r = httpx.post(f"{self.url}/api/generate", json=payload, timeout=600)
                if r.status_code < 400:
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
        return {
            "name": f"ollama-{self.model}",
            "capabilities": list(CAPABILITIES),
            "ready": self.ready,
        }

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

    async def _chat(self, prompt: str, image_b64: str, json_format: bool) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "keep_alive": self.keep_alive,
            "options": self.options(),
            "messages": [{"role": "user", "content": prompt, "images": [image_b64]}],
        }
        if json_format:
            payload["format"] = "json"
        data = await self._post("/api/chat", payload)
        content = (data.get("message") or {}).get("content")
        if not isinstance(content, str):
            log.warning("ollama response had no message content (keys: %s)", sorted(data))
            raise BackendError("ollama response had no message content")
        if self.log_raw:
            log.debug(
                "ollama raw output (model=%s, %d chars): %r",
                self.model,
                len(content),
                truncate_for_log(content),
            )
        return content

    def _parse(self, content: str, context: dict[str, Any], attempt: str) -> dict[str, Any]:
        try:
            out, how = parse_suggestion(content, context)
        except BackendError as exc:
            log.warning(
                "ollama output unusable (%s prompt): %s (model=%s, %d chars): %r",
                attempt,
                exc,
                self.model,
                len(content),
                truncate_for_log(content),
            )
            raise
        if how == "text" and attempt == "json":
            log.warning(
                "ollama ignored the JSON instructions; derived fields from plain text "
                "(model=%s, %d chars): %r",
                self.model,
                len(content),
                truncate_for_log(content),
            )
        return out

    async def generate(self, image_bytes: bytes, context: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        image_b64 = base64.b64encode(image_bytes).decode()
        content = await self._chat(build_prompt(context), image_b64, self.json_format)
        try:
            return self._parse(content, context, "json")
        except BackendError:
            budget = get_settings().backend_timeout_s * 0.4
            if not self.plain_retry or time.monotonic() - started > budget:
                raise
        content = await self._chat(PLAIN_PROMPT, image_b64, json_format=False)
        return self._parse(content, context, "plain")

    async def suggest(self, image_bytes: bytes, mime: str, context: dict[str, Any]) -> Any:
        self.start()
        if not self.ready:
            raise Busy(f"model '{self.model}' is warming up", self.retry_after)
        # One request in flight, up to queue_size waiting; the rest get 503.
        # Plain counters + polling keep this independent of the event loop.
        if self._in_flight and self._waiting >= self.queue_size:
            raise Busy("backend busy, queue full", self.retry_after)
        self._waiting += 1
        try:
            while self._in_flight:
                await asyncio.sleep(0.02)
        finally:
            self._waiting -= 1
        self._in_flight += 1
        try:
            return await self.generate(image_bytes, context)
        finally:
            self._in_flight -= 1

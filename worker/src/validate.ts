import type { ClientContext } from "./context";

/** Mirrors app/identity.py and the clamp/extract path in app/backends/ollama.py. */

export const NOT_JSON = "model output was not a JSON object";
export const NO_FIELDS = "model output had no usable fields";

const MAX_TAGS = 10;
const MAX_NAME = 120;
const MAX_CATEGORY = 80;
const MAX_TAG = 40;
const MAX_DESCRIPTION = 500;
const MAX_IDENTITY = 128;

const IDENTITY_ALIASES: Record<string, string[]> = {
  brand: ["brand", "brandname", "manufacturer", "make"],
  model_number: ["modelnumber", "modelno", "modelnum", "productmodel", "model"],
  serial_number: ["serialnumber", "serialno", "serialnum", "serial", "sn"],
};

const UNREADABLE = new Set([
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
]);

const PLACEHOLDERS = new Set(["str", "string", "...", "…", "0..1", "n/a", "na", "none", "null", "unknown"]);

const ALIASES: Record<string, string[]> = {
  name: ["item", "object", "product", "title", "label", "item_name", "product_name"],
  category: ["type", "kind", "class", "item_category"],
  description: ["caption", "summary", "details", "desc"],
  tags: ["keywords", "labels", "attributes"],
};

const PROMPT_WORDS = new Set([
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
]);

const IRREGULAR: Record<string, string> = {
  clothes: "clothing",
  knives: "knife",
  shelves: "shelf",
  people: "person",
};

const SYNONYMS_BY_TARGET: Record<string, string> = {
  kitchen: "cooking utensil|kitchen utensil|utensil|cookware|kitchenware|knife|cutlery|pan|pot",
  electronic: "headphone|earphone|headset|laptop|computer|phone|smartphone|mobile phone|cell phone|tablet|camera|gadget|electronic device",
  lighting: "lamp|light|bulb|light bulb|lantern",
  shoe: "sneaker|boot|footwear|sandal|trainer",
  clothing: "apparel|garment|shirt|t shirt|jacket|sweater",
  tool: "hardware|power tool|drill|hammer|screwdriver|wrench",
  book: "novel|paperback|hardcover",
};

const CATEGORY_SYNONYMS: Record<string, string> = {};
for (const [target, words] of Object.entries(SYNONYMS_BY_TARGET)) {
  for (const word of words.split("|")) CATEGORY_SYNONYMS[word] = target;
}

const FIRST_NAMES = new Set(
  `john james michael david robert william richard thomas peter paul george daniel matthew
  andrew christopher joseph kevin brian steven eric tom mike dave steve chris nick alex
  sam ben tim dan jim joe tony anthony charles edward henry jacob ethan noah liam lucas
  oliver leon mary maria anna sarah emma laura julia lisa linda susan jennifer elizabeth
  emily olivia sophia sophie hannah jessica michelle anne jane alice helen kate karen
  nancy chloe mia ella amelia charlotte hans klaus stefan markus andreas nikos giorgos
  eleni dimitris yannis kostas`.split(/\s+/),
);

export class SuggestionError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "SuggestionError";
  }
}

function foldKey(key: string): string {
  return key.trim().toLowerCase().replace(/[\W_]+/gu, "");
}

function singular(word: string): string {
  if (IRREGULAR[word]) return IRREGULAR[word];
  if (word.length > 4 && word.endsWith("ies")) return `${word.slice(0, -3)}y`;
  if (/(?:ches|shes|sses|xes|zes)$/.test(word)) return word.slice(0, -2);
  if (word.length > 3 && word.endsWith("s") && !word.endsWith("ss") && !word.endsWith("us") && !word.endsWith("is")) {
    return word.slice(0, -1);
  }
  return word;
}

function matchKey(value: string): string {
  const words = value.toLowerCase().replace(/[\W_]+/gu, " ").split(/\s+/).filter(Boolean);
  if (words.length === 0) return "";
  words[words.length - 1] = singular(words[words.length - 1]);
  return words.join(" ");
}

function isCombining(ch: string): boolean {
  const cp = ch.codePointAt(0) ?? 0;
  return (
    (cp >= 0x0300 && cp <= 0x036f) ||
    (cp >= 0x1ab0 && cp <= 0x1aff) ||
    (cp >= 0x1dc0 && cp <= 0x1dff) ||
    (cp >= 0x20d0 && cp <= 0x20ff) ||
    (cp >= 0xfe20 && cp <= 0xfe2f)
  );
}

function cut(text: string, limit: number): string {
  const chars = Array.from(text);
  if (chars.length <= limit) return text;
  let end = limit;
  while (
    end > 0 &&
    (isCombining(chars[end] ?? "") ||
      chars[end] === "\u200d" ||
      chars[end - 1] === "\u200d" ||
      ((chars[end]?.codePointAt(0) ?? 0) >= 0xfe00 && (chars[end]?.codePointAt(0) ?? 0) <= 0xfe0f))
  ) {
    end -= 1;
  }
  return chars.slice(0, end).join("").replace(/\s+$/u, "");
}

function printable(text: string): string {
  let out = "";
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0;
    if (cp <= 0x1f || (cp >= 0x7f && cp <= 0x9f)) continue;
    if (cp >= 0xd800 && cp <= 0xdfff) continue;
    if ((cp >= 0x202a && cp <= 0x202e) || (cp >= 0x2066 && cp <= 0x2069)) continue;
    out += ch;
  }
  return out;
}

function asText(value: unknown, depth = 0): string | null {
  if (typeof value === "string") return value;
  if (depth > 3) return null;
  if (value && typeof value === "object" && !Array.isArray(value) && "value" in value) {
    return asText((value as { value: unknown }).value, depth + 1);
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const text = asText(item, depth + 1);
      if (text && text.trim()) return text;
    }
  }
  return null;
}

export function cleanIdentity(value: unknown): string {
  const text = asText(value);
  if (text === null) return "";
  const cleaned = printable(text).trim();
  if (!cleaned || UNREADABLE.has(cleaned.toLowerCase())) return "";
  return cut(cleaned, MAX_IDENTITY);
}

function sourcesOf(raw: Record<string, unknown>): Record<string, unknown>[] {
  const sources = [raw];
  const keys = Object.keys(raw);
  if (keys.length === 1) {
    const inner = raw[keys[0]];
    if (inner && typeof inner === "object" && !Array.isArray(inner)) {
      sources.push(inner as Record<string, unknown>);
    }
  }
  return sources;
}

function lookup(source: Record<string, unknown>, aliases: string[]): unknown {
  const folded = new Map<string, unknown>();
  for (const [key, value] of Object.entries(source)) {
    const norm = foldKey(key);
    if (!folded.has(norm)) folded.set(norm, value);
  }
  for (const alias of aliases) {
    if (folded.has(alias)) return folded.get(alias);
  }
  return undefined;
}

export function identityFields(raw: unknown): { brand: string; model_number: string; serial_number: string } {
  const sources = raw && typeof raw === "object" && !Array.isArray(raw) ? sourcesOf(raw as Record<string, unknown>) : [];
  const out = { brand: "", model_number: "", serial_number: "" };
  for (const key of ["brand", "model_number", "serial_number"] as const) {
    let found: unknown;
    let hit = false;
    for (const source of sources) {
      const value = lookup(source, IDENTITY_ALIASES[key]);
      if (value !== undefined) {
        found = value;
        hit = true;
        break;
      }
    }
    out[key] = hit ? cleanIdentity(found) : "";
  }
  return out;
}

function clip(value: string, limit: number): string {
  const collapsed = value.split(/\s+/u).filter(Boolean).join(" ");
  if (Array.from(collapsed).length <= limit) return collapsed;
  return `${cut(collapsed, limit - 1)}…`;
}

function confidenceOf(value: unknown): number | null {
  let number = value;
  if (typeof number === "string") {
    const trimmed = number.trim();
    const percent = trimmed.endsWith("%");
    const parsed = Number(trimmed.replace(/%$/u, ""));
    if (!Number.isFinite(parsed)) return null;
    number = percent ? parsed / 100 : parsed;
  }
  if (typeof number === "number" && Number.isFinite(number)) {
    return Math.min(1, Math.max(0, number));
  }
  return null;
}

function unwrap(value: unknown, depth = 0): { text: string | null; confidence: unknown } {
  if (typeof value === "string") return { text: value, confidence: null };
  if (depth > 3) return { text: null, confidence: null };
  if (value && typeof value === "object" && !Array.isArray(value) && "value" in value) {
    const record = value as { value: unknown; confidence?: unknown };
    const inner = unwrap(record.value, depth + 1);
    return { text: inner.text, confidence: record.confidence ?? inner.confidence };
  }
  if (Array.isArray(value)) {
    for (const item of value) {
      const inner = unwrap(item, depth + 1);
      if (inner.text && inner.text.trim()) return inner;
    }
  }
  return { text: null, confidence: null };
}

function snap(value: string, choices: string[] | undefined, synonyms?: Record<string, string>): string | null {
  const list = (choices ?? []).filter((choice) => choice.trim());
  if (list.length === 0) return null;
  const exact = new Map(list.map((choice) => [choice.toLowerCase(), choice]));
  const direct = exact.get(value.toLowerCase());
  if (direct) return direct;
  const byKey = new Map<string, string>();
  for (const choice of list) {
    const key = matchKey(choice);
    if (!byKey.has(key)) byKey.set(key, choice);
  }
  const key = matchKey(value);
  const keyed = byKey.get(key);
  if (keyed) return keyed;
  if (synonyms && key) {
    for (const candidate of [key, key.split(" ").at(-1) ?? ""]) {
      const target = synonyms[candidate];
      if (target && byKey.has(target)) return byKey.get(target) ?? null;
    }
  }
  return null;
}

interface Scored {
  value: string;
  confidence?: number;
}

function scored(
  value: unknown,
  limit: number,
  choices?: string[],
  options?: { strict?: boolean; synonyms?: Record<string, string>; defaultConfidence?: number | null },
): Scored | null {
  const unwrapped = unwrap(value);
  if (unwrapped.text === null) return null;
  let text = clip(unwrapped.text, limit);
  if (!text || PLACEHOLDERS.has(text.toLowerCase())) return null;
  if (choices && choices.length > 0) {
    const hit = snap(text, choices, options?.synonyms);
    if (hit === null && options?.strict) return null;
    text = hit ?? text;
  }
  const out: Scored = { value: text };
  const conf = confidenceOf(unwrapped.confidence) ?? options?.defaultConfidence ?? null;
  if (conf !== null) out.confidence = conf;
  return out;
}

function normalize(raw: Record<string, unknown>): Record<string, unknown> {
  const lowered: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(raw)) lowered[key.trim().toLowerCase()] = value;
  const fields = Object.keys(ALIASES);
  if (!fields.some((key) => key in lowered) && Object.keys(lowered).length === 1) {
    const inner = Object.values(lowered)[0];
    if (inner && typeof inner === "object" && !Array.isArray(inner)) return normalize(inner as Record<string, unknown>);
  }
  const out: Record<string, unknown> = {};
  for (const [key, aliases] of Object.entries(ALIASES)) {
    for (const alias of [key, ...aliases]) {
      const value = lowered[alias];
      if (value !== undefined && value !== "" && !(Array.isArray(value) && value.length === 0)) {
        if (value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 0) continue;
        out[key] = value;
        break;
      }
    }
  }
  if ("confidence" in lowered) out.confidence = lowered.confidence;
  const tags = out.tags;
  if (typeof tags === "string") out.tags = tags.split(/[,;]/u).map((tag) => tag.trim()).filter(Boolean);
  else if (tags && typeof tags === "object" && !Array.isArray(tags)) out.tags = [tags];
  return out;
}

function cleanTags(tags: unknown[]): unknown[] {
  const out: unknown[] = [];
  const seen = new Set<string>();
  for (const tag of tags) {
    const record = tag && typeof tag === "object" && !Array.isArray(tag) ? (tag as Record<string, unknown>) : null;
    const value = record ? record.value : tag;
    if (typeof value !== "string") {
      out.push(tag);
      continue;
    }
    let values = [value];
    const split = value.indexOf(":");
    if (split !== -1 && PROMPT_WORDS.has(value.slice(0, split).trim().toLowerCase())) {
      values = value.slice(split + 1).split(",");
    }
    for (const item of values) {
      const text = item.trim();
      const key = text.toLowerCase();
      if (!text || PROMPT_WORDS.has(key) || seen.has(key)) continue;
      seen.add(key);
      out.push(record ? { ...record, value: text } : text);
    }
  }
  return out;
}

function isPersonName(value: string): boolean {
  return /^[A-Za-z]+$/u.test(value) && value[0] === value[0]?.toUpperCase() && value.slice(1) === value.slice(1).toLowerCase() && FIRST_NAMES.has(value.toLowerCase());
}

function wordsOf(text: string): Set<string> {
  return new Set(
    (text.toLowerCase().match(/[^\W\d_]+/gu) ?? []).map((word) => singular(word)),
  );
}

const LEADS =
  /^(?:(?:in|from) (?:the|this) (?:image|photo|picture),?\s*|(?:the|this) (?:image|photo|picture)\s+(?:shows|features|depicts|contains|displays|captures|is of)\s+|(?:there is|there are|this is|these are|it is|it's|i see|we can see|you can see)\s+|(?:a |an )?(?:close-up|photo|picture|image)(?: of)?\s+|(?:a|an|the|some|one|two|three|several)\s+|(?:pair|set|couple|stack|pile|piece|bunch) of\s+)/i;
const NAME_END =
  /[,.;:!?()]|\s(?:with|on|in|inside|sitting|lying|placed|resting|standing|that|which|next|against|under|over|near|beside|is|are|has|being)\b/i;

function captionName(text: string): string | null {
  const sentence = text.split(/\s+/u).join(" ").split(/(?<=[.!?])\s/u)[0] ?? "";
  let body = sentence;
  let stripped = body.replace(LEADS, "");
  while (stripped !== body) {
    body = stripped;
    stripped = body.replace(LEADS, "");
  }
  const name = (body.split(NAME_END)[0] ?? "").trim();
  let words = name.split(/\s+/u).filter(Boolean);
  if (words.length === 0 || !/[^\W\d_]{2,}/u.test(name)) return null;
  if (words.length > 6) return null;
  if (words.length > 4) words = words.slice(-4);
  const phrase = words.join(" ");
  return isPersonName(phrase) ? null : phrase;
}

function cap(value: string): string {
  return value ? value[0].toUpperCase() + value.slice(1) : value;
}

function nameAgreeing(name: Scored | null, description: Scored | null, overall: number | null): Scored | null {
  if (!description || description.value.split(/\s+/u).length < 4) return name;
  const derived = captionName(description.value);
  if (!derived) return name;
  if (name && [...wordsOf(name.value)].some((word) => wordsOf(derived).has(word))) return name;
  if (name && (name.confidence ?? 0) >= 0.9) return name;
  const out: Scored = { value: cap(derived) };
  const conf = name?.confidence ?? overall;
  out.confidence = conf === null || conf === undefined ? 0.5 : Math.min(conf, 0.5);
  return out;
}

export function clamp(raw: Record<string, unknown>, context: ClientContext | { categories?: string[]; tags?: string[] }): Record<string, unknown> {
  const idents = identityFields(raw);
  const normalized = normalize(raw);
  const overall = confidenceOf(normalized.confidence);
  let name = scored(normalized.name, MAX_NAME, undefined, { defaultConfidence: overall });
  if (name && isPersonName(name.value)) name = null;
  const out: Record<string, unknown> = {};
  const categories = Array.isArray(context.categories) ? context.categories.filter((item) => typeof item === "string") : undefined;
  const category = scored(normalized.category, MAX_CATEGORY, categories, {
    strict: true,
    synonyms: CATEGORY_SYNONYMS,
    defaultConfidence: overall,
  });
  if (category) out.category = category;
  const echoes = new Set(
    ["name", "category"].map((key) => (unwrap(normalized[key]).text ?? "").trim().toLowerCase()),
  );
  const description = scored(normalized.description, MAX_DESCRIPTION, undefined, { defaultConfidence: overall });
  if (description && !echoes.has(description.value.toLowerCase())) out.description = description;
  name = nameAgreeing(name, (out.description as Scored | undefined) ?? null, overall);
  if (name) {
    const rest = out;
    return finish({ name, ...rest }, normalized, context, idents);
  }
  return finish(out, normalized, context, idents);
}

function finish(
  out: Record<string, unknown>,
  normalized: Record<string, unknown>,
  context: { tags?: string[] },
  idents: { brand: string; model_number: string; serial_number: string },
): Record<string, unknown> {
  const tagsRaw = normalized.tags;
  if (Array.isArray(tagsRaw)) {
    const seen = new Set<string>();
    const tags: Scored[] = [];
    for (const tag of cleanTags(tagsRaw)) {
      const scoredTag = scored(tag, MAX_TAG, context.tags);
      if (!scoredTag || seen.has(scoredTag.value.toLowerCase())) continue;
      seen.add(scoredTag.value.toLowerCase());
      tags.push(scoredTag);
      if (tags.length >= MAX_TAGS) break;
    }
    if (tags.length > 0) out.tags = tags;
  }
  if (Object.keys(out).length === 0) throw new SuggestionError(NO_FIELDS);
  out.brand = idents.brand;
  out.model_number = idents.model_number;
  out.serial_number = idents.serial_number;
  return out;
}

function valueEnd(text: string, start: number): number | null {
  if (text[start] !== "{") return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{" || ch === "[") depth += 1;
    else if (ch === "}" || ch === "]") {
      depth -= 1;
      if (depth === 0) return i + 1;
    }
  }
  return null;
}

function repairTruncated(text: string, start: number): Record<string, unknown> | null {
  const stack: string[] = [];
  const cuts: Array<[number, string]> = [];
  let inString = false;
  let escaped = false;
  for (let i = start; i < text.length; i++) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === "\\") escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') inString = true;
    else if (ch === "{" || ch === "[") stack.push(ch === "{" ? "}" : "]");
    else if (ch === "}" || ch === "]") {
      if (stack.length === 0 || stack.pop() !== ch || stack.length === 0) return null;
      cuts.push([i + 1, [...stack].reverse().join("")]);
    } else if (ch === ",") cuts.push([i, [...stack].reverse().join("")]);
  }
  if (stack.length === 0) return null;
  const candidates = [`${text.slice(start).replace(/\s+$/u, "").replace(/,+$/u, "")}${[...stack].reverse().join("")}`];
  for (const [pos, closers] of [...cuts].reverse()) candidates.push(text.slice(start, pos) + closers);
  for (const candidate of candidates) {
    try {
      const obj = JSON.parse(candidate) as unknown;
      if (obj && typeof obj === "object" && !Array.isArray(obj) && Object.keys(obj).length > 0) {
        return obj as Record<string, unknown>;
      }
    } catch {
      continue;
    }
  }
  return null;
}

export function extractJson(text: string): Record<string, unknown> {
  let body = text.trim().replace(/^```(?:json)?\s*/iu, "");
  body = body.replace(/\s*```$/u, "");
  for (let index = body.indexOf("{"); index !== -1; index = body.indexOf("{", index + 1)) {
    const end = valueEnd(body, index);
    if (end !== null) {
      try {
        const obj = JSON.parse(body.slice(index, end)) as unknown;
        if (obj && typeof obj === "object" && !Array.isArray(obj)) return obj as Record<string, unknown>;
      } catch {
        // Fall through to truncation repair.
      }
    }
    const repaired = repairTruncated(body, index);
    if (repaired) return repaired;
  }
  throw new SuggestionError(NOT_JSON);
}

export interface Assessed {
  ok: true;
  suggestion: Record<string, unknown>;
}

export function assess(content: string, context: ClientContext): { ok: true; suggestion: Record<string, unknown> } | { ok: false; message: string } {
  try {
    const suggestion = clamp(extractJson(content), context);
    return { ok: true, suggestion };
  } catch (err) {
    if (err instanceof SuggestionError) return { ok: false, message: err.message };
    return { ok: false, message: NOT_JSON };
  }
}

function scoredPublic(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as { value?: unknown; confidence?: unknown };
  if (typeof record.value !== "string" || !record.value.trim()) return null;
  const out: Record<string, unknown> = { value: record.value.trim() };
  if (typeof record.confidence === "number" && Number.isFinite(record.confidence)) {
    out.confidence = Math.min(1, Math.max(0, record.confidence));
  }
  return out;
}

/** Same final shape as app.main.sanitize, plus model_used. */
export function publicSuggestion(raw: Record<string, unknown>, model: string): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of ["name", "category", "description"]) {
    const value = scoredPublic(raw[key]);
    if (value) out[key] = value;
  }
  if (Array.isArray(raw.tags)) {
    const tags = raw.tags.map((tag) => scoredPublic(tag)).filter((tag) => tag !== null);
    if (tags.length > 0) out.tags = tags;
  }
  if (model.trim()) out.model_used = model;
  out.backend_used = "cloudflare-workers";
  const idents = identityFields(raw);
  if (Object.keys(out).length > 0 || idents.brand || idents.model_number || idents.serial_number) {
    Object.assign(out, idents);
  }
  return out;
}

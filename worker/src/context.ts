import { HttpError } from "./http";

const MAX_CONTEXT_ITEMS = 50;

export interface ClientContext {
  categories: string[];
  tags: string[];
  locale: string;
  model?: string;
}

/** Lone UTF-16 surrogate. JSON.parse keeps these; they must not reach a response. */
export function hasLoneSurrogate(text: string): boolean {
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code < 0xd800 || code > 0xdfff) continue;
    const next = text.charCodeAt(i + 1);
    if (code <= 0xdbff && next >= 0xdc00 && next <= 0xdfff) {
      i++;
      continue;
    }
    return true;
  }
  return false;
}

/** Strip NUL, C0/C1 controls, and bidi embedding/override/isolate characters. */
export function stripUnsafe(text: string): string {
  let out = "";
  for (const ch of text) {
    const cp = ch.codePointAt(0) ?? 0;
    if (cp === 0) continue;
    if (cp <= 0x1f || (cp >= 0x7f && cp <= 0x9f)) continue;
    if ((cp >= 0x202a && cp <= 0x202e) || (cp >= 0x2066 && cp <= 0x2069)) continue;
    out += ch;
  }
  return out;
}

function stringList(value: unknown, field: string): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value) || !value.every((item) => typeof item === "string")) {
    throw new HttpError(400, "bad_request", `'context.${field}' must be a list of strings`);
  }
  for (const item of value) {
    if (hasLoneSurrogate(item)) {
      throw new HttpError(400, "bad_request", `'context.${field}' contains an unpaired surrogate`);
    }
  }
  return value;
}

function cleanedList(values: string[]): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const value of values) {
    const text = stripUnsafe(value).trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    out.push(text);
    if (out.length >= MAX_CONTEXT_ITEMS) break;
  }
  return out;
}

export function parseContext(raw: unknown): ClientContext {
  if (raw === undefined || raw === null || raw === "") {
    return { categories: [], tags: [], locale: "" };
  }
  if (typeof raw !== "string") {
    throw new HttpError(400, "bad_request", "'context' must be a JSON string field");
  }
  if (hasLoneSurrogate(raw)) {
    throw new HttpError(400, "bad_request", "'context' contains an unpaired surrogate");
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    throw new HttpError(400, "bad_request", "'context' is not valid JSON");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new HttpError(400, "bad_request", "'context' must be a JSON object");
  }
  const obj = parsed as Record<string, unknown>;
  const categories = cleanedList(stringList(obj.categories, "categories"));
  const tags = cleanedList(stringList(obj.tags, "tags"));
  let locale = "";
  if (obj.locale !== undefined && obj.locale !== null) {
    if (typeof obj.locale !== "string") {
      throw new HttpError(400, "bad_request", "'context.locale' must be a string");
    }
    if (hasLoneSurrogate(obj.locale)) {
      throw new HttpError(400, "bad_request", "'context.locale' contains an unpaired surrogate");
    }
    locale = stripUnsafe(obj.locale).trim();
  }
  const out: ClientContext = { categories, tags, locale };
  if (obj.model !== undefined && obj.model !== null) {
    if (typeof obj.model !== "string") {
      throw new HttpError(400, "bad_request", "'context.model' must be a string");
    }
    if (hasLoneSurrogate(obj.model)) {
      throw new HttpError(400, "bad_request", "'context.model' contains an unpaired surrogate");
    }
    out.model = obj.model;
  }
  return out;
}

import { buildPrompt, buildSchema, REPAIR_LINE } from "./prompt";
import type { ClientContext } from "./context";
import { MODEL } from "./neurons";

export interface ModelTurn {
  content: string;
  finishReason: string | null;
  usage: unknown;
}

export interface PreparedAi {
  /** Same image object and schema on every call, including the repair. */
  inputFor(prompt: string): Record<string, unknown>;
  imagePart: { type: "image_url"; image_url: { url: string } };
}

/** One data URI, one schema. Repair changes only the text part. */
export function prepareAi(dataUrl: string, context: ClientContext): PreparedAi {
  const imagePart = { type: "image_url" as const, image_url: { url: dataUrl } };
  const responseFormat = {
    type: "json_schema",
    json_schema: {
      name: "suggestion",
      strict: true,
      schema: buildSchema(context),
    },
  };
  const thinking = { enable_thinking: false };
  return {
    imagePart,
    inputFor(prompt: string) {
      return {
        messages: [
          {
            role: "user",
            content: [{ type: "text", text: prompt }, imagePart],
          },
        ],
        response_format: responseFormat,
        chat_template_kwargs: thinking,
        temperature: 0,
        max_completion_tokens: 512,
      };
    },
  };
}

export function promptsFor(context: ClientContext): { first: string; repair: string } {
  const first = buildPrompt(context);
  return { first, repair: `${first}\n${REPAIR_LINE}` };
}

export function readTurn(result: unknown): ModelTurn {
  if (result && typeof result === "object") {
    const record = result as Record<string, unknown>;
    const choices = record.choices;
    if (Array.isArray(choices) && choices[0] && typeof choices[0] === "object") {
      const choice = choices[0] as Record<string, unknown>;
      const message = choice.message;
      const content = message && typeof message === "object" ? (message as { content?: unknown }).content : choice.text;
      return {
        content: contentToString(content),
        finishReason: typeof choice.finish_reason === "string" ? choice.finish_reason : null,
        usage: record.usage ?? null,
      };
    }
    if ("response" in record) {
      return { content: contentToString(record.response), finishReason: null, usage: record.usage ?? null };
    }
  }
  return { content: contentToString(result), finishReason: null, usage: null };
}

function contentToString(content: unknown): string {
  if (typeof content === "string") return content;
  if (content === undefined || content === null) return "";
  return JSON.stringify(content);
}

const AI_CODES = new Set([3036, 3040, 3006]);

/**
 * The real Workers AI binding throws InferenceUpstreamError with the code
 * only in the message: "3036: ...", optionally prefixed with the error name.
 * Digits elsewhere in the text are not a code. lastRequestInternalStatusCode
 * is not read; that field is shared across concurrent requests.
 */
const BINDING_MESSAGE = /^(?:InferenceUpstreamError:\s*)?(3036|3040|3006):\s/;

function bindingMessageCode(message: unknown): number | null {
  if (typeof message !== "string") return null;
  const match = BINDING_MESSAGE.exec(message);
  return match ? Number(match[1]) : null;
}

/** Internal Workers AI codes we map. 3036 daily free neurons, 3040 capacity, 3006 too large. */
export function workersAiCode(err: unknown): number | null {
  const seen = new Set<unknown>();
  const stack = [err];
  while (stack.length > 0) {
    const current = stack.pop();
    if (!current || typeof current !== "object" || seen.has(current)) continue;
    seen.add(current);
    const record = current as Record<string, unknown>;
    for (const key of ["code", "internalCode", "error_code"]) {
      const value = record[key];
      const code =
        typeof value === "number" ? value : typeof value === "string" && /^\d+$/.test(value) ? Number(value) : null;
      if (code !== null && AI_CODES.has(code)) return code;
    }
    const fromMessage = bindingMessageCode(record.message);
    if (fromMessage !== null) return fromMessage;
    if (Array.isArray(record.errors)) stack.push(...record.errors);
    if (record.error && typeof record.error === "object") stack.push(record.error);
    if (record.cause && typeof record.cause === "object") stack.push(record.cause);
  }
  return null;
}

/** HTTP status on a Workers AI error, when the binding attached one. */
export function workersAiHttpStatus(err: unknown): number | null {
  const seen = new Set<unknown>();
  const stack = [err];
  while (stack.length > 0) {
    const current = stack.pop();
    if (!current || typeof current !== "object" || seen.has(current)) continue;
    seen.add(current);
    const record = current as Record<string, unknown>;
    for (const key of ["status", "statusCode", "httpStatus"]) {
      const value = record[key];
      const code =
        typeof value === "number" ? value : typeof value === "string" && /^\d{3}$/.test(value) ? Number(value) : null;
      if (code !== null && code >= 400 && code <= 499) return code;
    }
    if (Array.isArray(record.errors)) stack.push(...record.errors);
    if (record.error && typeof record.error === "object") stack.push(record.error);
    if (record.cause && typeof record.cause === "object") stack.push(record.cause);
  }
  return null;
}

export function isAiStub(ai: unknown): boolean {
  try {
    if (!ai || typeof ai !== "object") return false;
    return (ai as { __autofill_stub__?: boolean }).__autofill_stub__ === true;
  } catch {
    return false;
  }
}

export const AI_MODEL = MODEL;

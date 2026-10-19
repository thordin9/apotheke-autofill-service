import { buildPrompt, buildSchema, REPAIR_LINE } from "./prompt";
import type { ClientContext } from "./context";
import { MODEL } from "./neurons";

export interface ModelTurn {
  content: string;
  finishReason: string | null;
  usage: unknown;
}

export function aiInput(prompt: string, dataUrl: string, context: ClientContext): Record<string, unknown> {
  return {
    messages: [
      {
        role: "user",
        content: [
          { type: "text", text: prompt },
          { type: "image_url", image_url: { url: dataUrl } },
        ],
      },
    ],
    response_format: {
      type: "json_schema",
      json_schema: {
        name: "suggestion",
        strict: true,
        schema: buildSchema(context),
      },
    },
    chat_template_kwargs: { enable_thinking: false },
    temperature: 0,
    max_completion_tokens: 512,
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
      const code = typeof value === "number" ? value : typeof value === "string" && /^\d+$/.test(value) ? Number(value) : null;
      if (code !== null && AI_CODES.has(code)) return code;
    }
    if (Array.isArray(record.errors)) stack.push(...record.errors);
    if (record.error && typeof record.error === "object") stack.push(record.error);
    if (record.cause && typeof record.cause === "object") stack.push(record.cause);
  }
  if (err instanceof Error) {
    const match = err.message.match(/\b(3036|3040|3006)\b/);
    if (match) return Number(match[1]);
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

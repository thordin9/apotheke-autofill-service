import { env } from "cloudflare:workers";
import { createExecutionContext, waitOnExecutionContext } from "cloudflare:test";

import worker from "../src/index";

export const TOKEN = "test-token";

export function jpeg(extra = ""): Uint8Array {
  const suffix = new TextEncoder().encode(extra);
  const bytes = new Uint8Array(4 + suffix.length);
  bytes.set([0xff, 0xd8, 0xff, 0xd9]);
  bytes.set(suffix, 4);
  return bytes;
}

export function png(): Uint8Array {
  return new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00]);
}

export function webp(): Uint8Array {
  const bytes = new Uint8Array(12);
  bytes.set([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]);
  return bytes;
}

export function multipart(
  image: Uint8Array,
  options?: { context?: string; filename?: string },
): { body: Uint8Array; contentType: string } {
  const boundary = "----autofilltest";
  const enc = new TextEncoder();
  const chunks = [
    enc.encode(
      `--${boundary}\r\nContent-Disposition: form-data; name="image"; filename="${options?.filename ?? "photo.bin"}"\r\nContent-Type: application/octet-stream\r\n\r\n`,
    ),
    image,
  ];
  if (options?.context !== undefined) {
    chunks.push(
      enc.encode(`\r\n--${boundary}\r\nContent-Disposition: form-data; name="context"\r\n\r\n${options.context}`),
    );
  }
  chunks.push(enc.encode(`\r\n--${boundary}--\r\n`));
  const body = new Uint8Array(chunks.reduce((sum, chunk) => sum + chunk.byteLength, 0));
  let offset = 0;
  for (const chunk of chunks) {
    body.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return { body, contentType: `multipart/form-data; boundary=${boundary}` };
}

export interface AiCall {
  model: string;
  input: Record<string, unknown>;
}

export function scriptedAi(replies: unknown[]) {
  const calls: AiCall[] = [];
  const ai = {
    __autofill_stub__: true,
    async run(model: string, input: Record<string, unknown>) {
      calls.push({ model, input });
      const next = replies[Math.min(calls.length - 1, replies.length - 1)];
      if (typeof next === "function") return (next as () => unknown)();
      return await Promise.resolve(next);
    },
  };
  return { ai, calls };
}

export function reply(content: unknown, usage?: unknown, finishReason = "stop") {
  return {
    choices: [
      {
        message: { role: "assistant", content: typeof content === "string" ? content : JSON.stringify(content) },
        finish_reason: finishReason,
      },
    ],
    usage: usage === undefined ? { neurons: 21.37 } : usage,
  };
}

export const VALID = {
  name: "Cordless drill",
  category: "Tools",
  tags: ["cordless", "drill"],
  description: "A cordless drill sitting on a bench",
  confidence: 0.82,
  brand: "Bosch",
  model_number: "GSB 180",
  serial_number: "",
};

export function suggestRequest(
  image: Uint8Array = jpeg(),
  options?: { token?: string | null; context?: string; headers?: HeadersInit; now?: number },
): Request {
  const part = multipart(image, { context: options?.context });
  const headers = new Headers(options?.headers);
  headers.set("content-type", part.contentType);
  if (options?.token !== null) headers.set("authorization", `Bearer ${options?.token ?? TOKEN}`);
  if (options?.now !== undefined) headers.set("x-autofill-now", String(options.now));
  return new Request("https://autofill.example/v1/suggest", { method: "POST", headers, body: part.body });
}

const allowAll = {
  async limit(_options: { key: string }) {
    return { success: true };
  },
};

export async function call(request: Request, overrides: Record<string, unknown> = {}): Promise<Response> {
  const ctx = createExecutionContext();
  const response = await worker.fetch(
    request,
    { ...env, RATE_LIMITER: allowAll, ...overrides } as Env,
    ctx,
  );
  await waitOnExecutionContext(ctx);
  return response;
}

export async function resetBudget(): Promise<void> {
  await env.BUDGET.getByName("global").reset();
}

export function captureLogs(): { lines: string[]; restore: () => void } {
  const lines: string[] = [];
  const original = console.log;
  console.log = (...args: unknown[]) => {
    lines.push(args.map((arg) => String(arg)).join(" "));
    original.apply(console, args as []);
  };
  return {
    lines,
    restore: () => {
      console.log = original;
    },
  };
}

export async function bodyOf(response: Response): Promise<Record<string, unknown>> {
  return (await response.json()) as Record<string, unknown>;
}

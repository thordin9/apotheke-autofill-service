import { aiInput, AI_MODEL, isAiStub, promptsFor, readTurn, workersAiCode, workersAiHttpStatus } from "./ai";
import { bearerToken, tokensMatch } from "./auth";
import { bytesToBase64 } from "./base64";
import { Budget } from "./budget";
import { parseContext } from "./context";
import { errorResponse, HttpError, httpErrorResponse, json, readLimitedBody } from "./http";
import { sniffMime } from "./images";
import { logRequest, type RequestTiming } from "./log";
import {
  NEURON_ESTIMATE,
  neuronsFromUsage,
  parseMaxUpload,
  parseNeuronBudget,
  parseRequestCap,
} from "./neurons";
import { secondsUntilUtcMidnight } from "./time";
import { clamp, extractJson, NOT_JSON, NO_FIELDS, publicSuggestion, SuggestionError } from "./validate";

export { Budget };

/** Keep equal to the `period` of RATE_LIMITER in wrangler.toml. */
const RATE_LIMIT_PERIOD_S = 60;

const CAPABILITIES = ["name", "category", "tags", "description"];

interface AiRun {
  run(model: string, input: Record<string, unknown>): Promise<unknown>;
}

function testMode(env: Env): boolean {
  return (env as Env & { AUTOFILL_REQUIRE_AI_STUB?: string }).AUTOFILL_REQUIRE_AI_STUB === "1";
}

function nowMs(request: Request, env: Env): number {
  if (testMode(env)) {
    const raw = request.headers.get("x-autofill-now");
    if (raw && /^\d+$/.test(raw)) return Number(raw);
  }
  return Date.now();
}

function capacityError(now: number): HttpError {
  return new HttpError(503, "backend_unavailable", "daily capacity is exhausted", {
    "retry-after": String(secondsUntilUtcMidnight(now)),
  });
}

async function authorize(request: Request, env: Env): Promise<void> {
  if (!env.AUTOFILL_TOKEN) {
    throw new HttpError(503, "backend_unavailable", "service is not configured");
  }
  const given = bearerToken(request.headers.get("authorization") ?? "");
  if (!(await tokensMatch(given, env.AUTOFILL_TOKEN))) {
    throw new HttpError(401, "unauthorized", "missing or invalid bearer token", {
      "www-authenticate": "Bearer",
    });
  }
}

function limits(env: Env): { neuronBudget: number; requestCap: number; maxBytes: number } {
  const neuronBudget = parseNeuronBudget(env.AUTOFILL_DAILY_NEURON_BUDGET);
  const requestCap = parseRequestCap(env.AUTOFILL_DAILY_REQUEST_CAP);
  if (neuronBudget === null || requestCap === "bad") {
    throw new HttpError(503, "backend_unavailable", "service is not configured");
  }
  return { neuronBudget, requestCap: requestCap ?? 0, maxBytes: parseMaxUpload(env.MAX_UPLOAD_BYTES) };
}

async function info(request: Request, env: Env): Promise<Response> {
  const { neuronBudget, requestCap, maxBytes } = limits(env);
  const now = nowMs(request, env);
  let snap;
  try {
    snap = await env.BUDGET.getByName("global").snapshot(now);
  } catch {
    throw new HttpError(503, "backend_unavailable", "the service could not complete the request");
  }
  const neuronRemaining = snap.exhausted ? 0 : Math.max(0, neuronBudget - snap.used - snap.reserved);
  const requestsRemaining = requestCap > 0 ? Math.max(0, requestCap - snap.requests) : null;
  const ready = !snap.exhausted && neuronRemaining > 0 && (requestsRemaining === null || requestsRemaining > 0);
  return json({
    name: "cloudflare-workers",
    version: "1",
    capabilities: CAPABILITIES,
    backend: "cloudflare-workers",
    backend_used: "cloudflare-workers",
    max_image_bytes: maxBytes,
    ready,
    budget: {
      neurons: {
        limit: neuronBudget,
        used: snap.used,
        reserved: snap.reserved,
        remaining: neuronRemaining,
      },
      requests: {
        limit: requestCap > 0 ? requestCap : null,
        used: snap.requests,
        remaining: requestsRemaining,
      },
      resets_at: snap.resetsAt,
    },
  });
}

async function suggest(request: Request, env: Env, timing: RequestTiming): Promise<Response> {
  const key = request.headers.get("CF-Connecting-IP") || "unknown";
  let allowed = false;
  try {
    allowed = (await env.RATE_LIMITER.limit({ key })).success;
  } catch {
    throw new HttpError(503, "backend_unavailable", "the service could not complete the request");
  }
  if (!allowed) {
    throw new HttpError(429, "rate_limited", "too many requests", {
      "retry-after": String(RATE_LIMIT_PERIOD_S),
    });
  }
  const { neuronBudget, requestCap, maxBytes } = limits(env);
  const body = await readLimitedBody(request, maxBytes);
  const form = await readForm(body, request.headers.get("content-type"));
  const image = await imageBytes(form);
  const mime = sniffMime(image);
  if (!mime) throw new HttpError(415, "unsupported_media_type", "image must be JPEG, PNG or WebP");
  const contextPart = form.get("context");
  if (contextPart !== null && typeof contextPart !== "string") {
    throw new HttpError(400, "bad_request", "'context' must be a JSON string field");
  }
  const context = parseContext(contextPart ?? undefined);
  const encodedAt = performance.now();
  const dataUrl = `data:${mime};base64,${bytesToBase64(image)}`;
  timing.base64Ms += performance.now() - encodedAt;
  timing.model = AI_MODEL;

  const now = nowMs(request, env);
  const budget = env.BUDGET.getByName("global");
  const prompts = promptsFor(context);

  const attempt = async (prompt: string, countRequest: boolean) => {
    let reserved = false;
    let called = false;
    try {
      if (testMode(env) && !isAiStub(env.AI)) {
        throw new HttpError(503, "backend_unavailable", "refused to call Workers AI");
      }
      const gate = await budget.reserve({
        nowMs: now,
        neuronEstimate: NEURON_ESTIMATE,
        neuronBudget,
        requestCap,
        countRequest,
      });
      if (!gate.ok) throw capacityError(now);
      reserved = true;
      const raw = await (env.AI as unknown as AiRun).run(AI_MODEL, aiInput(prompt, dataUrl, context));
      called = true;
      timing.aiCalls += 1;
      const embedded = workersAiCode(raw);
      if (embedded) throw Object.assign(new Error("workers ai"), { code: embedded });
      if (raw instanceof Error) throw raw;
      const returnedStatus = workersAiHttpStatus(raw);
      if (returnedStatus !== null) throw Object.assign(new Error("workers ai"), { status: returnedStatus });
      const turn = readTurn(raw);
      const charge = neuronsFromUsage(turn.usage);
      reserved = false;
      try {
        await budget.settle({ nowMs: now, neuronEstimate: NEURON_ESTIMATE, neuronActual: charge.neurons });
      } catch {
        throw new HttpError(503, "backend_unavailable", "the service could not complete the request");
      }
      timing.neurons += charge.neurons;
      return turn;
    } catch (err) {
      if (reserved) {
        try {
          await budget.settle({ nowMs: now, neuronEstimate: NEURON_ESTIMATE, neuronActual: NEURON_ESTIMATE });
          timing.neurons += NEURON_ESTIMATE;
        } catch {
          // The reservation stays held, which fails closed.
        }
      }
      const code = workersAiCode(err);
      if (code === 3036) {
        try {
          await budget.markExhausted(now);
        } catch {
          // Still refuse below.
        }
        throw capacityError(now);
      }
      if (code === 3040) throw capacityError(now);
      if (code === 3006) throw new HttpError(413, "payload_too_large", `image exceeds ${maxBytes} bytes`);
      const httpStatus = workersAiHttpStatus(err);
      if (httpStatus !== null) {
        throw new HttpError(503, "backend_unavailable", "the model is unavailable");
      }
      if (err instanceof HttpError) throw err;
      throw new HttpError(
        503,
        "backend_unavailable",
        called ? "the model is unavailable" : "the service could not complete the request",
      );
    }
  };

  const judge = (content: string) => {
    const parsedAt = performance.now();
    let obj: Record<string, unknown> | null = null;
    let parseMessage = NOT_JSON;
    try {
      obj = extractJson(content);
    } catch (err) {
      parseMessage = err instanceof SuggestionError ? err.message : NOT_JSON;
    }
    timing.parseMs += performance.now() - parsedAt;
    const validatedAt = performance.now();
    if (!obj) {
      timing.validateMs += performance.now() - validatedAt;
      return { ok: false as const, message: parseMessage };
    }
    try {
      const suggestion = clamp(obj, context);
      timing.validateMs += performance.now() - validatedAt;
      return { ok: true as const, suggestion };
    } catch (err) {
      timing.validateMs += performance.now() - validatedAt;
      const message = err instanceof SuggestionError ? err.message : NO_FIELDS;
      return { ok: false as const, message };
    }
  };

  const first = await attempt(prompts.first, true);
  let judged = judge(first.content);
  if (!judged.ok) {
    const second = await attempt(prompts.repair, false);
    judged = judge(second.content);
    if (!judged.ok) throw new HttpError(503, "backend_error", judged.message);
  }
  return json(publicSuggestion(judged.suggestion, AI_MODEL));
}

async function readForm(bytes: Uint8Array, contentType: string | null): Promise<FormData> {
  if (!contentType || !contentType.toLowerCase().includes("multipart/form-data")) {
    throw new HttpError(400, "bad_request", "expected multipart/form-data");
  }
  try {
    return await new Request("https://autofill.invalid/v1/suggest", {
      method: "POST",
      headers: { "content-type": contentType },
      body: bytes,
    }).formData();
  } catch {
    throw new HttpError(400, "bad_request", "expected multipart/form-data");
  }
}

async function imageBytes(form: FormData): Promise<Uint8Array> {
  const image = form.get("image");
  if (!(image instanceof File)) throw new HttpError(400, "bad_request", "missing 'image' file part");
  const bytes = new Uint8Array(await image.arrayBuffer());
  if (bytes.byteLength === 0) throw new HttpError(400, "bad_request", "'image' is empty");
  return bytes;
}

async function route(request: Request, env: Env, timing: RequestTiming): Promise<Response> {
  const path = new URL(request.url).pathname;
  if (request.method === "GET" && path === "/healthz") {
    if (!env.AUTOFILL_TOKEN) return json({ status: "degraded" }, 503);
    return json({ status: "ok" });
  }
  await authorize(request, env);
  if (request.method === "GET" && path === "/v1/info") return info(request, env);
  if (request.method === "POST" && path === "/v1/suggest") return suggest(request, env, timing);
  if (path === "/healthz" || path === "/v1/info" || path === "/v1/suggest") {
    throw new HttpError(405, "bad_request", "Method Not Allowed");
  }
  throw new HttpError(404, "bad_request", "Not Found");
}

async function handle(request: Request, env: Env): Promise<Response> {
  const started = performance.now();
  const timing: RequestTiming = {
    base64Ms: 0,
    parseMs: 0,
    validateMs: 0,
    neurons: 0,
    model: "",
    aiCalls: 0,
  };
  try {
    const response = await route(request, env, timing);
    logRequest(response.status, started, timing);
    return response;
  } catch (err) {
    const response =
      err instanceof HttpError
        ? httpErrorResponse(err)
        : errorResponse(503, "backend_unavailable", "the service could not complete the request");
    logRequest(response.status, started, timing);
    return response;
  }
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    void ctx;
    return handle(request, env);
  },
} satisfies ExportedHandler<Env>;

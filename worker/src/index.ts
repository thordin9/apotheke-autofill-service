import { AI_MODEL, isAiStub, prepareAi, promptsFor, readTurn, workersAiCode, workersAiHttpStatus } from "./ai";
import { bearerToken, tokensMatch } from "./auth";
import { bytesToBase64 } from "./base64";
import { Budget } from "./budget";
import { parseContext } from "./context";
import { errorResponse, HttpError, httpErrorResponse, json, readLimitedBody } from "./http";
import { logRequest, type RequestTiming } from "./log";
import { advertisedImageBytes, maxImageBytes, readSuggestParts } from "./multipart";
import { resolveTier } from "./tier";
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
/** 3040 is a short capacity blip, not the UTC-midnight budget. Bounded 10–60 s. */
const CAPACITY_RETRY_AFTER_S = 30;

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

function budgetError(now: number): HttpError {
  return new HttpError(503, "backend_unavailable", "daily capacity is exhausted", {
    "retry-after": String(secondsUntilUtcMidnight(now)),
  });
}

function capacityBlip(): HttpError {
  return new HttpError(503, "backend_unavailable", "the model is out of capacity", {
    "retry-after": String(CAPACITY_RETRY_AFTER_S),
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
  const ready =
    !snap.exhausted &&
    neuronRemaining >= NEURON_ESTIMATE &&
    (requestsRemaining === null || requestsRemaining > 0);
  const tier = resolveTier(env.TIER);
  const enforced = maxImageBytes(maxBytes);
  return json({
    name: "cloudflare-workers",
    version: "1",
    capabilities: CAPABILITIES,
    backend: "cloudflare-workers",
    backend_used: "cloudflare-workers",
    max_image_bytes: Math.min(advertisedImageBytes(tier), enforced),
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
    enforced_image_bytes: enforced,
    tier,
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
  const imageCap = maxImageBytes(maxBytes);
  const body = await readLimitedBody(request, maxBytes);
  const parts = readSuggestParts(body, request.headers.get("content-type"), imageCap);
  const context = parseContext(parts.context);
  const encodedAt = Date.now();
  const dataUrl = `data:${parts.mime};base64,${bytesToBase64(parts.image)}`;
  timing.base64Ms += Date.now() - encodedAt;
  timing.model = AI_MODEL;

  const now = nowMs(request, env);
  const budget = env.BUDGET.getByName("global");
  const prompts = promptsFor(context);
  const prepared = prepareAi(dataUrl, context);

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
      if (!gate.ok) throw budgetError(now);
      reserved = true;
      const raw = await (env.AI as unknown as AiRun).run(AI_MODEL, prepared.inputFor(prompt));
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
        throw budgetError(now);
      }
      if (code === 3040) throw capacityBlip();
      if (code === 3006) throw new HttpError(413, "payload_too_large", `image exceeds ${imageCap} bytes`);
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
    const parsedAt = Date.now();
    let obj: Record<string, unknown> | null = null;
    let parseMessage = NOT_JSON;
    try {
      obj = extractJson(content);
    } catch (err) {
      parseMessage = err instanceof SuggestionError ? err.message : NOT_JSON;
    }
    timing.parseMs += Date.now() - parsedAt;
    const validatedAt = Date.now();
    if (!obj) {
      timing.validateMs += Date.now() - validatedAt;
      return { ok: false as const, message: parseMessage };
    }
    try {
      const suggestion = clamp(obj, context);
      timing.validateMs += Date.now() - validatedAt;
      return { ok: true as const, suggestion };
    } catch (err) {
      timing.validateMs += Date.now() - validatedAt;
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

async function route(request: Request, env: Env, timing: RequestTiming): Promise<Response> {
  const path = new URL(request.url).pathname;
  if (request.method === "GET" && path === "/healthz") {
    if (!env.AUTOFILL_TOKEN) return json({ status: "degraded" }, 503);
    return json({ status: "ok" });
  }
  if (path !== "/healthz" && path !== "/v1/info" && path !== "/v1/suggest") {
    throw new HttpError(404, "bad_request", "Not Found");
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
  const started = Date.now();
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

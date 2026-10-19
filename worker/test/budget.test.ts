import { beforeEach, describe, expect, it } from "vitest";

import { INPUT_NEURONS_PER_MILLION, OUTPUT_NEURONS_PER_MILLION } from "../src/neurons";
import {
  VALID,
  bodyOf,
  call,
  jpeg,
  reply,
  resetBudget,
  scriptedAi,
  suggestRequest,
  TOKEN,
} from "./helpers";

beforeEach(resetBudget);

const evening = Date.parse("2026-10-19T23:00:00.000Z");
const morning = Date.parse("2026-10-20T00:00:00.000Z");

function infoRequest(now?: number): Request {
  const headers = new Headers({ authorization: `Bearer ${TOKEN}` });
  if (now !== undefined) headers.set("x-autofill-now", String(now));
  return new Request("https://autofill.example/v1/info", { headers });
}

describe("budget", () => {
  it("reports the Python info fields plus the neuron budget", async () => {
    const response = await call(infoRequest(evening));
    expect(response.status).toBe(200);
    const body = await bodyOf(response);
    expect(body.name).toBe("cloudflare-workers");
    expect(body.version).toBe("1");
    expect(body.capabilities).toEqual(["name", "category", "tags", "description"]);
    expect(body.backend).toBe("cloudflare-workers");
    expect(body.backend_used).toBe("cloudflare-workers");
    expect(body.max_image_bytes).toBe(3145728);
    expect(body.ready).toBe(true);
    expect(body.budget).toEqual({
      neurons: { limit: 6000, used: 0, reserved: 0, remaining: 6000 },
      requests: { limit: null, used: 0, remaining: null },
      resets_at: "2026-10-20T00:00:00.000Z",
    });
  });

  it("accounts usage.neurons, the Gemma rate table, and the estimate", async () => {
    const usage = scriptedAi([reply(VALID, { neurons: 21.37, prompt_tokens: 10, completion_tokens: 10 })]);
    expect((await call(suggestRequest(), { AI: usage.ai })).status).toBe(200);
    const afterUsage = await bodyOf(await call(infoRequest()));
    expect((afterUsage.budget as { neurons: { used: number } }).neurons.used).toBe(21.37);

    await resetBudget();
    const rated = scriptedAi([reply(VALID, { prompt_tokens: 1000, completion_tokens: 100 })]);
    expect((await call(suggestRequest(), { AI: rated.ai })).status).toBe(200);
    const expected = (1000 * INPUT_NEURONS_PER_MILLION + 100 * OUTPUT_NEURONS_PER_MILLION) / 1_000_000;
    const afterRates = await bodyOf(await call(infoRequest()));
    expect((afterRates.budget as { neurons: { used: number } }).neurons.used).toBeCloseTo(expected, 6);

    await resetBudget();
    const estimated = scriptedAi([reply(VALID, {})]);
    expect((await call(suggestRequest(), { AI: estimated.ai })).status).toBe(200);
    const afterEstimate = await bodyOf(await call(infoRequest()));
    expect((afterEstimate.budget as { neurons: { used: number } }).neurons.used).toBe(30);
  });

  it("counts a repair retry", async () => {
    const { ai } = scriptedAi([reply("nope", {}), reply(VALID, { neurons: 10 })]);
    expect((await call(suggestRequest(), { AI: ai })).status).toBe(200);
    const body = await bodyOf(await call(infoRequest()));
    expect((body.budget as { neurons: { used: number }; requests: { used: number } }).neurons.used).toBe(40);
    expect((body.budget as { requests: { used: number } }).requests.used).toBe(1);
  });

  it("keeps the counter when the clock goes backwards", async () => {
    const ahead = Date.parse("2026-10-21T12:00:00.000Z");
    const behind = Date.parse("2026-10-19T12:00:00.000Z");
    const { ai } = scriptedAi([reply(VALID, { neurons: 30 }), reply(VALID, { neurons: 30 })]);
    const env = { AI: ai, AUTOFILL_DAILY_NEURON_BUDGET: "30" };
    expect((await call(suggestRequest(jpeg(), { now: ahead }), env)).status).toBe(200);
    const blocked = await call(suggestRequest(jpeg(), { now: behind }), env);
    expect(blocked.status).toBe(503);
    const info = await bodyOf(await call(infoRequest(behind)));
    expect((info.budget as { neurons: { used: number; remaining: number } }).neurons.used).toBe(30);
    expect((info.budget as { neurons: { remaining: number } }).neurons.remaining).toBe(5970);
  });

  it("returns 503 and Retry-After once the cap is reached, then resets at UTC midnight", async () => {
    const { ai } = scriptedAi([reply(VALID, { neurons: 30 }), reply(VALID, { neurons: 30 }), reply(VALID, { neurons: 30 })]);
    const env = { AI: ai, AUTOFILL_DAILY_NEURON_BUDGET: "30" };
    expect((await call(suggestRequest(jpeg(), { now: evening }), env)).status).toBe(200);
    const blocked = await call(suggestRequest(jpeg(), { now: evening }), env);
    expect(blocked.status).toBe(503);
    expect(blocked.headers.get("retry-after")).toBe("3600");
    expect(await bodyOf(blocked)).toEqual({
      error: { code: "backend_unavailable", message: "daily capacity is exhausted" },
    });
    const info = await bodyOf(await call(infoRequest(evening), env));
    expect((info.budget as { neurons: { remaining: number } }).neurons.remaining).toBe(0);
    expect(info.ready).toBe(false);
    expect((await call(suggestRequest(jpeg(), { now: morning }), env)).status).toBe(200);
    const reset = await bodyOf(await call(infoRequest(morning), env));
    expect((reset.budget as { neurons: { used: number; remaining: number } }).neurons.used).toBe(30);
    expect((reset.budget as { neurons: { remaining: number } }).neurons.remaining).toBe(0);
    expect(reset.budget).toMatchObject({ resets_at: "2026-10-21T00:00:00.000Z" });
  });

  it("never admits more concurrent requests than the cap", async () => {
    const neurons = scriptedAi([reply(VALID, { neurons: 30 })]);
    const neuronResponses = await Promise.all(
      Array.from({ length: 8 }, () =>
        call(suggestRequest(), { AI: neurons.ai, AUTOFILL_DAILY_NEURON_BUDGET: "60" }),
      ),
    );
    expect(neuronResponses.filter((response) => response.status === 200)).toHaveLength(2);
    expect(neuronResponses.filter((response) => response.status === 503)).toHaveLength(6);

    await resetBudget();
    const requests = scriptedAi([reply(VALID, { neurons: 1 })]);
    const requestResponses = await Promise.all(
      Array.from({ length: 8 }, () =>
        call(suggestRequest(), {
          AI: requests.ai,
          AUTOFILL_DAILY_NEURON_BUDGET: "6000",
          AUTOFILL_DAILY_REQUEST_CAP: "3",
        }),
      ),
    );
    expect(requestResponses.filter((response) => response.status === 200)).toHaveLength(3);
    expect(requestResponses.filter((response) => response.status === 503)).toHaveLength(5);
  });

  it("fails safe when Durable Object storage throws", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const response = await call(suggestRequest(), {
      AI: ai,
      BUDGET: {
        getByName() {
          return {
            async reserve() {
              throw new Error("storage boom SECRET");
            },
          };
        },
      },
    });
    expect(response.status).toBe(503);
    const text = JSON.stringify(await response.json());
    expect(text).not.toContain("storage boom");
    expect(text).not.toContain("SECRET");
    expect(text).toContain("the service could not complete the request");
    expect(calls).toHaveLength(0);
  });

  it("returns 429 after auth when the rate limit binding says no", async () => {
    let key = "";
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const response = await call(suggestRequest(jpeg(), { headers: { "CF-Connecting-IP": "client-a" } }), {
      AI: ai,
      RATE_LIMITER: {
        async limit(options: { key: string }) {
          key = options.key;
          return { success: false };
        },
      },
    });
    expect(response.status).toBe(429);
    expect(response.headers.get("retry-after")).toBe("60");
    expect(await bodyOf(response)).toEqual({ error: { code: "rate_limited", message: "too many requests" } });
    expect(key).toBe("client-a");
    expect(calls).toHaveLength(0);
  });
});

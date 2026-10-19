import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { tokensMatch } from "../src/auth";
import { VALID, bodyOf, call, jpeg, reply, resetBudget, scriptedAi, suggestRequest, TOKEN } from "./helpers";

beforeEach(resetBudget);

describe("auth and health", () => {
  it("healthz is open and ok when the token is set", async () => {
    const response = await call(new Request("https://autofill.example/healthz"));
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ status: "ok" });
  });

  it("healthz ignores a bad bearer token", async () => {
    const response = await call(
      new Request("https://autofill.example/healthz", { headers: { authorization: "Bearer wrong" } }),
    );
    expect(response.status).toBe(200);
  });

  it("healthz is degraded when the secret is missing", async () => {
    const response = await call(new Request("https://autofill.example/healthz"), { AUTOFILL_TOKEN: "" });
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ status: "degraded" });
  });

  it("rejects a missing token with 401 after a digest compare", async () => {
    const lengths: number[] = [];
    const subtle = crypto.subtle as unknown as {
      timingSafeEqual: (a: ArrayBufferView, b: ArrayBufferView) => boolean;
    };
    const original = subtle.timingSafeEqual.bind(crypto.subtle);
    subtle.timingSafeEqual = (left, right) => {
      lengths.push(left.byteLength, right.byteLength);
      return original(left, right);
    };
    try {
      const response = await call(suggestRequest(jpeg(), { token: null }));
      expect(response.status).toBe(401);
      expect(response.headers.get("www-authenticate")).toBe("Bearer");
      expect(await bodyOf(response)).toEqual({
        error: { code: "unauthorized", message: "missing or invalid bearer token" },
      });
    } finally {
      subtle.timingSafeEqual = original;
    }
    expect(lengths.length).toBeGreaterThan(0);
    expect(lengths.every((length) => length === 32)).toBe(true);
  });

  it("rejects a wrong token and accepts the right one", async () => {
    const wrong = await call(suggestRequest(jpeg(), { token: "wrong-token" }));
    expect(wrong.status).toBe(401);
    const { ai } = scriptedAi([reply(VALID)]);
    const right = await call(suggestRequest(), { AI: ai });
    expect(right.status).toBe(200);
  });

  it("fails closed on /v1 when the secret is missing or empty", async () => {
    for (const token of [undefined, ""]) {
      const info = await call(new Request("https://autofill.example/v1/info"), { AUTOFILL_TOKEN: token });
      expect(info.status).toBe(503);
      expect(await bodyOf(info)).toEqual({
        error: { code: "backend_unavailable", message: "service is not configured" },
      });
      const suggest = await call(suggestRequest(), { AUTOFILL_TOKEN: token });
      expect(suggest.status).toBe(503);
      expect(await bodyOf(suggest)).toEqual({
        error: { code: "backend_unavailable", message: "service is not configured" },
      });
      const health = await call(new Request("https://autofill.example/healthz"), { AUTOFILL_TOKEN: token });
      expect(health.status).toBe(503);
      expect(await health.json()).toEqual({ status: "degraded" });
    }
  });

  it("compares unequal lengths without throwing", async () => {
    await expect(tokensMatch("a", "much-longer-token")).resolves.toBe(false);
    await expect(tokensMatch(TOKEN, TOKEN)).resolves.toBe(true);
  });

  it("does not call the rate limiter before auth succeeds", async () => {
    let limited = 0;
    const response = await call(suggestRequest(jpeg(), { token: "nope" }), {
      RATE_LIMITER: {
        async limit() {
          limited += 1;
          return { success: false };
        },
      },
    });
    expect(response.status).toBe(401);
    expect(limited).toBe(0);
  });

  it("returns 404 and 405 with the error envelope", async () => {
    const missing = await call(
      new Request("https://autofill.example/nope", { headers: { authorization: `Bearer ${TOKEN}` } }),
    );
    expect(missing.status).toBe(404);
    expect(await bodyOf(missing)).toEqual({ error: { code: "bad_request", message: "Not Found" } });
    const method = await call(
      new Request("https://autofill.example/v1/info", {
        method: "POST",
        headers: { authorization: `Bearer ${TOKEN}` },
      }),
    );
    expect(method.status).toBe(405);
  });

  it("uses the configured token from the test binding", () => {
    expect(env.AUTOFILL_TOKEN).toBe(TOKEN);
  });
});

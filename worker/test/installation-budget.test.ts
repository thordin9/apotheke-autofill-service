import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { LIMIT_MESSAGE, WARNING_MESSAGE } from "../src/installation";
import { VALID, bodyOf, call, callHosted, jpeg, reply, resetBudget, scriptedAi, suggestRequest } from "./helpers";

beforeEach(resetBudget);

const midMonth = Date.parse("2026-11-13T12:00:00.000Z");
const nextMonth = Date.parse("2026-12-01T00:00:00.000Z");

// The edge Worker sends no bearer token through the service binding.
function hosted(id: string, headers: Record<string, string> = {}, now = midMonth): Request {
  return suggestRequest(jpeg(), { now, token: null, headers: { "x-apotheke-installation": id, ...headers } });
}

describe("per-installation monthly autofill budget", () => {
  it("refuses any installation header on the public routes, before the token check, and counts nothing", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const sets: Record<string, string>[] = [
      { "x-apotheke-installation": "spoofed" },
      { "x-apotheke-autofill-limit": "0" },
      { "x-apotheke-autofill-soft": "0" },
    ];
    for (const headers of sets) {
      for (const token of [undefined, null]) {
        const res = await call(suggestRequest(jpeg(), { now: midMonth, token, headers }), { AI: ai });
        expect(res.status).toBe(400);
        expect(((await bodyOf(res)).error as { code: string }).code).toBe("bad_request");
      }
    }
    expect(calls).toHaveLength(0);
    expect(await env.BUDGET.getByName("global").snapshot(midMonth)).toMatchObject({ requests: 0 });
    expect(await env.BUDGET.getByName("installation:spoofed").reserveMonthly({ nowMs: midMonth, hard: 1, soft: 1 })).toMatchObject({ ok: true, used: 1 });
  });

  it("leaves a public request without installation headers exactly as before", async () => {
    const { ai } = scriptedAi([reply(VALID)]);
    const res = await call(suggestRequest(jpeg(), { now: midMonth }), { AI: ai });
    expect(res.status).toBe(200);
    expect((await bodyOf(res)).warning).toBeUndefined();
  });

  it("requires the installation header on the hosted entrypoint and needs no bearer token there", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const missing = await callHosted(suggestRequest(jpeg(), { now: midMonth, token: null }), { AI: ai });
    expect(missing.status).toBe(400);
    expect(calls).toHaveLength(0);
    expect((await callHosted(hosted("no-token"), { AI: ai })).status).toBe(200);
  });

  it("keys the rate limiter by installation on the hosted entrypoint", async () => {
    const { ai } = scriptedAi([reply(VALID)]);
    const keys: string[] = [];
    const limiter = { async limit({ key }: { key: string }) { keys.push(key); return { success: true }; } };
    await callHosted(hosted("rate-one", {}, midMonth), { AI: ai, RATE_LIMITER: limiter });
    const viaIp = suggestRequest(jpeg(), { now: midMonth, headers: { "cf-connecting-ip": "192.0.2.1" } });
    await call(viaIp, { AI: ai, RATE_LIMITER: limiter });
    expect(keys).toEqual(["installation:rate-one", "192.0.2.1"]);
  });

  it("refuses past the hard value with the plan message, without calling the model", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const id = "hard-limit";
    for (let i = 0; i < 2; i++) expect((await callHosted(hosted(id, { "x-apotheke-autofill-limit": "2" }), { AI: ai })).status).toBe(200);
    const refused = await callHosted(hosted(id, { "x-apotheke-autofill-limit": "2" }), { AI: ai });
    expect(refused.status).toBe(429);
    expect(await bodyOf(refused)).toEqual({ error: { code: "autofill_limit", message: LIMIT_MESSAGE } });
    expect(LIMIT_MESSAGE).toBe("Autofill limit reached for this month");
    expect(refused.headers.get("retry-after")).toBe(String((nextMonth - midMonth) / 1000));
    expect(calls).toHaveLength(2);
    // Another installation has its own count, and a new month starts from zero.
    expect((await callHosted(hosted("other-one", { "x-apotheke-autofill-limit": "2" }), { AI: ai })).status).toBe(200);
    expect((await callHosted(hosted(id, { "x-apotheke-autofill-limit": "2" }, nextMonth), { AI: ai })).status).toBe(200);
  });

  it("adds a warning field from the soft value (80% of hard by default)", async () => {
    const { ai } = scriptedAi([reply(VALID)]);
    const warnings: unknown[] = [];
    for (let i = 0; i < 5; i++) {
      const res = await callHosted(hosted("soft-default", { "x-apotheke-autofill-limit": "5" }), { AI: ai });
      warnings.push((await bodyOf(res)).warning ?? null);
    }
    expect(warnings).toEqual([null, null, null, WARNING_MESSAGE, WARNING_MESSAGE]);
    const explicit = await callHosted(hosted("soft-set", { "x-apotheke-autofill-limit": "5", "x-apotheke-autofill-soft": "1" }), { AI: ai });
    expect((await bodyOf(explicit)).warning).toBe(WARNING_MESSAGE);
  });

  it("only counts an installation without a limit header", async () => {
    const { ai } = scriptedAi([reply(VALID)]);
    for (let i = 0; i < 3; i++) {
      const res = await callHosted(hosted("no-limit"), { AI: ai });
      expect(res.status).toBe(200);
      expect((await bodyOf(res)).warning).toBeUndefined();
    }
    expect(await env.BUDGET.getByName("installation:no-limit").reserveMonthly({ nowMs: midMonth, hard: 3, soft: 3 })).toEqual({ ok: false, used: 3, warn: true });
  });

  it("rejects a malformed installation id or limit with 400", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const bad: Record<string, string>[] = [{ "x-apotheke-installation": "Bad_Id" }, { "x-apotheke-installation": "ok", "x-apotheke-autofill-limit": "-1" }];
    for (const headers of bad) {
      const res = await callHosted(suggestRequest(jpeg(), { now: midMonth, token: null, headers }), { AI: ai });
      expect(res.status).toBe(400);
      expect(((await bodyOf(res)).error as { code: string }).code).toBe("bad_request");
    }
    expect(calls).toHaveLength(0);
  });
});

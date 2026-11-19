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

  it("refunds a failed call so it does not use up the month (L-2191)", async () => {
    const id = "refund-failed";
    const exhausted = { AUTOFILL_DAILY_NEURON_BUDGET: "0" };
    const { ai, calls } = scriptedAi([reply(VALID)]);
    for (let i = 0; i < 7; i++) {
      const res = await callHosted(hosted(id, { "x-apotheke-autofill-limit": "7" }), { AI: ai, ...exhausted });
      expect(res.status).toBe(503);
    }
    expect(calls).toHaveLength(0);
    expect(await env.BUDGET.getByName(`installation:${id}`).reserveMonthly({ nowMs: midMonth, hard: 0, soft: 0 })).toEqual({
      ok: false,
      used: 0,
      warn: true,
    });
    // The month is still free: a later success is admitted, not 429.
    expect((await callHosted(hosted(id, { "x-apotheke-autofill-limit": "7" }), { AI: ai })).status).toBe(200);
    expect(await env.BUDGET.getByName(`installation:${id}`).reserveMonthly({ nowMs: midMonth, hard: 0, soft: 0 })).toEqual({
      ok: false,
      used: 1,
      warn: true,
    });
  });

  it("keeps the count after a model error, a parse failure or a repaired success (L-2295, L-2298)", async () => {
    const boom = scriptedAi([() => Promise.reject(new Error("model down"))]);
    const model = await callHosted(hosted("refund-model", { "x-apotheke-autofill-limit": "1" }), { AI: boom.ai });
    expect(model.status).toBe(503);
    expect(boom.calls).toHaveLength(1);
    // The thrown model call was charged the estimate, so the month keeps the call.
    expect((await callHosted(hosted("refund-model", { "x-apotheke-autofill-limit": "1" }), { AI: scriptedAi([reply(VALID)]).ai })).status).toBe(429);

    const parse = scriptedAi([reply("nope"), reply("still nope")]);
    const failed = await callHosted(hosted("refund-parse", { "x-apotheke-autofill-limit": "1" }), { AI: parse.ai });
    expect(failed.status).toBe(503);
    expect(((await bodyOf(failed)).error as { code: string }).code).toBe("backend_error");
    expect(parse.calls).toHaveLength(2);
    // The model answered, so the month keeps the call.
    expect((await callHosted(hosted("refund-parse", { "x-apotheke-autofill-limit": "1" }), { AI: scriptedAi([reply(VALID)]).ai })).status).toBe(429);

    const repaired = scriptedAi([reply("nope"), reply(VALID)]);
    expect((await callHosted(hosted("refund-repair", { "x-apotheke-autofill-limit": "1" }), { AI: repaired.ai })).status).toBe(200);
    const second = await callHosted(hosted("refund-repair", { "x-apotheke-autofill-limit": "1" }), { AI: scriptedAi([reply(VALID)]).ai });
    expect(second.status).toBe(429);
  });

  it("keeps the count when the model answers once and the repair is refused (N-2299)", async () => {
    const id = "one-answer";
    // Room for one estimate: the first answer (21.37) leaves too little for the repair.
    const { ai, calls } = scriptedAi([reply("nope"), reply(VALID)]);
    const res = await callHosted(hosted(id), { AI: ai, AUTOFILL_DAILY_NEURON_BUDGET: "40" });
    expect(res.status).toBe(503);
    expect(await bodyOf(res)).toEqual({ error: { code: "backend_unavailable", message: "daily capacity is exhausted" } });
    expect(calls).toHaveLength(1);
    expect((await env.BUDGET.getByName(`installation:${id}`).reserveMonthly({ nowMs: midMonth, hard: 0, soft: 0 })).used).toBe(1);
  });

  it("refunds exactly one call when others are already counted (N-2296)", async () => {
    const id = "refund-one";
    const used = () => env.BUDGET.getByName(`installation:${id}`).reserveMonthly({ nowMs: midMonth, hard: 0, soft: 0 });
    expect((await callHosted(hosted(id), { AI: scriptedAi([reply(VALID)]).ai })).status).toBe(200);
    expect((await callHosted(hosted(id), { AI: scriptedAi([reply(VALID)]).ai })).status).toBe(200);
    const res = await callHosted(hosted(id), { AI: scriptedAi([reply(VALID)]).ai, AUTOFILL_DAILY_NEURON_BUDGET: "0" });
    expect(res.status).toBe(503);
    expect((await used()).used).toBe(2);
  });

  it("returns the original error and keeps the count when the refund fails (N-2296)", async () => {
    const id = "refund-throws";
    const real = env.BUDGET;
    const BUDGET = {
      getByName(name: string) {
        const stub = real.getByName(name);
        if (!name.startsWith("installation:")) return stub;
        return {
          reserveMonthly: (input: { nowMs: number; hard: number | null; soft: number }) => stub.reserveMonthly(input),
          refundMonthly: async () => {
            throw new Error("refund unavailable");
          },
        };
      },
    };
    const res = await callHosted(hosted(id), { AI: scriptedAi([reply(VALID)]).ai, AUTOFILL_DAILY_NEURON_BUDGET: "0", BUDGET });
    expect(res.status).toBe(503);
    expect(await bodyOf(res)).toEqual({ error: { code: "backend_unavailable", message: "daily capacity is exhausted" } });
    expect(res.headers.get("retry-after")).not.toBeNull();
    expect((await real.getByName(`installation:${id}`).reserveMonthly({ nowMs: midMonth, hard: 0, soft: 0 })).used).toBe(1);
  });

  it("refunds in the month of the reservation, not the current one (N-2296)", async () => {
    const id = "refund-boundary";
    const lastMs = Date.parse("2025-01-31T23:59:59.999Z");
    const res = await callHosted(hosted(id, {}, lastMs), { AI: scriptedAi([reply(VALID)]).ai, AUTOFILL_DAILY_NEURON_BUDGET: "0" });
    expect(res.status).toBe(503);
    expect((await env.BUDGET.getByName(`installation:${id}`).reserveMonthly({ nowMs: lastMs, hard: 0, soft: 0 })).used).toBe(0);
  });

  it("does not decrement twice or across a month boundary", async () => {
    const stub = env.BUDGET.getByName("installation:refund-unit");
    expect(await stub.reserveMonthly({ nowMs: midMonth, hard: 2, soft: 2 })).toEqual({ ok: true, used: 1, warn: false });
    expect(await stub.refundMonthly({ nowMs: midMonth })).toEqual({ ok: true, used: 0 });
    expect(await stub.refundMonthly({ nowMs: midMonth })).toEqual({ ok: true, used: 0 });
    expect(await stub.reserveMonthly({ nowMs: midMonth, hard: 2, soft: 2 })).toEqual({ ok: true, used: 1, warn: false });
    expect(await stub.refundMonthly({ nowMs: nextMonth })).toEqual({ ok: true, used: 0 });
    expect(await stub.reserveMonthly({ nowMs: midMonth, hard: 0, soft: 0 })).toEqual({ ok: false, used: 1, warn: true });
  });

  it("admits exactly the hard number of concurrent hosted calls (N-2192)", async () => {
    const { ai } = scriptedAi([reply(VALID)]);
    const id = "concurrent-five";
    const responses = await Promise.all(
      Array.from({ length: 10 }, () => callHosted(hosted(id, { "x-apotheke-autofill-limit": "5" }), { AI: ai })),
    );
    expect(responses.filter((res) => res.status === 200)).toHaveLength(5);
    expect(responses.filter((res) => res.status === 429)).toHaveLength(5);
    expect(await env.BUDGET.getByName(`installation:${id}`).reserveMonthly({ nowMs: midMonth, hard: 0, soft: 0 })).toEqual({
      ok: false,
      used: 5,
      warn: true,
    });
  });

  it("does not open an installation budget on a public request without the headers (N-2192)", async () => {
    const { ai } = scriptedAi([reply(VALID)]);
    const names: string[] = [];
    const real = env.BUDGET;
    const BUDGET = {
      getByName(name: string) {
        names.push(name);
        return real.getByName(name);
      },
    };
    expect((await call(suggestRequest(jpeg(), { now: midMonth }), { AI: ai, BUDGET })).status).toBe(200);
    expect(names.some((name) => name.startsWith("installation:"))).toBe(false);
    expect(names).toContain("global");
  });

  it("rejects a 64-character installation id with 400 (N-2192)", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const ok = "a".repeat(63);
    const tooLong = "a".repeat(64);
    expect((await callHosted(hosted(ok), { AI: ai })).status).toBe(200);
    const res = await callHosted(hosted(tooLong), { AI: ai });
    expect(res.status).toBe(400);
    expect(((await bodyOf(res)).error as { code: string }).code).toBe("bad_request");
    expect(calls).toHaveLength(1);
  });
});

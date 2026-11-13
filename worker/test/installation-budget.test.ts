import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { LIMIT_MESSAGE, WARNING_MESSAGE } from "../src/installation";
import { VALID, bodyOf, call, jpeg, reply, resetBudget, scriptedAi, suggestRequest } from "./helpers";

beforeEach(resetBudget);

const midMonth = Date.parse("2026-11-13T12:00:00.000Z");
const nextMonth = Date.parse("2026-12-01T00:00:00.000Z");

function hosted(id: string, headers: Record<string, string> = {}, now = midMonth): Request {
  return suggestRequest(jpeg(), { now, headers: { "x-apotheke-installation": id, ...headers } });
}

describe("per-installation monthly autofill budget", () => {
  it("leaves a request without an installation id exactly as before, limit headers or not", async () => {
    const { ai } = scriptedAi([reply(VALID)]);
    const plain = await call(suggestRequest(jpeg(), { now: midMonth }), { AI: ai });
    const ignored = await call(
      suggestRequest(jpeg(), { now: midMonth, headers: { "x-apotheke-autofill-limit": "0", "x-apotheke-autofill-soft": "0" } }),
      { AI: ai },
    );
    expect(plain.status).toBe(200);
    expect(ignored.status).toBe(200);
    const body = await bodyOf(plain);
    expect(body.warning).toBeUndefined();
    expect(await bodyOf(ignored)).toEqual(body);
    expect(await env.BUDGET.getByName("global").snapshot(midMonth)).toMatchObject({ requests: 2 });
  });

  it("refuses past the hard value with the plan message, without calling the model", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const id = "hard-limit";
    for (let i = 0; i < 2; i++) expect((await call(hosted(id, { "x-apotheke-autofill-limit": "2" }), { AI: ai })).status).toBe(200);
    const refused = await call(hosted(id, { "x-apotheke-autofill-limit": "2" }), { AI: ai });
    expect(refused.status).toBe(429);
    expect(await bodyOf(refused)).toEqual({ error: { code: "autofill_limit", message: LIMIT_MESSAGE } });
    expect(LIMIT_MESSAGE).toBe("Autofill limit reached for this month");
    expect(refused.headers.get("retry-after")).toBe(String((nextMonth - midMonth) / 1000));
    expect(calls).toHaveLength(2);
    // Another installation has its own count, and a new month starts from zero.
    expect((await call(hosted("other-one", { "x-apotheke-autofill-limit": "2" }), { AI: ai })).status).toBe(200);
    expect((await call(hosted(id, { "x-apotheke-autofill-limit": "2" }, nextMonth), { AI: ai })).status).toBe(200);
  });

  it("adds a warning field from the soft value (80% of hard by default)", async () => {
    const { ai } = scriptedAi([reply(VALID)]);
    const warnings: unknown[] = [];
    for (let i = 0; i < 5; i++) {
      const res = await call(hosted("soft-default", { "x-apotheke-autofill-limit": "5" }), { AI: ai });
      warnings.push((await bodyOf(res)).warning ?? null);
    }
    expect(warnings).toEqual([null, null, null, WARNING_MESSAGE, WARNING_MESSAGE]);
    const explicit = await call(hosted("soft-set", { "x-apotheke-autofill-limit": "5", "x-apotheke-autofill-soft": "1" }), { AI: ai });
    expect((await bodyOf(explicit)).warning).toBe(WARNING_MESSAGE);
  });

  it("only counts an installation without a limit header", async () => {
    const { ai } = scriptedAi([reply(VALID)]);
    for (let i = 0; i < 3; i++) {
      const res = await call(hosted("no-limit"), { AI: ai });
      expect(res.status).toBe(200);
      expect((await bodyOf(res)).warning).toBeUndefined();
    }
    expect(await env.BUDGET.getByName("installation:no-limit").reserveMonthly({ nowMs: midMonth, hard: 3, soft: 3 })).toEqual({ ok: false, used: 3, warn: true });
  });

  it("rejects a malformed installation id or limit with 400", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const bad: Record<string, string>[] = [{ "x-apotheke-installation": "Bad_Id" }, { "x-apotheke-installation": "ok", "x-apotheke-autofill-limit": "-1" }];
    for (const headers of bad) {
      const res = await call(suggestRequest(jpeg(), { now: midMonth, headers }), { AI: ai });
      expect(res.status).toBe(400);
      expect(((await bodyOf(res)).error as { code: string }).code).toBe("bad_request");
    }
    expect(calls).toHaveLength(0);
  });
});

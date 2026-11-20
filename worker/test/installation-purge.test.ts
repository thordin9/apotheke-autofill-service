import { env } from "cloudflare:workers";
import { beforeEach, describe, expect, it } from "vitest";

import { VALID, bodyOf, call, callHosted, jpeg, reply, resetBudget, scriptedAi, suggestRequest, TOKEN } from "./helpers";

beforeEach(resetBudget);

const midMonth = Date.parse("2026-11-13T12:00:00.000Z");

function purgeRequest(id: string, init: { method?: string; token?: string | null } = {}): Request {
  const headers = new Headers();
  if (init.token) headers.set("authorization", `Bearer ${init.token}`);
  return new Request(`https://autofill.example/v1/installations/${id}/budget`, { method: init.method ?? "DELETE", headers });
}

function used(id: string): Promise<number> {
  return env.BUDGET.getByName(`installation:${id}`)
    .reserveMonthly({ nowMs: midMonth, hard: 0, soft: 0 })
    .then((month) => month.used);
}

async function spend(id: string, calls: number): Promise<void> {
  const { ai } = scriptedAi([reply(VALID)]);
  for (let i = 0; i < calls; i++) {
    const req = suggestRequest(jpeg(), { now: midMonth, token: null, headers: { "x-apotheke-installation": id } });
    expect((await callHosted(req, { AI: ai })).status).toBe(200);
  }
}

describe("installation budget purge", () => {
  it("deletes one installation's budget and is idempotent", async () => {
    await spend("purge-me", 3);
    const first = await callHosted(purgeRequest("purge-me"));
    expect(first.status).toBe(200);
    expect(await bodyOf(first)).toEqual({ ok: true, installation: "purge-me", deleted: 1 });
    const again = await callHosted(purgeRequest("purge-me"));
    expect(again.status).toBe(200);
    expect(await bodyOf(again)).toEqual({ ok: true, installation: "purge-me", deleted: 0 });
    // A later call starts the month from zero.
    expect(await used("purge-me")).toBe(0);
  });

  it("answers deleted 0 for an unknown installation", async () => {
    const res = await callHosted(purgeRequest("never-seen"));
    expect(res.status).toBe(200);
    expect(await bodyOf(res)).toEqual({ ok: true, installation: "never-seen", deleted: 0 });
  });

  it("leaves other installations and the global budget untouched", async () => {
    await spend("purge-target", 1);
    await spend("purge-target-2", 2);
    await spend("other-tenant", 2);
    const before = await env.BUDGET.getByName("global").snapshot(midMonth);
    expect((await callHosted(purgeRequest("purge-target"))).status).toBe(200);
    expect(await used("purge-target-2")).toBe(2);
    expect(await used("other-tenant")).toBe(2);
    expect(await env.BUDGET.getByName("global").snapshot(midMonth)).toEqual(before);
  });

  it("refuses the primary installation and leaves its budget", async () => {
    await spend("primary", 1);
    const res = await callHosted(purgeRequest("primary"));
    expect(res.status).toBe(400);
    expect(((await bodyOf(res)).error as { code: string }).code).toBe("bad_request");
    expect(await used("primary")).toBe(1);
  });

  it("rejects a malformed installation id with 400", async () => {
    for (const id of ["Bad", "a--b", "-a", "a".repeat(64), "a%2Fb", "global%3A"]) {
      const res = await callHosted(purgeRequest(id));
      expect(res.status).toBe(400);
    }
  });

  it("allows only DELETE", async () => {
    for (const method of ["GET", "POST", "PUT"]) {
      const res = await callHosted(purgeRequest("purge-method", { method }));
      expect(res.status).toBe(405);
      expect(res.headers.get("allow")).toBe("DELETE");
    }
  });

  it("is not reachable on the public routes, with or without the bearer token", async () => {
    await spend("public-purge", 1);
    for (const token of [TOKEN, null]) {
      const res = await call(purgeRequest("public-purge", { token }));
      expect(res.status).toBe(404);
    }
    expect(await used("public-purge")).toBe(1);
  });
});

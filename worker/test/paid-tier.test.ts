import { createHash } from "node:crypto";
import { Buffer } from "node:buffer";
import { beforeEach, describe, expect, it } from "vitest";

import wranglerToml from "../wrangler.toml?raw";
import { bytesToBase64 } from "../src/base64";
import { readSuggestParts } from "../src/multipart";
import {
  VALID,
  bodyOf,
  call,
  jpeg,
  multipart,
  reply,
  resetBudget,
  scriptedAi,
  suggestRequest,
  TOKEN,
  unreadStream,
} from "./helpers";

beforeEach(resetBudget);

const evening = Date.parse("2026-10-19T23:00:00.000Z");

/**
 * sha256 of the top-level worker/wrangler.toml, before [env.paid]. Last changed
 * to add the autofill.services.getapotheke.eu custom domain route.
 */
const TOP_LEVEL_SHA256 = "6c40173d47a427db99f2400c64363b6c9e92bd69c4d349d92fef62777c072a88";

const FREE_BODY = 1048576;
const FREE_IMAGE = 983040;
const FREE_ADVERTISED = 524288;
const PAID_BODY = 2 * 1024 * 1024;
const PAID_IMAGE = PAID_BODY - 65536;
const PAID_ADVERTISED = 1048576;
const PAID_NEURONS = 30000;

const FREE_INFO_KEYS = [
  "name",
  "version",
  "capabilities",
  "backend",
  "backend_used",
  "max_image_bytes",
  "ready",
  "budget",
  "enforced_image_bytes",
  "tier",
];

function infoRequest(now?: number): Request {
  const headers = new Headers({ authorization: `Bearer ${TOKEN}` });
  if (now !== undefined) headers.set("x-autofill-now", String(now));
  return new Request("https://autofill.example/v1/info", { headers });
}

function paidEnv(extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    TIER: "paid",
    MAX_UPLOAD_BYTES: String(PAID_BODY),
    AUTOFILL_DAILY_NEURON_BUDGET: String(PAID_NEURONS),
    ...extra,
  };
}

async function infoBody(overrides: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
  const response = await call(infoRequest(evening), overrides);
  expect(response.status).toBe(200);
  return bodyOf(response);
}

function freeInfo(): Record<string, unknown> {
  return {
    name: "cloudflare-workers",
    version: "1",
    capabilities: ["name", "category", "tags", "description"],
    backend: "cloudflare-workers",
    backend_used: "cloudflare-workers",
    max_image_bytes: FREE_ADVERTISED,
    ready: true,
    budget: {
      neurons: { limit: 6000, used: 0, reserved: 0, remaining: 6000 },
      requests: { limit: null, used: 0, remaining: null },
      resets_at: "2026-10-20T00:00:00.000Z",
    },
    enforced_image_bytes: FREE_IMAGE,
    tier: "free",
  };
}

interface TomlTable {
  name: string;
  body: string;
}

function tomlTables(text: string): TomlTable[] {
  const tables: TomlTable[] = [];
  let current: TomlTable | null = null;
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    const header = /^\[\[(.+)\]\]$/.exec(trimmed) ?? /^\[(.+)\]$/.exec(trimmed);
    if (header && !trimmed.startsWith("#")) {
      if (current) tables.push(current);
      current = { name: header[1], body: "" };
      continue;
    }
    if (current) current.body += `${line}\n`;
  }
  if (current) tables.push(current);
  return tables;
}

function assignments(body: string): Record<string, string> {
  const values: Record<string, string> = {};
  for (const line of body.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const eq = trimmed.indexOf("=");
    if (eq < 0) continue;
    values[trimmed.slice(0, eq).trim()] = trimmed.slice(eq + 1).trim();
  }
  return values;
}

function tableBody(tables: TomlTable[], name: string): string {
  const found = tables.find((table) => table.name === name);
  expect(found, name).toBeDefined();
  return found?.body ?? "";
}

function dataUrlBytes(url: string): Uint8Array {
  const marker = "base64,";
  const at = url.indexOf(marker);
  expect(at).toBeGreaterThan(0);
  return new Uint8Array(Buffer.from(url.slice(at + marker.length), "base64"));
}

function sameBytes(left: Uint8Array, right: Uint8Array): boolean {
  return Buffer.from(left.buffer, left.byteOffset, left.byteLength).equals(
    Buffer.from(right.buffer, right.byteOffset, right.byteLength),
  );
}

describe("TIER flag", () => {
  it("keeps free info identical and appends enforced_image_bytes and tier", async () => {
    const body = await infoBody();
    expect(Object.keys(body)).toEqual(FREE_INFO_KEYS);
    expect(body).toEqual(freeInfo());
    expect(body).not.toHaveProperty("recommended_image_bytes");
  });

  it.each(["", "Paid", " paid", "premium", "payd", "PAID", "paid ", "free"])(
    "treats TIER %j as free",
    async (tier) => {
      const body = await infoBody({ TIER: tier });
      expect(Object.keys(body)).toEqual(FREE_INFO_KEYS);
      expect(body).toEqual(freeInfo());
    },
  );

  it("reports the paid resize target, enforced cap, and neuron budget", async () => {
    expect(PAID_BODY).toBe(2097152);
    expect(PAID_IMAGE).toBe(2031616);
    const body = await infoBody(paidEnv());
    expect(Object.keys(body)).toEqual(FREE_INFO_KEYS);
    expect(body.max_image_bytes).toBe(PAID_ADVERTISED);
    expect(body.enforced_image_bytes).toBe(PAID_IMAGE);
    expect(body.tier).toBe("paid");
    expect(body).not.toHaveProperty("recommended_image_bytes");
    expect(body.budget).toEqual({
      neurons: { limit: PAID_NEURONS, used: 0, reserved: 0, remaining: PAID_NEURONS },
      requests: { limit: null, used: 0, remaining: null },
      resets_at: "2026-10-20T00:00:00.000Z",
    });
    const previous = { ...body };
    delete previous.enforced_image_bytes;
    delete previous.tier;
    expect(previous.max_image_bytes).toBe(PAID_ADVERTISED);
  });
});

describe("enforced image cap", () => {
  it.each([
    ["free", FREE_IMAGE, FREE_BODY, {}],
    ["paid", PAID_IMAGE, PAID_BODY, paidEnv()],
  ] as const)(
    "accepts exactly enforced_image_bytes and rejects one byte over on %s",
    async (_label, imageCap, bodyCap, overrides) => {
      const atCap = jpeg("x".repeat(imageCap - 4));
      const over = jpeg("y".repeat(imageCap + 1 - 4));
      expect(atCap.byteLength).toBe(imageCap);
      expect(over.byteLength).toBe(imageCap + 1);
      expect(multipart(atCap).body.byteLength).toBeLessThanOrEqual(bodyCap);
      expect(multipart(over).body.byteLength).toBeLessThanOrEqual(bodyCap);

      const accepted = scriptedAi([reply(VALID)]);
      const ok = await call(suggestRequest(atCap), { AI: accepted.ai, ...overrides });
      expect(ok.status).toBe(200);
      expect(accepted.calls).toHaveLength(1);
      expect(accepted.calls[0].model).toBe("@cf/google/gemma-4-26b-a4b-it");
      expect(accepted.calls[0].input.chat_template_kwargs).toEqual({ enable_thinking: false });

      const rejected = scriptedAi([reply(VALID)]);
      const response = await call(suggestRequest(over), { AI: rejected.ai, ...overrides });
      expect(response.status).toBe(413);
      expect(await bodyOf(response)).toEqual({
        error: { code: "payload_too_large", message: `image exceeds ${imageCap} bytes` },
      });
      expect(rejected.calls).toHaveLength(0);
    },
  );
});

describe("whole-body 413 message", () => {
  it.each([
    ["free", FREE_BODY, {}],
    ["paid", PAID_BODY, paidEnv()],
  ] as const)("names the %s MAX_UPLOAD_BYTES", async (_label, cap, overrides) => {
    const tracked = unreadStream(new Uint8Array([0xff, 0xd8]));
    const request = new Request("https://autofill.example/v1/suggest", {
      method: "POST",
      headers: {
        authorization: `Bearer ${TOKEN}`,
        "content-type": "multipart/form-data; boundary=----autofilltest",
        "content-length": String(cap + 1),
      },
      body: tracked.stream,
      duplex: "half",
    } as RequestInit);
    const before = tracked.reads();
    const response = await call(request, overrides);
    expect(response.status).toBe(413);
    expect(await bodyOf(response)).toEqual({
      error: { code: "payload_too_large", message: `request body exceeds ${cap} bytes` },
    });
    expect(tracked.reads()).toBe(before);
  });
});

describe("neuron cap", () => {
  it("returns the same exhausted error on free and paid", async () => {
    const seen: Array<{ status: number; retry: string | null; body: unknown }> = [];
    for (const overrides of [{}, paidEnv()]) {
      await resetBudget();
      const { ai } = scriptedAi([reply(VALID, { neurons: 30 }), reply(VALID, { neurons: 30 })]);
      const env = { AI: ai, ...overrides, AUTOFILL_DAILY_NEURON_BUDGET: "30" };
      expect((await call(suggestRequest(jpeg(), { now: evening }), env)).status).toBe(200);
      const blocked = await call(suggestRequest(jpeg(), { now: evening }), env);
      seen.push({
        status: blocked.status,
        retry: blocked.headers.get("retry-after"),
        body: await bodyOf(blocked),
      });
    }
    expect(seen[0]).toEqual(seen[1]);
    expect(seen[0]).toEqual({
      status: 503,
      retry: "3600",
      body: { error: { code: "backend_unavailable", message: "daily capacity is exhausted" } },
    });
  });
});

describe("paid upload memory and original bytes", () => {
  it("keeps a near-cap paid image as one view plus one base64 string", async () => {
    const size = PAID_IMAGE - 1024;
    const image = jpeg("Q".repeat(size - 4));
    expect(image.byteLength).toBe(size);
    expect(image.byteLength).toBeGreaterThan(PAID_IMAGE - 2048);
    const part = multipart(image, { context: '{"locale":"en"}' });
    expect(part.body.byteLength).toBeLessThanOrEqual(PAID_BODY);
    expect(part.body.byteLength).toBeGreaterThan(size);

    const parsed = readSuggestParts(part.body, part.contentType, PAID_IMAGE);
    expect(parsed.image.buffer).toBe(part.body.buffer);
    expect(sameBytes(parsed.image, image)).toBe(true);

    const encoded = bytesToBase64(parsed.image);
    expect(encoded.length).toBe(Math.ceil(parsed.image.byteLength / 3) * 4);
    const latin1 = part.body.byteLength + encoded.length;
    const utf16 = part.body.byteLength + encoded.length * 2;
    expect(latin1).toBeLessThan(8 * 1024 * 1024);
    expect(utf16).toBeLessThan(16 * 1024 * 1024);
    expect(utf16).toBeLessThan(128 * 1024 * 1024);

    const { ai, calls } = scriptedAi([reply(VALID)]);
    const response = await call(suggestRequest(image, { context: '{"locale":"en"}' }), {
      AI: ai,
      ...paidEnv(),
    });
    expect(response.status).toBe(200);
    expect(calls).toHaveLength(1);
    const content = (calls[0].input.messages as Array<{ content: Array<Record<string, unknown>> }>)[0].content;
    const url = (content[1].image_url as { url: string }).url;
    expect(sameBytes(dataUrlBytes(url), image)).toBe(true);
    expect(url.length).toBeLessThan(encoded.length + 64);
  });

  it("sends the original image bytes on free and paid", async () => {
    const image = jpeg("CANARY-BYTES-\r\n--not-the-boundary");
    for (const overrides of [{}, paidEnv()]) {
      const { ai, calls } = scriptedAi([reply(VALID)]);
      const response = await call(suggestRequest(image), { AI: ai, ...overrides });
      expect(response.status).toBe(200);
      const content = (calls[0].input.messages as Array<{ content: Array<Record<string, unknown>> }>)[0].content;
      const url = (content[1].image_url as { url: string }).url;
      expect(dataUrlBytes(url)).toEqual(image);
      expect(calls[0].input.chat_template_kwargs).toEqual({ enable_thinking: false });
    }
  });
});

describe("auth on both tiers", () => {
  it.each([
    ["free", {}],
    ["paid", paidEnv()],
  ] as const)("returns 401 and 503 on %s", async (_label, overrides) => {
    const missing = await call(suggestRequest(jpeg(), { token: null }), overrides);
    expect(missing.status).toBe(401);
    expect(missing.headers.get("www-authenticate")).toBe("Bearer");
    expect(await bodyOf(missing)).toEqual({
      error: { code: "unauthorized", message: "missing or invalid bearer token" },
    });

    const wrong = await call(suggestRequest(jpeg(), { token: "wrong-token" }), overrides);
    expect(wrong.status).toBe(401);
    expect(await bodyOf(wrong)).toEqual({
      error: { code: "unauthorized", message: "missing or invalid bearer token" },
    });

    for (const token of [undefined, ""]) {
      const info = await call(infoRequest(), { ...overrides, AUTOFILL_TOKEN: token });
      expect(info.status).toBe(503);
      expect(await bodyOf(info)).toEqual({
        error: { code: "backend_unavailable", message: "service is not configured" },
      });
      const suggest = await call(suggestRequest(), { ...overrides, AUTOFILL_TOKEN: token });
      expect(suggest.status).toBe(503);
      expect(await bodyOf(suggest)).toEqual({
        error: { code: "backend_unavailable", message: "service is not configured" },
      });
    }
  });
});

describe("wrangler.toml paid environment", () => {
  it("leaves the top-level config unchanged and repeats every binding and var", () => {
    const marker = "\n[env.paid]\n";
    const at = wranglerToml.indexOf(marker);
    expect(at).toBeGreaterThan(0);
    expect(wranglerToml.indexOf(marker, at + 1)).toBe(-1);
    const topText = wranglerToml.slice(0, at + 1);
    expect(createHash("sha256").update(topText).digest("hex")).toBe(TOP_LEVEL_SHA256);
    expect(topText).not.toContain("TIER");
    expect(topText).not.toContain("cpu_ms");
    expect(topText).not.toContain("2097152");
    expect(topText).not.toContain("30000");

    const topTables = tomlTables(topText);
    const paidText = wranglerToml.slice(at + 1);
    const paidTables = tomlTables(paidText);
    const topVars = assignments(tableBody(topTables, "vars"));
    const paidVars = assignments(tableBody(paidTables, "env.paid.vars"));
    for (const key of Object.keys(topVars)) {
      expect(paidVars, key).toHaveProperty(key);
    }
    expect(paidVars.AUTOFILL_DAILY_REQUEST_CAP).toBe(topVars.AUTOFILL_DAILY_REQUEST_CAP);
    expect(paidVars.AUTOFILL_DAILY_NEURON_BUDGET).toBe('"30000"');
    expect(paidVars.MAX_UPLOAD_BYTES).toBe('"2097152"');
    expect(paidVars.TIER).toBe('"paid"');
    expect(Object.keys(paidVars).sort()).toEqual([...Object.keys(topVars), "TIER"].sort());

    const paidScalars = assignments(paidText.split("\n[")[0] ?? "");
    expect(paidScalars.name).toBe('"apotheke-autofill"');
    expect(assignments(tableBody(topTables, "ai"))).toEqual(assignments(tableBody(paidTables, "env.paid.ai")));
    expect(assignments(tableBody(topTables, "durable_objects.bindings"))).toEqual(
      assignments(tableBody(paidTables, "env.paid.durable_objects.bindings")),
    );
    expect(assignments(tableBody(topTables, "ratelimits"))).toEqual(
      assignments(tableBody(paidTables, "env.paid.ratelimits")),
    );
    const routes = (tables: TomlTable[], name: string) =>
      tables.filter((table) => table.name === name).map((table) => assignments(table.body));
    expect(routes(topTables, "routes")).toHaveLength(2);
    expect(routes(topTables, "routes")).toEqual(routes(paidTables, "env.paid.routes"));
    expect(assignments(tableBody(topTables, "migrations"))).toEqual(
      assignments(tableBody(paidTables, "env.paid.migrations")),
    );
    expect(assignments(tableBody(paidTables, "env.paid.limits")).cpu_ms).toBe("500");
    expect(topTables.some((table) => table.name === "limits")).toBe(false);
    expect(paidText).not.toContain("kv_namespaces");
    expect(paidText).not.toContain("account_id");
  });
});

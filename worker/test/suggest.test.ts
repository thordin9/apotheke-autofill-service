import { beforeEach, describe, expect, it } from "vitest";

import { buildPrompt, V3_CATEGORIES } from "../src/prompt";
import { clamp } from "../src/validate";
import {
  VALID,
  bodyOf,
  call,
  captureLogs,
  jpeg,
  reply,
  resetBudget,
  scriptedAi,
  suggestRequest,
} from "./helpers";

beforeEach(resetBudget);

const UPSTREAM = "UPSTREAM-SECRET-DO-NOT-LEAK";

describe("suggest", () => {
  it("returns a schema-valid suggestion and sends Gemma 4 a data URI with thinking off", async () => {
    const { ai, calls } = scriptedAi([reply(VALID, { neurons: 21.37 })]);
    const logs = captureLogs();
    const response = await call(suggestRequest(jpeg("IMGCANARY"), { context: JSON.stringify({ categories: ["Tools", "Kitchen"] }) }), {
      AI: ai,
    });
    logs.restore();
    expect(response.status).toBe(200);
    const body = await bodyOf(response);
    expect(body.model_used).toBe("@cf/google/gemma-4-26b-a4b-it");
    expect(body.brand).toBe("Bosch");
    expect(body.category).toEqual({ value: "Tools", confidence: 0.82 });
    expect(calls).toHaveLength(1);
    expect(calls[0].model).toBe("@cf/google/gemma-4-26b-a4b-it");
    const input = calls[0].input;
    expect(input.chat_template_kwargs).toEqual({ enable_thinking: false });
    expect(input.response_format).toMatchObject({ type: "json_schema" });
    const content = (input.messages as Array<{ content: Array<Record<string, unknown>> }>)[0].content;
    const image = content[1].image_url as { url: string };
    expect(image.url.startsWith("data:image/jpeg;base64,")).toBe(true);
    const schema = (input.response_format as { json_schema: { schema: { properties: { category: { enum: string[] } } } } })
      .json_schema.schema;
    expect(schema.properties.category.enum).toEqual(["Tools", "Kitchen"]);
    const logged = logs.lines.join("\n");
    expect(logged).not.toContain("IMGCANARY");
    expect(logged).not.toContain("Bosch");
    expect(logged).not.toContain("test-token");
    expect(logged).toContain("\"neurons\":21.37");
  });

  it("repairs an invalid reply once", async () => {
    const { ai, calls } = scriptedAi([reply("not json", null), reply(VALID, { neurons: 10 })]);
    const response = await call(suggestRequest(), { AI: ai });
    expect(response.status).toBe(200);
    expect((await bodyOf(response)).name).toEqual({ value: "Cordless drill", confidence: 0.82 });
    expect(calls).toHaveLength(2);
    expect(calls[0].input).toMatchObject({ chat_template_kwargs: { enable_thinking: false } });
    expect(calls[1].input).toMatchObject({ chat_template_kwargs: { enable_thinking: false } });
    const firstUrl = imageUrl(calls[0].input);
    const secondUrl = imageUrl(calls[1].input);
    expect(secondUrl).toBe(firstUrl);
  });

  it("returns backend_error when the repair also fails", async () => {
    const { ai, calls } = scriptedAi([reply("nope", null), reply("still nope", null)]);
    const response = await call(suggestRequest(), { AI: ai });
    expect(response.status).toBe(503);
    expect(await bodyOf(response)).toEqual({
      error: { code: "backend_error", message: "model output was not a JSON object" },
    });
    expect(calls).toHaveLength(2);
  });

  it("accepts length-truncated JSON after a local repair", async () => {
    const truncated =
      '{"name":"Hammer","category":"Tools","tags":["hammer"],"description":"A claw hammer with a wooden handle","confidence":0.66,"brand":"Stan';
    const { ai, calls } = scriptedAi([reply(truncated, { neurons: 12 }, "length")]);
    const response = await call(suggestRequest(), { AI: ai });
    expect(response.status).toBe(200);
    const body = await bodyOf(response);
    expect(body.name).toEqual({ value: "Hammer", confidence: 0.66 });
    expect(body.brand).toBe("");
    expect(calls).toHaveLength(1);
  });

  it("asks the model again when a truncated reply cannot be repaired", async () => {
    const { ai, calls } = scriptedAi([reply("{\"name\":", null, "length"), reply(VALID, { neurons: 5 })]);
    const response = await call(suggestRequest(), { AI: ai });
    expect(response.status).toBe(200);
    expect(calls).toHaveLength(2);
  });

  it("maps Workers AI 3036 and 3040 to 503 and 3006 to 413 without upstream text", async () => {
    const secret = `${UPSTREAM} allocation of neurons`;
    const quota = scriptedAi([Object.assign(new Error(secret), { errors: [{ code: 3036, message: secret }] })]);
    const quotaResponse = await call(suggestRequest(), { AI: quota.ai });
    expect(quotaResponse.status).toBe(503);
    expect(quotaResponse.headers.get("retry-after")).toMatch(/^\d+$/);
    const quotaBody = JSON.stringify(await quotaResponse.json());
    expect(quotaBody).not.toContain(UPSTREAM);
    expect(quotaBody).toContain("daily capacity is exhausted");

    const again = await call(suggestRequest(), { AI: quota.ai });
    expect(again.status).toBe(503);
    expect(quota.calls).toHaveLength(1);

    await resetBudget();
    const capacity = scriptedAi([
      Object.assign(new Error(secret), { errors: [{ code: 3040, message: secret }] }),
      reply(VALID),
    ]);
    const capacityResponse = await call(suggestRequest(), { AI: capacity.ai });
    expect(capacityResponse.status).toBe(503);
    expect(capacityResponse.headers.get("retry-after")).toMatch(/^\d+$/);
    expect(JSON.stringify(await capacityResponse.json())).not.toContain(UPSTREAM);
    const recovered = await call(suggestRequest(), { AI: capacity.ai });
    expect(recovered.status).toBe(200);
    expect(capacity.calls).toHaveLength(2);

    await resetBudget();
    const tooBig = scriptedAi([
      Object.assign(new Error(secret), { code: 3006 }),
      reply(VALID),
    ]);
    const tooBigResponse = await call(suggestRequest(), { AI: tooBig.ai, MAX_UPLOAD_BYTES: "3145728" });
    expect(tooBigResponse.status).toBe(413);
    const tooBigBody = await bodyOf(tooBigResponse);
    expect(tooBigBody).toEqual({
      error: { code: "payload_too_large", message: "image exceeds 3145728 bytes" },
    });
    expect(JSON.stringify(tooBigBody)).not.toContain(UPSTREAM);
    const after = await call(suggestRequest(), { AI: tooBig.ai });
    expect(after.status).toBe(200);
  });

  it("strips controls and bidi from categories and rejects a lone surrogate", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const context = JSON.stringify({ categories: ["Tools\u0000\n", "\u202eKitchen"] });
    const response = await call(suggestRequest(jpeg(), { context }), { AI: ai });
    expect(response.status).toBe(200);
    const schema = (calls[0].input.response_format as { json_schema: { schema: { properties: { category: { enum: string[] } } } } })
      .json_schema.schema;
    expect(schema.properties.category.enum).toEqual(["Tools", "Kitchen"]);

    const lone = JSON.stringify({ categories: ["Tools", "A\uD800B"] });
    const rejected = await call(suggestRequest(jpeg(), { context: lone }), { AI: ai });
    expect(rejected.status).toBe(400);
    expect((await bodyOf(rejected)).error).toEqual({
      code: "bad_request",
      message: "'context.categories' contains an unpaired surrogate",
    });
    expect(calls).toHaveLength(1);
  });

  it("uses the v3 category list when the client sends none", () => {
    const prompt = buildPrompt({ categories: [], tags: [], locale: "" });
    expect(prompt).toContain(`category (exactly one of: ${V3_CATEGORIES.join(", ")}),`);
    expect(prompt.endsWith("Write the values in the language of locale 'en'.")).toBe(true);
    expect(prompt.endsWith("\n")).toBe(false);
  });

  it("matches the Python clamp fixtures", () => {
    const scored = clamp(
      {
        name: "Cordless drill",
        category: "tools",
        tags: ["bosch", "cordless"],
        description: "A cordless drill sitting on a bench",
        confidence: 0.82,
        brand: "  Bosch  ",
        model_number: "GSB 180",
        serial_number: "n/a",
      },
      { categories: ["Tools", "Kitchen"], tags: ["bosch", "red"] },
    );
    expect(scored).toEqual({
      name: { value: "Cordless drill", confidence: 0.82 },
      category: { value: "Tools", confidence: 0.82 },
      description: { value: "A cordless drill sitting on a bench", confidence: 0.82 },
      tags: [{ value: "bosch" }, { value: "cordless" }],
      brand: "Bosch",
      model_number: "GSB 180",
      serial_number: "",
    });
    const cut = clamp(
      {
        name: "Kettle",
        category: "Kitchen",
        tags: ["kettle"],
        description: "A black electric kettle with a handle",
        confidence: 0.7,
        brand: "\u202eTefal\ud83d\x00",
        model_number: "x".repeat(129),
        serial_number: "  ",
      },
      { categories: ["Tools", "Kitchen"], tags: [] },
    );
    expect(cut.brand).toBe("Tefal");
    expect(cut.model_number).toBe("x".repeat(128));
    expect(cut.serial_number).toBe("");
  });
});

function imageUrl(input: Record<string, unknown>): string {
  const messages = input.messages as Array<{ content: Array<{ image_url?: { url: string } }> }>;
  return messages[0].content[1].image_url?.url ?? "";
}

import { env } from "cloudflare:workers";
import { createExecutionContext, waitOnExecutionContext } from "cloudflare:test";
import { describe, expect, it } from "vitest";

import worker from "../src/index";
import { isAiStub } from "../src/ai";
import { suggestRequest } from "./helpers";

describe("AI stub guard", () => {
  it("refuses the real AI binding while tests require a stub", async () => {
    expect(isAiStub(env.AI)).toBe(false);
    const ctx = createExecutionContext();
    const response = await worker.fetch(suggestRequest(), env, ctx);
    await waitOnExecutionContext(ctx);
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      error: { code: "backend_unavailable", message: "refused to call Workers AI" },
    });
  });
});

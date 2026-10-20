import { describe, expect, it } from "vitest";

import { logRequest } from "../src/log";

describe("request log", () => {
  it("writes Date.now deltas and does not invent a CPU time", () => {
    const lines: string[] = [];
    const originalLog = console.log;
    const originalNow = Date.now;
    let clock = 5_000;
    console.log = (line?: unknown) => {
      lines.push(String(line));
    };
    Date.now = () => {
      clock += 25;
      return clock;
    };
    try {
      logRequest(200, 5_000, {
        base64Ms: 1.25,
        parseMs: 0.5,
        validateMs: 0.25,
        neurons: 30,
        model: "@cf/google/gemma-4-26b-a4b-it",
        aiCalls: 1,
      });
    } finally {
      console.log = originalLog;
      Date.now = originalNow;
    }
    const line = JSON.parse(lines[0]) as Record<string, unknown>;
    expect(line).toEqual({
      level: "info",
      status: 200,
      base64_ms: 1.25,
      parse_ms: 0.5,
      validate_ms: 0.25,
      total_ms: 25,
      neurons: 30,
      model: "@cf/google/gemma-4-26b-a4b-it",
      ai_calls: 1,
    });
    expect(line).not.toHaveProperty("cpu_ms");
    expect(JSON.stringify(line)).not.toContain("test-token");
  });
});

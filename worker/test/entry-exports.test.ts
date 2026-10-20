import { describe, expect, it } from "vitest";

import * as entry from "../src/index";

describe("entry exports", () => {
  it("exports only a function or class besides default", () => {
    const names = Object.keys(entry).filter((name) => name !== "default");
    expect(names).toContain("Budget");
    for (const name of names) {
      const value = entry[name as keyof typeof entry];
      expect(typeof value, name).toBe("function");
    }
  });
});

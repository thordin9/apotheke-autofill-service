import { Buffer } from "node:buffer";
import { describe, expect, it } from "vitest";

import { base64Method, bytesToBase64 } from "../src/base64";

function jpegOf(size: number): Uint8Array {
  const bytes = new Uint8Array(size);
  bytes[0] = 0xff;
  bytes[1] = 0xd8;
  bytes[2] = 0xff;
  for (let i = 3; i < size; i++) bytes[i] = i % 251;
  return bytes;
}

function median(samples: number[]): number {
  const sorted = [...samples].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)] ?? 0;
}

describe("base64", () => {
  it("encodes 1 MB and 2 MB with the native method and without btoa", () => {
    const original = globalThis.btoa;
    let usedBtoa = false;
    globalThis.btoa = () => {
      usedBtoa = true;
      return "";
    };
    try {
      expect(base64Method()).toBe("toBase64");
      for (const size of [1024 * 1024, 2 * 1024 * 1024]) {
        const bytes = jpegOf(size);
        bytesToBase64(bytes.subarray(0, 4096));
        const encode: number[] = [];
        let encoded = "";
        for (let i = 0; i < 5; i++) {
          const start = performance.now();
          encoded = bytesToBase64(bytes);
          encode.push(performance.now() - start);
        }
        expect(encoded).toBe(Buffer.from(bytes).toString("base64"));
        const jsonStart = performance.now();
        JSON.stringify({ type: "image_url", image_url: { url: `data:image/jpeg;base64,${encoded}` } });
        const jsonMs = performance.now() - jsonStart;
        const bufferStart = performance.now();
        Buffer.from(bytes).toString("base64");
        const bufferMs = performance.now() - bufferStart;
        console.log(
          `[base64-bench] method=toBase64 bytes=${size} median_ms=${median(encode).toFixed(3)} json_ms=${jsonMs.toFixed(3)} buffer_ms=${bufferMs.toFixed(3)}`,
        );
        expect(median(encode)).toBeLessThan(1000);
      }
    } finally {
      globalThis.btoa = original;
    }
    expect(usedBtoa).toBe(false);
  }, 30_000);
});

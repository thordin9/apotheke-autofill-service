import { beforeEach, describe, expect, it } from "vitest";

import { readLimitedBody } from "../src/http";
import { maxImageBytes, MULTIPART_HEADROOM, readSuggestParts } from "../src/multipart";
import { VALID, bodyOf, call, jpeg, png, reply, resetBudget, scriptedAi, suggestRequest, webp } from "./helpers";

beforeEach(resetBudget);

describe("upload limits and input errors", () => {
  it("advertises the image cap as the body cap minus 64 KiB", () => {
    expect(MULTIPART_HEADROOM).toBe(65536);
    expect(maxImageBytes(1048576)).toBe(983040);
    expect(maxImageBytes(65536)).toBe(0);
    expect(maxImageBytes(40)).toBe(0);
  });

  it("returns 413 from Content-Length before calling the model", async () => {
    let calls = 0;
    const { ai } = scriptedAi([
      () => {
        calls += 1;
        return reply(VALID);
      },
    ]);
    const request = suggestRequest(jpeg("x".repeat(80)));
    const response = await call(request, { AI: ai, MAX_UPLOAD_BYTES: "40" });
    expect(response.status).toBe(413);
    const body = await bodyOf(response);
    expect(body).toEqual({ error: { code: "payload_too_large", message: "image exceeds 40 bytes" } });
    expect(calls).toBe(0);
  });

  it("returns 413 for a chunked body with no Content-Length", async () => {
    const part = new TextEncoder().encode("x".repeat(300));
    let pulled = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        const size = 50;
        const start = pulled;
        if (start >= part.byteLength) {
          controller.close();
          return;
        }
        pulled += size;
        controller.enqueue(part.subarray(start, start + size));
      },
    });
    const request = new Request("https://autofill.example/v1/suggest", {
      method: "POST",
      headers: {
        authorization: "Bearer test-token",
        "content-type": "multipart/form-data; boundary=----autofilltest",
      },
      body: stream,
      duplex: "half",
    } as RequestInit);
    expect(request.headers.get("content-length")).toBeNull();
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const response = await call(request, { AI: ai, MAX_UPLOAD_BYTES: "128" });
    expect(response.status).toBe(413);
    expect((await bodyOf(response)).error).toEqual({
      code: "payload_too_large",
      message: "image exceeds 128 bytes",
    });
    expect(calls).toHaveLength(0);
  });

  it("stops reading once the stream passes the cap", async () => {
    let reads = 0;
    const stream = new ReadableStream<Uint8Array>({
      pull(controller) {
        reads += 1;
        controller.enqueue(new Uint8Array(40));
      },
    });
    const request = new Request("https://autofill.example/v1/suggest", {
      method: "POST",
      body: stream,
      duplex: "half",
    } as RequestInit);
    await expect(readLimitedBody(request, 100)).rejects.toMatchObject({ status: 413 });
    expect(reads).toBeLessThan(6);
  });

  it("rejects a non-image from the first bytes before a closing boundary", async () => {
    const boundary = "----autofilltest";
    const body =
      `--${boundary}\r\n` +
      'Content-Disposition: form-data; name="image"; filename="a.gif"\r\n' +
      "Content-Type: image/gif\r\n\r\n" +
      "GIF89aXXXXYYYY";
    const response = await call(
      new Request("https://autofill.example/v1/suggest", {
        method: "POST",
        headers: {
          authorization: "Bearer test-token",
          "content-type": `multipart/form-data; boundary=${boundary}`,
        },
        body,
      }),
    );
    expect(response.status).toBe(415);
    expect(await bodyOf(response)).toEqual({
      error: { code: "unsupported_media_type", message: "image must be JPEG, PNG or WebP" },
    });
  });

  it("returns 413 for an image part over the image cap while the body is under the body cap", async () => {
    const bodyCap = 80_000;
    const imageCap = bodyCap - 65536;
    const image = jpeg("x".repeat(imageCap));
    expect(image.byteLength).toBeGreaterThan(imageCap);
    const request = suggestRequest(image);
    expect(Number(request.headers.get("content-length"))).toBeLessThanOrEqual(bodyCap);
    const response = await call(request, { MAX_UPLOAD_BYTES: String(bodyCap) });
    expect(response.status).toBe(413);
    expect(await bodyOf(response)).toEqual({
      error: { code: "payload_too_large", message: `image exceeds ${imageCap} bytes` },
    });
  });

  it("keeps image bytes identical for curl, PHP curl, and httpx bodies", () => {
    const image = jpeg("CANARY-BYTES-\r\n--not-the-boundary");
    const context = '{"locale":"en"}';
    const shapes = [
      clientBody("--------------------------curlform", image, context, "image/jpeg"),
      clientBody("phpcurlboundary", image, context, "application/octet-stream"),
      clientBody("httpxboundary", image, context, "image/jpeg", true),
    ];
    for (const part of shapes) {
      const parsed = readSuggestParts(part.body, part.contentType, 983040);
      expect(Array.from(parsed.image)).toEqual(Array.from(image));
      expect(parsed.context).toBe(context);
      expect(parsed.mime).toBe("image/jpeg");
    }
  });

  it("returns 415 from magic bytes, not the part content type", async () => {
    const gif = new TextEncoder().encode("GIF89a-not-an-image");
    const response = await call(suggestRequest(gif));
    expect(response.status).toBe(415);
    expect(await bodyOf(response)).toEqual({
      error: { code: "unsupported_media_type", message: "image must be JPEG, PNG or WebP" },
    });
  });

  it("accepts PNG and WebP magic", async () => {
    const { ai } = scriptedAi([reply(VALID), reply(VALID)]);
    const pngResponse = await call(suggestRequest(png()), { AI: ai });
    expect(pngResponse.status).toBe(200);
    const webpResponse = await call(suggestRequest(webp()), { AI: ai });
    expect(webpResponse.status).toBe(200);
  });

  it("returns 400 for a missing image, an empty image, and a non-multipart body", async () => {
    const empty = await call(
      new Request("https://autofill.example/v1/suggest", {
        method: "POST",
        headers: {
          authorization: "Bearer test-token",
          "content-type": "multipart/form-data; boundary=----autofilltest",
        },
        body: "------autofilltest--\r\n",
      }),
    );
    expect(empty.status).toBe(400);
    expect((await bodyOf(empty)).error).toMatchObject({ code: "bad_request" });

    const raw = await call(
      new Request("https://autofill.example/v1/suggest", {
        method: "POST",
        headers: { authorization: "Bearer test-token", "content-type": "image/jpeg" },
        body: jpeg(),
      }),
    );
    expect(raw.status).toBe(400);
    expect(await bodyOf(raw)).toEqual({ error: { code: "bad_request", message: "expected multipart/form-data" } });
  });

  it("returns 400 for bad context JSON", async () => {
    const response = await call(suggestRequest(jpeg(), { context: "{nope" }));
    expect(response.status).toBe(400);
    expect(await bodyOf(response)).toEqual({ error: { code: "bad_request", message: "'context' is not valid JSON" } });
  });
});

function clientBody(
  boundary: string,
  image: Uint8Array,
  context: string,
  partType: string,
  quoteBoundary = false,
): { body: Uint8Array; contentType: string } {
  const enc = new TextEncoder();
  const head = enc.encode(
    `--${boundary}\r\nContent-Disposition: form-data; name="image"; filename="label.jpg"\r\nContent-Type: ${partType}\r\n\r\n`,
  );
  const mid = enc.encode(`\r\n--${boundary}\r\nContent-Disposition: form-data; name="context"\r\n\r\n${context}`);
  const tail = enc.encode(`\r\n--${boundary}--\r\n`);
  const body = new Uint8Array(head.length + image.length + mid.length + tail.length);
  body.set(head, 0);
  body.set(image, head.length);
  body.set(mid, head.length + image.length);
  body.set(tail, head.length + image.length + mid.length);
  const token = quoteBoundary ? `"${boundary}"` : boundary;
  return { body, contentType: `multipart/form-data; boundary=${token}` };
}

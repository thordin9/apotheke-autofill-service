import { beforeEach, describe, expect, it } from "vitest";

import { readLimitedBody } from "../src/http";
import { maxImageBytes, MULTIPART_HEADROOM, readSuggestParts } from "../src/multipart";
import {
  VALID,
  bodyOf,
  call,
  jpeg,
  multipart,
  png,
  reply,
  resetBudget,
  scriptedAi,
  suggestRequest,
  unreadStream,
  webp,
} from "./helpers";

beforeEach(resetBudget);

describe("upload limits and input errors", () => {
  it("caps an image part at the body cap minus 64 KiB", () => {
    expect(MULTIPART_HEADROOM).toBe(65536);
    expect(maxImageBytes(1048576)).toBe(983040);
    expect(maxImageBytes(65536)).toBe(0);
    expect(maxImageBytes(40)).toBe(0);
  });

  it("accepts a body between 524288 and 1048576", async () => {
    const image = jpeg("x".repeat(600_000));
    const part = multipart(image);
    const request = suggestRequest(image);
    expect(image.byteLength).toBeGreaterThan(524288);
    expect(image.byteLength).toBeLessThanOrEqual(983040);
    expect(part.body.byteLength).toBeGreaterThan(524288);
    expect(part.body.byteLength).toBeLessThanOrEqual(1048576);
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const response = await call(request, { AI: ai });
    expect(response.status).toBe(200);
    expect(calls).toHaveLength(1);
  });

  it("accepts an image part of 983040 bytes and rejects 983041 with no AI call", async () => {
    const atCap = jpeg("x".repeat(983040 - 4));
    const over = jpeg("x".repeat(983041 - 4));
    expect(atCap.byteLength).toBe(983040);
    expect(over.byteLength).toBe(983041);
    const atPart = multipart(atCap);
    const overPart = multipart(over);
    expect(atPart.body.byteLength).toBeLessThanOrEqual(1048576);
    expect(overPart.body.byteLength).toBeLessThanOrEqual(1048576);

    const accepted = scriptedAi([reply(VALID)]);
    const ok = await call(suggestRequest(atCap), { AI: accepted.ai });
    expect(ok.status).toBe(200);
    expect(accepted.calls).toHaveLength(1);

    const rejected = scriptedAi([reply(VALID)]);
    const response = await call(suggestRequest(over), { AI: rejected.ai });
    expect(response.status).toBe(413);
    expect(await bodyOf(response)).toEqual({
      error: { code: "payload_too_large", message: "image exceeds 983040 bytes" },
    });
    expect(rejected.calls).toHaveLength(0);
  });

  it("returns 413 for a body of 1048577 bytes", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const response = await call(
      new Request("https://autofill.example/v1/suggest", {
        method: "POST",
        headers: {
          authorization: "Bearer test-token",
          "content-type": "multipart/form-data; boundary=----autofilltest",
        },
        body: new Uint8Array(1048577),
      }),
      { AI: ai },
    );
    expect(response.status).toBe(413);
    expect(await bodyOf(response)).toEqual({
      error: { code: "payload_too_large", message: "image exceeds 1048576 bytes" },
    });
    expect(calls).toHaveLength(0);
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
    const { result: response, buffered } = await countingArrayBuffer(() =>
      call(request, { AI: ai, MAX_UPLOAD_BYTES: "128" }),
    );
    expect(response.status).toBe(413);
    expect((await bodyOf(response)).error).toEqual({
      code: "payload_too_large",
      message: "image exceeds 128 bytes",
    });
    expect(calls).toHaveLength(0);
    expect(buffered).toBe(0);
    expect(pulled).toBeGreaterThan(0);
    expect(pulled).toBeLessThan(part.byteLength);
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

  it("accepts a Content-Length exactly at the cap by buffering the body", async () => {
    const part = multipart(jpeg());
    const cap = part.body.byteLength + MULTIPART_HEADROOM;
    const body = new Uint8Array(cap);
    body.set(part.body);
    const request = new Request("https://autofill.example/v1/suggest", {
      method: "POST",
      headers: {
        authorization: "Bearer test-token",
        "content-type": part.contentType,
        "content-length": String(cap),
      },
      body,
    });
    expect(request.headers.get("content-length")).toBe(String(cap));
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const { result: response, buffered } = await countingArrayBuffer(() =>
      call(request, { AI: ai, MAX_UPLOAD_BYTES: String(cap) }),
    );
    expect(response.status).toBe(200);
    expect(calls).toHaveLength(1);
    expect(buffered).toBe(1);
  });

  it("returns 413 for a Content-Length one byte over the cap without reading the body", async () => {
    const part = multipart(jpeg());
    const cap = part.body.byteLength + MULTIPART_HEADROOM;
    const tracked = unreadStream(part.body);
    const request = new Request("https://autofill.example/v1/suggest", {
      method: "POST",
      headers: {
        authorization: "Bearer test-token",
        "content-type": part.contentType,
        "content-length": String(cap + 1),
      },
      body: tracked.stream,
      duplex: "half",
    } as RequestInit);
    expect(request.headers.get("content-length")).toBe(String(cap + 1));
    const before = tracked.reads();
    const { result: response, buffered } = await countingArrayBuffer(() =>
      call(request, { MAX_UPLOAD_BYTES: String(cap) }),
    );
    expect(response.status).toBe(413);
    expect(await bodyOf(response)).toEqual({
      error: { code: "payload_too_large", message: `image exceeds ${cap} bytes` },
    });
    expect(tracked.reads()).toBe(before);
    expect(buffered).toBe(0);
  });

  it("returns 400 for a non-numeric Content-Length without buffering", async () => {
    await expectInvalidLength("nope");
  });

  it("returns 400 for a negative Content-Length without buffering", async () => {
    await expectInvalidLength("-1");
  });

  it("returns 400 for a duplicate or comma-joined Content-Length without buffering", async () => {
    await expectInvalidLength("8, 8");
    const part = multipart(jpeg());
    const headers = new Headers({
      authorization: "Bearer test-token",
      "content-type": part.contentType,
    });
    headers.append("content-length", "8");
    headers.append("content-length", "9");
    expect(headers.get("content-length")).toBe("8, 9");
    const tracked = unreadStream(part.body);
    const request = new Request("https://autofill.example/v1/suggest", {
      method: "POST",
      headers,
      body: tracked.stream,
      duplex: "half",
    } as RequestInit);
    const before = tracked.reads();
    const { result: response, buffered } = await countingArrayBuffer(() => call(request));
    expect(response.status).toBe(400);
    expect(await bodyOf(response)).toEqual({
      error: { code: "bad_request", message: "invalid Content-Length" },
    });
    expect(buffered).toBe(0);
    expect(tracked.reads()).toBe(before);
  });

  it("returns 400 when Content-Length is 0 and a body is present", async () => {
    const part = multipart(jpeg());
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const response = await call(
      new Request("https://autofill.example/v1/suggest", {
        method: "POST",
        headers: {
          authorization: "Bearer test-token",
          "content-type": part.contentType,
          "content-length": "0",
        },
        body: part.body,
      }),
      { AI: ai },
    );
    expect(response.status).toBe(400);
    expect(await bodyOf(response)).toEqual({
      error: { code: "bad_request", message: "invalid Content-Length" },
    });
    expect(calls).toHaveLength(0);
  }, 2000);

  it("rejects a body longer than its declared Content-Length", async () => {
    const part = multipart(jpeg());
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const response = await call(
      new Request("https://autofill.example/v1/suggest", {
        method: "POST",
        headers: {
          authorization: "Bearer test-token",
          "content-type": part.contentType,
          "content-length": String(part.body.byteLength - 1),
        },
        body: part.body,
      }),
      { AI: ai },
    );
    expect(response.status).toBeGreaterThanOrEqual(400);
    expect(response.status).toBeLessThan(500);
    expect(calls).toHaveLength(0);

    const direct = new Request("https://autofill.example/v1/suggest", {
      method: "POST",
      headers: { "content-length": "10" },
      body: new Uint8Array(80),
    });
    let returned: Uint8Array | undefined;
    try {
      returned = await readLimitedBody(direct, 100);
    } catch (err) {
      expect(err).toMatchObject({ status: expect.any(Number) });
      const status = (err as { status: number }).status;
      expect(status).toBeGreaterThanOrEqual(400);
      expect(status).toBeLessThan(500);
    }
    if (returned) {
      expect(returned.byteLength).toBeLessThanOrEqual(10);
    }
  });

  it("rejects a body over the cap when Content-Length is under the cap", async () => {
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const response = await call(
      new Request("https://autofill.example/v1/suggest", {
        method: "POST",
        headers: {
          authorization: "Bearer test-token",
          "content-type": "multipart/form-data; boundary=----autofilltest",
          "content-length": "10",
        },
        body: new Uint8Array(150),
      }),
      { AI: ai, MAX_UPLOAD_BYTES: "100" },
    );
    expect(response.status).toBe(413);
    expect(await bodyOf(response)).toEqual({
      error: { code: "payload_too_large", message: "image exceeds 100 bytes" },
    });
    expect(calls).toHaveLength(0);
  });

  it("returns 400 when the body is shorter than Content-Length", async () => {
    const part = multipart(jpeg());
    const { ai, calls } = scriptedAi([reply(VALID)]);
    const response = await call(
      new Request("https://autofill.example/v1/suggest", {
        method: "POST",
        headers: {
          authorization: "Bearer test-token",
          "content-type": part.contentType,
          "content-length": String(part.body.byteLength + 25),
        },
        body: part.body,
      }),
      { AI: ai },
    );
    expect(response.status).toBe(400);
    expect(await bodyOf(response)).toEqual({
      error: { code: "bad_request", message: "invalid Content-Length" },
    });
    expect(calls).toHaveLength(0);
  }, 2000);
});

async function countingArrayBuffer<T>(run: () => Promise<T>): Promise<{ result: T; buffered: number }> {
  let buffered = 0;
  const original = Request.prototype.arrayBuffer;
  Request.prototype.arrayBuffer = async function (this: Request) {
    buffered += 1;
    return original.call(this);
  };
  try {
    return { result: await run(), buffered };
  } finally {
    Request.prototype.arrayBuffer = original;
  }
}

async function expectInvalidLength(contentLength: string): Promise<void> {
  const part = multipart(jpeg());
  const tracked = unreadStream(part.body);
  const request = new Request("https://autofill.example/v1/suggest", {
    method: "POST",
    headers: {
      authorization: "Bearer test-token",
      "content-type": part.contentType,
      "content-length": contentLength,
    },
    body: tracked.stream,
    duplex: "half",
  } as RequestInit);
  expect(request.headers.get("content-length")).toBe(contentLength);
  const before = tracked.reads();
  const { result: response, buffered } = await countingArrayBuffer(() => call(request));
  expect(response.status).toBe(400);
  expect(await bodyOf(response)).toEqual({
    error: { code: "bad_request", message: "invalid Content-Length" },
  });
  expect(buffered).toBe(0);
  expect(tracked.reads()).toBe(before);
}

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

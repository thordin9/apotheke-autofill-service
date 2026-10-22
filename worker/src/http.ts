export class HttpError extends Error {
  readonly status: number;
  readonly code: string;
  readonly headers: Record<string, string> | undefined;

  constructor(
    status: number,
    code: string,
    message: string,
    headers?: Record<string, string>,
  ) {
    super(message);
    this.status = status;
    this.code = code;
    this.headers = headers;
  }
}

export function json(body: unknown, status = 200, headers?: Record<string, string>): Response {
  const h = new Headers(headers);
  h.set("content-type", "application/json");
  return new Response(JSON.stringify(body), { status, headers: h });
}

export function errorResponse(status: number, code: string, message: string, headers?: Record<string, string>): Response {
  return json({ error: { code, message } }, status, headers);
}

export function httpErrorResponse(err: HttpError): Response {
  return errorResponse(err.status, err.code, err.message, err.headers);
}

function bodyTooLarge(maxBytes: number): HttpError {
  return new HttpError(413, "payload_too_large", `request body exceeds ${maxBytes} bytes`);
}

/** Whole-body cap.
 * A single numeric Content-Length within the cap is buffered, then checked
 * against the bytes actually read. A length over the cap is rejected before
 * that read. A missing length (chunked) is counted as it is read and aborted
 * over the cap. A non-numeric, negative, or combined length is rejected first.
 */
export async function readLimitedBody(request: Request, maxBytes: number): Promise<Uint8Array> {
  const declared = request.headers.get("content-length");
  if (declared !== null) {
    if (!/^\d+$/.test(declared)) {
      throw new HttpError(400, "bad_request", "invalid Content-Length");
    }
    const declaredBytes = Number(declared);
    if (declaredBytes > maxBytes) {
      throw bodyTooLarge(maxBytes);
    }
    let buffered: ArrayBuffer;
    try {
      buffered = await request.arrayBuffer();
    } catch (err) {
      if (err instanceof HttpError) throw err;
      throw new HttpError(400, "bad_request", "expected multipart/form-data");
    }
    if (buffered.byteLength > maxBytes) {
      throw bodyTooLarge(maxBytes);
    }
    if (buffered.byteLength !== declaredBytes) {
      throw new HttpError(400, "bad_request", "invalid Content-Length");
    }
    return new Uint8Array(buffered);
  }
  const body = request.body;
  if (!body) return new Uint8Array();
  const reader = body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const step = await reader.read();
      if (step.done) break;
      const value = step.value;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel();
        throw bodyTooLarge(maxBytes);
      }
      chunks.push(value);
    }
  } catch (err) {
    if (err instanceof HttpError) throw err;
    throw new HttpError(400, "bad_request", "expected multipart/form-data");
  }
  const out = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    out.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return out;
}

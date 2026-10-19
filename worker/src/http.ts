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

/** Whole-body cap. Content-Length is checked before the stream is read.
 * A body with no length (chunked) is counted as it is read and aborted over the cap.
 */
export async function readLimitedBody(request: Request, maxBytes: number): Promise<Uint8Array> {
  const declared = request.headers.get("content-length");
  if (declared !== null) {
    if (!/^\d+$/.test(declared)) {
      throw new HttpError(400, "bad_request", "invalid Content-Length");
    }
    if (Number(declared) > maxBytes) {
      throw new HttpError(413, "payload_too_large", `image exceeds ${maxBytes} bytes`);
    }
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
        throw new HttpError(413, "payload_too_large", `image exceeds ${maxBytes} bytes`);
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

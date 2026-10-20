import { HttpError } from "./http";
import { sniffMime } from "./images";

/** Room for multipart headers between the body cap and the image. */
export const MULTIPART_HEADROOM = 65536;

export function maxImageBytes(bodyCap: number): number {
  return Math.max(0, bodyCap - MULTIPART_HEADROOM);
}

export interface SuggestParts {
  image: Uint8Array;
  mime: "image/jpeg" | "image/png" | "image/webp";
  context?: string;
}

function findBytes(hay: Uint8Array, needle: Uint8Array, from: number): number {
  if (needle.length === 0 || from > hay.length - needle.length) return -1;
  const first = needle[0];
  let i = from;
  const last = hay.length - needle.length;
  while (i <= last) {
    const at = hay.indexOf(first, i);
    if (at < 0 || at > last) return -1;
    let match = true;
    for (let j = 1; j < needle.length; j++) {
      if (hay[at + j] !== needle[j]) {
        match = false;
        break;
      }
    }
    if (match) return at;
    i = at + 1;
  }
  return -1;
}

function boundaryToken(contentType: string): string | null {
  const match = /boundary=(?:"([^"]+)"|([^;\s]+))/i.exec(contentType);
  const token = match?.[1] ?? match?.[2];
  return token ? token.trim() : null;
}

function partName(headers: string): string | null {
  const match = /content-disposition:[^\n]*\bname="([^"]*)"/i.exec(headers);
  if (match) return match[1];
  const bare = /content-disposition:[^\n]*\bname=([^;\s]+)/i.exec(headers);
  return bare ? bare[1] : null;
}

/**
 * Read the image and optional context. Magic bytes are checked on the first
 * bytes of the image part, before the rest of that part is accepted.
 */
export function readSuggestParts(bytes: Uint8Array, contentType: string | null, imageCap: number): SuggestParts {
  if (!contentType || !contentType.toLowerCase().includes("multipart/form-data")) {
    throw new HttpError(400, "bad_request", "expected multipart/form-data");
  }
  const token = boundaryToken(contentType);
  if (!token) throw new HttpError(400, "bad_request", "expected multipart/form-data");
  const encoder = new TextEncoder();
  const opener = encoder.encode(`--${token}`);
  const closer = encoder.encode(`\r\n--${token}`);
  if (findBytes(bytes, opener, 0) !== 0) {
    throw new HttpError(400, "bad_request", "expected multipart/form-data");
  }

  let cursor = opener.length;
  let image: Uint8Array | null = null;
  let mime: SuggestParts["mime"] | null = null;
  let context: string | undefined;

  while (cursor < bytes.length) {
    if (bytes[cursor] === 45 && bytes[cursor + 1] === 45) break;
    if (bytes[cursor] === 13 && bytes[cursor + 1] === 10) cursor += 2;
    const headerEnd = findBytes(bytes, encoder.encode("\r\n\r\n"), cursor);
    if (headerEnd < 0) throw new HttpError(400, "bad_request", "expected multipart/form-data");
    const headers = new TextDecoder().decode(bytes.subarray(cursor, headerEnd));
    const bodyAt = headerEnd + 4;
    const name = partName(headers);
    if (name === "image") {
      const preview = bytes.subarray(bodyAt, Math.min(bytes.length, bodyAt + 12));
      if (preview.length >= 12 && sniffMime(preview) === null) {
        throw new HttpError(415, "unsupported_media_type", "image must be JPEG, PNG or WebP");
      }
      const end = findBytes(bytes, closer, bodyAt);
      if (end < 0) throw new HttpError(400, "bad_request", "expected multipart/form-data");
      const length = end - bodyAt;
      if (length === 0) throw new HttpError(400, "bad_request", "'image' is empty");
      if (length > imageCap) {
        throw new HttpError(413, "payload_too_large", `image exceeds ${imageCap} bytes`);
      }
      const slice = bytes.subarray(bodyAt, end);
      const sniffed = sniffMime(slice);
      if (!sniffed) throw new HttpError(415, "unsupported_media_type", "image must be JPEG, PNG or WebP");
      image = slice;
      mime = sniffed;
      cursor = end + closer.length;
      continue;
    }
    const end = findBytes(bytes, closer, bodyAt);
    if (end < 0) throw new HttpError(400, "bad_request", "expected multipart/form-data");
    if (name === "context") context = new TextDecoder().decode(bytes.subarray(bodyAt, end));
    cursor = end + closer.length;
  }

  if (!image || !mime) throw new HttpError(400, "bad_request", "missing 'image' file part");
  return { image, mime, context };
}

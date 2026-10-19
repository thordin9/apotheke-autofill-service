import { Buffer } from "node:buffer";

type BytesWithBase64 = Uint8Array & { toBase64?: () => string };

/** Native toBase64 when workerd provides it; otherwise node:buffer.
 * No btoa and no fromCharCode loop: those blow the 10 ms Free CPU budget.
 */
export function bytesToBase64(bytes: Uint8Array): string {
  const native = (Uint8Array.prototype as BytesWithBase64).toBase64;
  if (typeof native === "function") return native.call(bytes);
  return Buffer.from(bytes).toString("base64");
}

export function base64Method(): "toBase64" | "buffer" {
  return typeof (Uint8Array.prototype as BytesWithBase64).toBase64 === "function"
    ? "toBase64"
    : "buffer";
}

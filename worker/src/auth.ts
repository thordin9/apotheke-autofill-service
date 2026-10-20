/** Constant-time bearer check. SHA-256 both sides, then timingSafeEqual.
 * timingSafeEqual throws when the lengths differ, so the digests are the inputs.
 * The server token's digest is cached for this isolate and replaced when the
 * secret value changes. The presented token is hashed every time. Neither
 * value is logged.
 */

interface TimingSubtle {
  timingSafeEqual(a: BufferSource, b: BufferSource): boolean;
}

let cachedToken: string | null = null;
let cachedDigest: ArrayBuffer | null = null;

async function digestServerToken(expected: string): Promise<ArrayBuffer> {
  if (cachedToken === expected && cachedDigest !== null) return cachedDigest;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(expected));
  cachedToken = expected;
  cachedDigest = digest;
  return digest;
}

export async function tokensMatch(given: string, expected: string): Promise<boolean> {
  const [left, right] = await Promise.all([
    crypto.subtle.digest("SHA-256", new TextEncoder().encode(given)),
    digestServerToken(expected),
  ]);
  return (crypto.subtle as unknown as TimingSubtle).timingSafeEqual(left, right);
}

export function bearerToken(header: string): string {
  const schemeEnd = header.indexOf(" ");
  if (schemeEnd === -1) return "";
  const scheme = header.slice(0, schemeEnd);
  if (scheme.toLowerCase() !== "bearer") return "";
  return header.slice(schemeEnd + 1).trim();
}

/** Constant-time bearer check. SHA-256 both sides, then timingSafeEqual.
 * timingSafeEqual throws when the lengths differ, so the digests are the inputs.
 */

interface TimingSubtle {
  timingSafeEqual(a: BufferSource, b: BufferSource): boolean;
}

export async function tokensMatch(given: string, expected: string): Promise<boolean> {
  const enc = new TextEncoder();
  const [left, right] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(given)),
    crypto.subtle.digest("SHA-256", enc.encode(expected)),
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

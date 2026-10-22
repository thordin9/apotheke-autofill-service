export type ServiceTier = "free" | "paid";

/**
 * Paid mode is the exact string "paid". Empty, differently cased, padded,
 * and unknown values stay on the free path. One comparison, no trimming.
 */
export function resolveTier(raw: string | undefined): ServiceTier {
  if (raw === "paid") return "paid";
  return "free";
}

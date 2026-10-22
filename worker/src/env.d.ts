/** AUTOFILL_TOKEN is a secret, so `wrangler types` does not list it.
 * TIER is set only on [env.paid], so the top-level generated Env omits it.
 */
interface Env {
  AUTOFILL_TOKEN: string;
  TIER?: string;
}

declare namespace Cloudflare {
  interface Env {
    AUTOFILL_TOKEN: string;
    TIER?: string;
  }
}

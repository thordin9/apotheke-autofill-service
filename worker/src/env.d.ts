/** AUTOFILL_TOKEN is a secret, so `wrangler types` does not list it. */
interface Env {
  AUTOFILL_TOKEN: string;
}

declare namespace Cloudflare {
  interface Env {
    AUTOFILL_TOKEN: string;
  }
}

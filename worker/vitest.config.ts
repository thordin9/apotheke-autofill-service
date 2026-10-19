import { cloudflareTest } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [
    cloudflareTest({
      wrangler: { configPath: "./wrangler.jsonc" },
      // The AI binding is remote by default and would spend Neurons. Tests
      // inject a stub and refuse the real binding; keep the session local.
      remoteBindings: false,
      miniflare: {
        // Test-only. Production wrangler.jsonc does not set this, so a real
        // deploy calls env.AI. Tests must pass an AI object marked as a stub;
        // the handler refuses the real binding while this var is "1".
        bindings: {
          AUTOFILL_TOKEN: "test-token",
          AUTOFILL_REQUIRE_AI_STUB: "1",
        },
      },
    }),
  ],
});

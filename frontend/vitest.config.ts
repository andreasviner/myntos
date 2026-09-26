import { defineConfig } from 'vitest/config';

/**
 * Unit tests for `src/lib/`, and deliberately nothing else.
 *
 * The pages are `.astro` files whose scripts run against a real document and a
 * real API; testing those means a browser, and a browser harness that is
 * half-trusted is worse than none. What *is* worth testing here is the small
 * amount of logic the whole site depends on being right: which company a page
 * is about, and the refusal to ask for or display any other one. That code
 * touches nothing but `localStorage` and `fetch`, both of which a test can hand
 * it.
 *
 * No `astro.config.mjs`, no Cloudflare adapter, no environment: the modules
 * under test import nothing from Astro, so loading its config would only make
 * the tests slower and able to fail for reasons that have nothing to do with
 * them. `window` is stubbed per test instead of pulling in jsdom.
 */
export default defineConfig({
  test: {
    include: ['src/lib/**/*.test.ts'],
    // Each test file re-imports the modules it needs so that the "decided once
    // per page load" state in lib/tenant.ts starts empty, the way it does in a
    // browser that has just loaded a page.
    restoreMocks: true,
  },
});

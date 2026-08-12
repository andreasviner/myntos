// @ts-check
import cloudflare from '@astrojs/cloudflare';
import { defineConfig } from 'astro/config';

// Static-first: pages prerender at build time and deploy to Cloudflare as
// static assets. Opt individual pages into SSR later with
// `export const prerender = false` — the Cloudflare adapter handles them.
export default defineConfig({
  output: 'static',
  adapter: cloudflare({
    // Cloudflare cannot run sharp at runtime; optimize images at build time.
    imageService: 'compile',
  }),
});

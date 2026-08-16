# Myntos frontend

Astro + TypeScript, managed with [pnpm](https://pnpm.io), deployed to
Cloudflare Workers.

```bash
pnpm install
pnpm dev       # http://localhost:4321
pnpm check     # type-check (.astro + .ts)
pnpm build     # production build → dist/
pnpm run deploy  # build + wrangler deploy (requires Cloudflare login)
```

Run these from this `frontend/` directory — there is no package.json at the
repo root. Note `pnpm run deploy`, not `pnpm deploy`: pnpm reserves `deploy`
as a built-in workspace command and will not fall through to the script.

The site is static-first; pages that need server rendering opt in with
`export const prerender = false` (handled by the `@astrojs/cloudflare`
adapter, v14+). Deployment config lives in [wrangler.jsonc](wrangler.jsonc);
the build emits a resolved config at `dist/client/wrangler.json`, which the
`deploy` script points wrangler at (the path moves into `dist/server/` if SSR
routes are added — adjust the script then).

**Every page must keep the legal disclaimer visible** — it renders in the
footer via `src/layouts/BaseLayout.astro`. Do not remove it.

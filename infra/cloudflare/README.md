# Cloudflare deployment

Decided July 2026 (verified against Cloudflare docs; see
[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) for the reasoning and the
Norwegian storage-location rules that shaped it).

| Component | Where | Notes |
| --- | --- | --- |
| Frontend (Astro) | **Workers + static assets** | Config in `frontend/wrangler.jsonc`; deploy with `pnpm run deploy` from `frontend/`. Pages is maintenance-mode — don't use it. |
| Backend (FastAPI) | **Cloudflare Containers**, `jurisdiction: "eu"` | GA since 2026-04, Workers Paid plan ($5/mo base). Build from `backend/Dockerfile`. Stateless: ephemeral disk, instances sleep. Python Workers rejected (beta, no TCP sockets → no Postgres drivers). |
| PostgreSQL | Managed provider, **EEA region** | e.g. Supabase Stockholm (`eu-north-1`), Neon Frankfurt, or EEA-owned Aiven. Connect directly with asyncpg. Skip Hyperdrive for the container (JS-Workers-only; default query caching is unsafe for ledger reads). |
| Bilag PDFs | **R2, `jurisdiction=eu`** | `wrangler r2 bucket create <name> --jurisdiction eu`. Jurisdiction is **immutable after creation** and uses the dedicated S3 endpoint `https://<ACCOUNT_ID>.eu.r2.cloudflarestorage.com`. Location *hints* are not a compliance mechanism. |

## Cost expectations

Containers bill memory+disk while awake (even at zero CPU) and sleep on
inactivity; an always-on standard-1 FastAPI instance lands around $25–30/mo
beyond included allotments. Egress NA/EU $0.025/GB with 1 TB included.

## Portability guarantee

The backend is a plain Docker image — if a customer or regulator demands
different hosting (e.g. a Norway-only storage order after a compliance
breach), redeploy it unchanged to any EEA/Norwegian Docker host and keep
Cloudflare as CDN/proxy. Never store state in the container.

## Secrets

Cloudflare secret bindings / environment injection only. The Maskinporten
private JWK and database credentials never enter the repo or the image.

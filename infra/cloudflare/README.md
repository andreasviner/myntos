# Cloudflare

Cloudflare hosts the **frontend and the document bucket**. It does not host the
backend.

| Component | Where | Notes |
| --- | --- | --- |
| Frontend (Astro) | **Workers + static assets** | Config in `frontend/wrangler.jsonc`, custom domain `myntos.no`. Deploy with `pnpm run deploy` from `frontend/`, or `./update.sh --frontend-only`. Pages is maintenance-mode; don't use it. |
| Bilag PDFs | **R2, `jurisdiction=eu`** | `wrangler r2 bucket create <name> --jurisdiction eu`. Jurisdiction is **immutable after creation** and uses the dedicated S3 endpoint `https://<ACCOUNT_ID>.eu.r2.cloudflarestorage.com`. Location *hints* are not a compliance mechanism. |

## The backend used to live here

It was a Cloudflare Container behind a Worker, with configuration injected as
secret bindings through a `FORWARDED_ENV` allow-list in `backend/worker.js`.
That is gone: the backend now runs on a machine the team operates, under
systemd with Caddy terminating TLS for `api.myntos.no`. `backend/Dockerfile`,
`backend/worker.js` and `backend/wrangler.jsonc` were deleted with it.

This section exists because the removed setup had a failure mode worth
remembering. Configuration reached the container only if its name appeared in
`FORWARDED_ENV`, and a name missing from that list produced no error anywhere:
the API simply behaved as though the setting had never been set. It cost a
silent regression in bilag extraction (`LOYND_KEY`) and, later, an ID-porten
integration that reported itself as "not set up" while every local check
passed. If the backend is ever containerised again, an allow-list that fails
open like that should not come back with it.

The backend keeps no state, so moving it is still cheap: everything durable is
in Postgres and R2.

## Cost expectations

Only Workers and R2 now. Egress NA/EU $0.025/GB with 1 TB included. The
container line item (roughly $7-27/month depending on instance type) is gone.

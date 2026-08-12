# Security Policy

Myntos handles accounting data and credentials for Norwegian government
APIs. We take every report seriously.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Use GitHub's private vulnerability reporting ("Report a vulnerability" under
the repository's *Security* tab). We aim to acknowledge reports within
72 hours and to ship a fix or mitigation within 90 days, with credit to the
reporter unless you prefer otherwise.

## Scope

In scope:

- The backend API (`backend/`), including auth, storage and integration code
- The frontend (`frontend/`)
- CI/CD and supply-chain issues in this repository

Out of scope:

- Skatteetaten, Altinn and Maskinporten themselves (report to the respective
  service owners)
- Vulnerabilities requiring a compromised developer machine

## Handling of secrets

- No credentials, tokens or private keys are ever committed. `.gitignore`
  excludes `.env` and `secrets/`, and gitleaks runs in pre-commit.
- Maskinporten private keys must live outside the repo (or in the gitignored
  `secrets/` directory) and be referenced by path via `.env`.
- If you find a leaked secret in the history: report it privately (above) so
  it can be rotated in Samarbeidsportalen before public disclosure.

## Baseline measures in this repository

- Locked dependencies (`uv.lock`, `pnpm-lock.yaml`) with Dependabot updates
- gitleaks secret scanning in pre-commit
- Security headers on all API responses; API docs disabled in production
- Least-privilege Maskinporten scopes; tokens cached in memory only

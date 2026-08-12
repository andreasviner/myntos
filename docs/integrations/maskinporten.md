# Maskinporten

Machine-to-machine auth for Norwegian government APIs. Verified against
official sources 2026-07-18. The code contract is
`myntos.auth.maskinporten.client.TokenProvider`; the concrete client is
wired up by the deployment owner.

> **Scope of what Maskinporten buys you (2026):** retrieval and information
> APIs only. Validation and **submission** of MVA-melding and skattemelding
> require **ID-porten** (a person). Don't design headless submission — see
> [ARCHITECTURE.md](../ARCHITECTURE.md#the-two-auth-planes-this-is-the-part-everyone-gets-wrong).

## Endpoints (verified live)

| | Test | Production |
| --- | --- | --- |
| Well-known | `https://test.maskinporten.no/.well-known/oauth-authorization-server` | `https://maskinporten.no/.well-known/oauth-authorization-server` |
| Issuer / `aud` | `https://test.maskinporten.no/` | `https://maskinporten.no/` |
| Token endpoint | `https://test.maskinporten.no/token` | `https://maskinporten.no/token` |

Fetch issuer/token endpoint from the well-known document at runtime (Digdir's
recommendation) rather than hardcoding. `ver2.maskinporten.no` is dead
(since 2024-06-10) — distrust old docs that mention it.

## The JWT grant (RFC 7523)

POST `application/x-www-form-urlencoded` to the token endpoint with
`grant_type=urn:ietf:params:oauth:grant-type:jwt-bearer` and
`assertion=<signed JWT>`:

| Claim / header | Value | Sharp edge |
| --- | --- | --- |
| `aud` | The issuer string | **Must match exactly, trailing slash included, single value** — else `invalid_grant` |
| `iss` | Your `client_id` | |
| `scope` | Whitespace-separated scopes | |
| `iat`/`exp` | UTC | `exp − iat ≤ 120 s`; `iat` checked against Maskinporten's clock with ~10 s tolerance — server clock skew is a real failure mode |
| `jti` | UUID per request | Grants are **single-use**: never retry with the same assertion, regenerate |
| header `kid` **or** `x5c` | Pre-registered JWK id, or full virksomhetssertifikat chain | |
| Signing | RS256/RS384/RS512 | |

Response: `access_token` (self-contained JWT, **non-revocable**),
`expires_in`, `scope`. Treat the token as opaque; cache in memory keyed by
`(scopes, consumer_org)` until `expires_in − 30 s` — actual lifetimes vary
per scope (some 10 min, Skatteetaten's skattemelding scope issues 8 h tokens).

## Client registration & keys

- Register in Digdir self-service: `sjolvbetjening.samarbeid.digdir.no`
  (prod) / `sjolvbetjening.test.samarbeid.digdir.no` (test, self-signup with
  org-domain email). Client: `integration_type` Maskinporten,
  `token_endpoint_auth_method` `private_key_jwt`, grant type jwt-bearer.

### Scopes to put on the Maskinporten client

The scope picker is filtered by API provider — select **Skatteetaten** and
**Digitaliseringsdirektoratet/Altinn**, not the hundreds of unrelated ones.

| Scope | Why |
| --- | --- |
| `altinn:instances.read` | Read Altinn 3 app instances (skattemelding, MVA, brreg årsregnskap) |
| `altinn:instances.write` | Create instances and upload data |
| `skatteetaten:formueinntekt/skattemelding` | Fetch/validate skattemelding + næringsspesifikasjon |
| `skatteetaten:formueinntekt/skattemelding/eiendom` | Optional — property valuation data |

**Do not** add: `altinn:serviceowner/*` (service owners only — fails
delegation checks for end-user systems), KRR scopes (contact register,
irrelevant), or `skatteetaten:mvameldingvalidering` /
`skatteetaten:mvameldinginnsending` — those are **ID-porten** scopes and
belong on a separate ID-porten client, since MVA validation and submission
require a logged-in person.

The Altinn scopes are available immediately. Skatteetaten's scopes can be
*added* to the client in both environments, but they stay inert — the portal
marks them **"Tilgang mangler"** — until Skatteetaten grants your
organization access. **This applies to the test environment too**, so
skattemelding work is gated on the application (ekstern-Jira form + accepting
the bruksvilkår), not just production. Leaving ungranted scopes registered is
harmless; just don't request them in the JWT grant, since Maskinporten
rejects the whole token request if any requested scope is unauthorized.

Useful consequence: `altinn:instances.read` / `.write` alone are enough for
the **Brønnøysundregistrene årsregnskap** filing, so that integration is not
blocked by Skatteetaten onboarding.

Naming: include the environment in the display name (e.g.
`Myntos (test)`), because test and prod are separate registrations and
you will have both. Keep the description specific — Digdir and the API
providers read it when reviewing access requests.
- **Default: per-installation self-generated RSA JWK** (`kid` header) —
  Digdir explicitly recommends this over spreading virksomhetssertifikat.
  Registered keys **expire after max 1 year** — build a rotation runbook or
  production silently breaks on the anniversary.
- Optionally support `x5c` with a Buypass/Commfides virksomhetssertifikat for
  enterprises that have one. Suppliers must never hold customers'
  certificates.
- One Maskinporten client per deployment; never copy a client's key across
  installations. Prefer separate clients per API over one client with many
  scopes.

## Altinn token exchange

For Altinn 3 apps (skattemelding, MVA) the Maskinporten/ID-porten token is
not the final credential — exchange it:
`GET {altinn_platform}/authentication/api/v1/exchange/{maskinporten|id-porten}`
(test `platform.tt02.altinn.no`, prod `platform.altinn.no`). The returned
Altinn JWT is what the app APIs accept. End-user systems use scopes
`altinn:instances.read` / `altinn:instances.write` (the
`altinn:serviceowner/*` family is for service owners — requesting the wrong
one fails delegation checks).

## Acting for clients (regnskapsfører)

- **Today (classic):** customer delegates the API scope to the supplier in
  Altinn; the supplier adds `"consumer_org": "<customer orgnr>"` to the JWT
  grant; the token gains `consumer`/`supplier`/`delegation_source` claims.
  Store each client company's orgnr for this.
- **New model (Altinn 3 systembruker):** vendor registers its system in the
  Altinn systemregister, customers approve a system-user request, tokens are
  requested with RAR `authorization_details` (`urn:altinn:systemuser`).
  Access packages (tilgangspakker) replace Altinn roles through 2026
  (skattemelding 2026-02-01, MVA 2026-04-15, Altinn 2 shutdown June 2026).

## Implementation notes (Python)

No maintained Python library exists — implement a thin module with
`PyJWT` (already a dependency, `pyjwt[crypto]`) + `httpx` (~50 lines).
References: Digdir's `felleslosninger/jwt-grant-generator` (Java, canonical
grant construction), `ks-no/fiks-maskinporten` (token-caching design),
`torleifg/maskinporten-scripts` (Python example).

Key handling: load the private JWK from `MASKINPORTEN_JWK_PATH` (or an
injected secret env var); never from the repo; never log tokens or keys.

## Test vs prod

Fully separate universes: separate client registrations, separate test
certificates, synthetic organizations/persons from **Tenor Testdatasøk** in
TT02. A working test setup proves nothing about prod, and prod scope access
requires manual approval by Skatteetaten.

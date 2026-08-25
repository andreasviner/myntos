# Maskinporten

Machine-to-machine auth for Norwegian government APIs. Verified against
official sources 2026-07-18. The code contract is
`myntos.auth.maskinporten.client.TokenProvider`, and
`JwtGrantTokenProvider` in the same module implements it — what the
deployment owner supplies is the client registration and the key, not code.

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

Two things this fails on, both verified 2026-08-24:

- **`?test=true` is required on a test platform.** Without it the ID-porten
  exchange answers **403** and says nothing about a missing parameter.
  `myntos.integrations.altinn.tokens` derives it from the platform host, so it
  cannot be forgotten when environments change.
- **The ID-porten client needs an Altinn scope of its own.** The exchange is
  refused **403** for a token carrying only `openid` — which is what an
  ID-porten client registered with no Altinn scope issues: `scope: "openid"` and
  **no `aud` claim at all**, so there is nothing for Altinn to authorize.

  The scope is **`altinn:enduser`**, registered on the ID-porten client in
  Samarbeidsportalen and then requested in `IDPORTEN_SCOPES`. **Confirmed
  2026-08-24**: adding it turned the exchange from 403 into a token, with nothing
  else changed. It comes from Altinn's Altinn-2-era
  [ID-porten tutorial](https://altinn.github.io/docs/api/rest/kom-i-gang/tutorial-postman/id-porten-token/),
  which was reason to doubt the *name* until it was tried; it survived.

  A separate report ([Altinn/altinn-authentication#7](https://github.com/Altinn/altinn-authentication/issues/7))
  says a test person must have logged into `tt02.altinn.no` once before their
  token can be exchanged. **Tried, and it changed nothing** (2026-08-24) - the
  missing scope was the whole cause. Recorded so nobody spends a second
  afternoon on it.

  Only the *person* plane is affected: the Maskinporten exchange works without
  any of this, because those tokens carry `altinn:instances.*`.

## Representing a taxpayer: `digdir:skattemelding`

**Verified working 2026-08-24**, and the single most useful thing in this
document: a real skattemelding was fetched for AS `314067630` (income year 2025)
by a TestID login as `08866995401`, its daglig leder. That is the product's own
shape - a person logged in as themselves, reading a client company's melding.

The recipe, all three parts required:

1. `digdir:skattemelding` on the ID-porten client. Digdir brokers it against
   Altinn's rights register: "lese og levere skattemelding for alle
   organisasjoner som jeg har rettigheter for i Altinn".
2. `skatteetaten:formueinntekt/skattemelding` in the *same* token. This one is
   access to the API; the other is permission to act for somebody. Dropping it
   turns every answer into a **403 with an empty body**, which is the signature
   of a missing API scope and is worth recognising on sight.
3. Real Altinn rights over the company. Being daglig leder in Enhetsregisteret
   is enough - no delegation has to be set up by hand for a role holder.

Two things this does *not* do.

**It does not rescue the machine plane.** A Maskinporten token carrying both
scopes still answers **400 `SME-EKSTERN-005`**, "Det er ikke mulig aa sjekke om
paalogget bruker har noedvendige rettigheter til aa representere skattepliktig".
Read it literally: there is no paalogget bruker in a machine token, so there are
no rights to check. `consumer_org` does not help either (tried). Retrieval for a
named taxpayer wants a person, or a delegation this organisation does not yet
hold.

**It does not reach a person's own melding.** A fodselsnummer is not an
organisation anybody holds Altinn rights for, so that path rests on
`skatteetaten:formueinntekt/skattemelding` alone.

> **Expiry date on this mechanism.** It works today because daglig leder is an
> Altinn *role*, and Altinn roles count in access checks only until
> **2026-12-01**, after which access moves to tilgangspakker. The scope should
> survive; what a role holder automatically gets will not. Re-verify before
> then.

## Applying for a scope Skatteetaten has not granted

Adding a scope in Samarbeidsportalen only *declares* it. The portal then shows
**"Tilgang mangler"** and the client-level banner "Det er lagt til scopes pa
klienten som virksomheten din ikke har tilgang til". Requesting such a scope in a
token fails the **whole** request, so a declared-but-ungranted scope must stay
out of `MASKINPORTEN_SCOPES` and `IDPORTEN_SCOPES` until the grant lands.

Access is applied for through **Skatteetaten's own service desk**, not through
Digdir and not through the portal:

    https://eksternjira.sits.no/plugins/servlet/desk/site/global

One request can cover several services and both test and production, and it
requires accepting the terms of use for the services asked for. Two
prerequisites catch people out:

1. **Access to the service desk itself** has to be delegated in Altinn before
   the form can be submitted - Hovedadministrator or Begrenset signeringsrett.
2. **A konsumentavtale and an egenerklaering** must be signed by the
   organisation, and production access is only granted after a *successful test*
   in the test environment. So the test integration is a prerequisite for
   production, not a parallel track.

No processing time is published, and the MVA scopes are documented as being
handled manually, so assume weeks rather than days.

Sources: skatteetaten.no "Her kan du soke om tilgang til tjenester for
innrapportering" and "Fa tilgang til data".

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

## Implementation (Python)

No maintained Python library exists, so this is a thin module over `PyJWT`
(`pyjwt[crypto]`) and `httpx`. References used: Digdir's
`felleslosninger/jwt-grant-generator` (Java, canonical grant construction),
`ks-no/fiks-maskinporten` (token-caching design), `torleifg/maskinporten-scripts`
(Python example).

| What | Where |
| --- | --- |
| The JWT grant, discovery, token cache | `myntos.auth.maskinporten.client` |
| Key loading (JWK **or** PEM), public JWK, RFC 7638 thumbprint | `myntos.auth.signing_keys` |
| ID-porten authorization-code flow (`private_key_jwt`) | `myntos.auth.idporten.client` |
| Altinn token exchange, machine-token cache | `myntos.integrations.altinn.tokens` |
| Altinn 3 instance lifecycle | `myntos.integrations.altinn.instances` |
| Skattemelding fetch/validate/submit | `myntos.integrations.skatteetaten.client` |
| MVA validation and submission | `myntos.integrations.skatteetaten.mva` |
| Tests (Digdir and Altinn stubbed) | `tests/test_digdir_auth.py`, `tests/test_altinn_submission.py` |

Key handling: the private key is read from `MASKINPORTEN_JWK_PATH` (or an
injected secret env var); never from the repo; never logged, and neither are
tokens. Both encodings are accepted — a JWK document or a PEM private key —
because a key from `openssl genrsa` is as registrable as one from Digdir's
self-service.

**The `kid` is the trap.** Digdir looks the signing key up by the `kid` in the
JWT header, and a value that matches no registered key is answered with
`invalid_client` — which says nothing about key ids. A JWK carries its own and
that one is used; a PEM carries none, so `MASKINPORTEN_KID` (or `IDPORTEN_KID`)
is required for one. To see what to register and what to set:

```bash
cd backend
uv run python -m myntos.auth.digdir_key show maskinporten          # existing key
uv run python -m myntos.auth.digdir_key generate --out ../secrets/idporten.pem
```

Both print the public JWK to paste into Samarbeidsportalen, and write it to a
`.public.jwk.json` file next to the private key so a 4096-bit modulus is copied
from a file rather than out of a terminal.

**Read the `kid` back from the portal.** The helper proposes the RFC 7638
thumbprint, but Samarbeidsportalen assigns a **UUID** of its own to a registered
key (observed 2026-08-23: key `5c613c2f-…` on the test client), and the portal's
value is what the JWT header must carry. Set `MASKINPORTEN_KID` from the Nøkler
tab, not from the helper's output. Registered keys expire after **at most one year**:
rotate by registering the new key alongside the old one, switching the `*_KID`
variable, and only then removing the old key from the portal.

## Test vs prod

Fully separate universes: separate client registrations, separate test
certificates, synthetic organizations/persons from **Tenor Testdatasøk** in
TT02. A working test setup proves nothing about prod, and prod scope access
requires manual approval by Skatteetaten.

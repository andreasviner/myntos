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

  **Any one `altinn:*` scope fixes it**, registered on the ID-porten client in
  Samarbeidsportalen and then requested in `IDPORTEN_SCOPES`. What the scope
  *is* does not matter to the exchange; that the token has an Altinn audience at
  all does.

  This paragraph said "the scope is `altinn:enduser`" until 2026-09-03, because
  that is what was added in test on 2026-08-24 and the exchange went from 403 to
  a token with nothing else changed. It was the wrong conclusion from a true
  observation. **Production disproves it**: its ID-porten client has never held
  `altinn:enduser`, requests
  `altinn:accessmanagement/authorizedparties` as its only `altinn:*` scope, and
  exchanges tokens on every company confirmation. `altinn:enduser` came from
  Altinn's Altinn-2-era
  [ID-porten tutorial](https://altinn.github.io/docs/api/rest/kom-i-gang/tutorial-postman/id-porten-token/),
  and Samarbeidsportalen no longer offers it on either client: the
  `altinn:enduser/*` scopes still in the picker are labelled LEGACY and point at
  the same name without the prefix. Do not go looking for the checkbox.

  A separate report ([Altinn/altinn-authentication#7](https://github.com/Altinn/altinn-authentication/issues/7))
  says a test person must have logged into `tt02.altinn.no` once before their
  token can be exchanged. **Tried, and it changed nothing** (2026-08-24) - the
  missing scope was the whole cause. Recorded so nobody spends a second
  afternoon on it.

  Only the *person* plane is affected: the Maskinporten exchange works without
  any of this, because those tokens carry `altinn:instances.*`.

## Which companies a person may report for: `altinn:accessmanagement/authorizedparties`

Altinn already holds the answer the setup form would otherwise ask a
regnskapsfører to type once per client. One call, with an **exchanged Altinn
token**:

```
GET {altinn_platform}/accessmanagement/api/v1/authorizedparties?includeAltinn2=true
Authorization: Bearer <Altinn token>
```

It answers with the parties the token's holder may act for — the same list
Altinn's own "Velg aktør" picker is built from — as a JSON array, main units
carrying their subunits.

Three things decide whether it works:

- **The scope is `altinn:accessmanagement/authorizedparties`**, registered on
  the **ID-porten** client in Samarbeidsportalen and requested in
  `IDPORTEN_SCOPES`. Without it: **403**, naming nothing. The
  `…/authorizedparties.resourceowner` variant is a different scope for service
  owners looking up somebody else's list, and is not what an end-user system
  asks for.
- **It is a person's list.** The endpoint reads `userId` off the Altinn token,
  and a Maskinporten-derived token has none — that is a **401**, and it reads
  exactly like a bad token. So the ID-porten plane is the only one that answers
  here, which is also why the scope belongs on that client and not on the
  Maskinporten one.
- **`includeAltinn2=true`.** Left out, roles that still live in Altinn 2 are
  omitted and the list is quietly short — no error, just missing clients.

Two fields in the answer are easy to skip and both matter. `isDeleted` marks a
party that no longer exists, and `onlyHierarchyElementWithNoAccess` marks one
included *solely* to hold its subunits: the person has no rights on it at all.
Offering either as something to file for is offering a 403 dressed up as a
choice, so `myntos.api.v1.skatt` filters both, along with every party that has
no organisasjonsnummer (those are persons, identified by fødselsnummer, which
never leaves the server).

Verify it against a live environment with the diagnostic, which reads the list
as a synthetic person and reports how many companies came back:

```bash
cd backend
uv run python -m myntos.auth.digdir_check --person <synthetic fnr>
```

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

## Which scope is where (probed 2026-08-28)

Three states, and a call that fails looks the same in all three:

1. **Granted** to the organisation, by the API provider's service desk
2. **Registered on a client**, by us in Samarbeidsportalen - *per client*, and
   this is the step that keeps getting missed
3. **Requested** in `MASKINPORTEN_SCOPES` / `IDPORTEN_SCOPES`

A scope must be all three. Requesting one that is not granted fails the **whole**
token request, so step 3 waits for step 1.

`profile` is the exception that needs no grant from anybody: it is Digdir's own,
so only steps 2 and 3 apply. It adds the `name` claim, from Folkeregisteret, and
it is the only thing that lets a person's ID-porten login be recognised in a
company's styreliste in Enhetsregisteret. Without it the organisasjon page can
only say "trolig" where it could say "bekreftet"; nothing breaks, because
`domain/identity/register_match.py` falls back to the name on the account. See
[../register-access.md](../register-access.md).

Also: **test and production are separate**. Adding a scope to a production client
does nothing for `f8293b48`, which is a test client, and every verification in
this repository runs against test.

| Scope | Maskinporten `f8293b48` | ID-porten `5aa404aa` |
| --- | --- | --- |
| `altinn:instances.read` / `.write` | usable | n/a (comes via the exchange) |
| `altinn:enduser` | n/a | **retired** - not in the picker, and not needed: any `altinn:*` scope gives the token its audience |
| `digdir:skattemelding` | usable | usable |
| `skatteetaten:formueinntekt/skattemelding` (+ `/eiendom`) | usable | usable |
| `skatteetaten:mvameldingvalidering` | not registered | usable |
| `altinn:accessmanagement/authorizedparties` | granted, **not on this client** | usable |
| `altinn:delegationrequests.read` / `.write` | usable | not registered |
| `altinn:clientdelegations.read` / `.write` | usable | not registered |
| `altinn:authentication/systemregister.write` | usable | not registered |
| `altinn:authentication/systemuser.request.read` / `.write` | usable | not registered |
| `altinn:correspondence.read` | granted, not on client | not registered |
| `altinn:events.subscribe` | granted, not on client | not registered |
| `digdir:dialogporten` | granted, not on client | not registered |
| `skatteetaten:mvameldinginnsending` | **retired** - see mva.md | **retired** |

Granted for org `938193045` in **both test and production** (Altinn Servicedesk,
2026-08-28): the twelve Altinn/Digdir scopes above other than
`authorizedparties`, which was already held.

To re-check any of this rather than trusting the table, ask Maskinporten for a
token with one scope and read the refusal: `invalid_scope ... invalid scopes for
client` means not registered on that client. The same wording is used for a scope
that does not exist, so it cannot tell you whether a name is real - only the
portal's picker can.

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

## Systembruker: acting without a person present

The only thing the person-present chain cannot do is retrieval for a named
taxpayer with no human in the loop: the machine plane answers 400
`SME-EKSTERN-005` because a client-credentials token has no logged-in user whose
rights can be checked. Systembruker fixes that by giving the token a subject.

It also outlives a deadline the proven route does not. Reading a client's
skattemelding works today because daglig leder is an Altinn **role**, and roles
stop counting in access checks on **2026-12-01**.

**Registered in TT02 on 2026-08-28** (`myntos.integrations.altinn.systemregister`):

| | |
| --- | --- |
| System id | `938193045_myntos` |
| Altinn's own handle | `426f7e05-23e1-4fd3-b256-e929063b0a30` |
| Declared rights | `app_skd_formueinntekt-skattemelding-v2`, `app_skd_mva-melding-innsending-v1` |
| Maskinporten client | `f8293b48-…` (test) |
| `isVisible` | **true** (findable in Altinn's catalogue) |

Four things learned doing it:

- **The id is `{vendor orgnr}_{name}` and is permanent in practice.** Once a
  customer has approved a system user against it, changing it means every
  customer approving again.
- **`rights` is a ceiling, not a grant.** It bounds what the system may ever
  request; the customer's approval is what grants it, per customer. So the list
  is the two apps we actually submit to and nothing more.
- **`allowedredirecturls` must be https.** The loopback URL ID-porten requires
  is rejected here, so the deployed frontend is the only valid answer even in
  test.
- **The create call answers with a bare uuid string, not an object**, and the
  registration is confirmed by reading the system back rather than by parsing
  that.

`isVisible` false keeps the system out of Altinn's catalogue while it is being
built. Vendor-initiated system-user requests still work, because the vendor
generates the confirmation URL itself, so turning it on is a decision about
being *found* rather than about being *usable*.

`isVisible` controls being *found*, not being *usable*: an invisible system can
still have system users, because the vendor generates the confirmation URL
itself. Updating the registration is `PUT {vendor}/{system id}` with the **whole
document** - a partial body missing `clientId` would unhook the system from its
Maskinporten client, and nothing would say so until the next token request.

### The approval is the customer's, in Altinn, and cannot be ours

Creating a system-user request returns a `confirmUrl` on Altinn's own access
management UI:

    POST {platform}/authentication/api/v1/systemuser/request/vendor
    -> 201 {"id": "...", "status": "New",
            "confirmUrl": "https://am.ui.tt02.altinn.no/accessmanagement/ui/
                           systemuser/request?id=..."}

Somebody at the customer with the tilgangsstyring or hovedadministrator access
package opens that, logs in, and approves. **The vendor cannot approve on their
behalf** - which is the entire point, since the approval is what authorizes us
to act with nobody watching.

Tried and confirmed (2026-08-28): the end user can `GET` the request with an
exchanged ID-porten token (200), but `POST .../approve` answers **403**. The
approve endpoints belong to the access-management API, which requires
`altinn:accessmanagement/enduser:requests.read` / `.write` - scopes we do not
hold. Adding them would let an automated test approve headlessly with TestID;
they are not needed for the product, where a real customer clicks.

### It works. Verified end to end 2026-08-28

A customer approved (TestID as `08866995401`, daglig leder of `314067630`), and
then, with **nobody logged in**:

    200  GET api-test.sits.no/api/skattemelding/v2/utkast/2025/314067630

The same call with a plain client-credentials token answers 400
`SME-EKSTERN-005`. The Maskinporten-to-Altinn exchange also accepts a
systembruker token, so the instance APIs are reachable on this plane too.

The token's `authorization_details` is what makes the difference:

    [{"type": "urn:altinn:systemuser",
      "systemuser_org": {"authority": "iso6523-actorid-upis",
                         "ID": "0192:314067630"},
      "systemuser_id": ["ffa04a65-397d-4df9-ad64-4425ef0d4a45"],
      "system_id": "938193045_myntos"}]

`consumer` remains `0192:938193045` - us. The token says who we are *and* whose
system user we are acting as, which is the subject Skatteetaten could not find
before.

**The trap, and it cost the first attempt: `externalRef` is part of the system
user's identity.** Altinn documents it as needed "only when multiple system users
point to the same system", which reads as optional. It is not: a request created
with one produces a system user keyed by it, and a token request that omits it is
answered

    404  invalid_altinn_customer_configuration

which names nothing. `myntos.integrations.altinn.systemuser` therefore requires
it and returns it on the request object, so a caller never has to remember what
it sent.

### What remains for unattended submission

Retrieval is done. Submission still requires a person by Skatteetaten's own
policy, so systembruker does not change that half - see skatteetaten.md. What it
changes is everything before the signature: fetching, validating, creating the
instance and uploading can now run on a schedule.

The grant side is **done**: `JwtGrantTokenProvider.get_access_token` takes
`systemuser_org` and emits RFC 9396 `authorization_details` of type
`urn:altinn:systemuser` inside the assertion. It refuses to combine that with
`consumer_org`, since a grant is either a delegation or a system user, and the
cache key includes the customer - a token carries its authority in its claims,
so sharing one across customers would be acting with the wrong authority.

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
| The companies a person may report for | `myntos.integrations.altinn.authorized_parties` |
| Skattemelding fetch/validate/submit | `myntos.integrations.skatteetaten.client` |
| MVA validation and submission | `myntos.integrations.skatteetaten.mva` |
| Tests (Digdir and Altinn stubbed) | `tests/test_digdir_auth.py`, `tests/test_altinn_submission.py`, `tests/test_altinn_authorized_parties.py` |

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

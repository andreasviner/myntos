# BankID sign-in through a broker (Idura)

"Logg inn med BankID" is a login to Myntos, bought from a broker. It is not
ID-porten and never becomes ID-porten: Digdir's terms for private customers
allow ID-porten for reaching public services, which is what the filings use,
and not as a product's everyday sign-in. A BankID login here proves nothing to
Skatteetaten; every filing still asks for its own ID-porten login at level high.

Code: `backend/src/myntos/auth/oidc/providers.py` (`bankid_profile`, the
`IdentityProvider.BANKID` branch of `_build`), `auth/oidc/client.py` (the
`private_key_jwt` assertion), `domain/identity/provider_signup.py` (signup),
`domain/identity/service.py` (`is_bankid_account`, `link_provider`,
`unlink_provider`, `confirm_identity`), `domain/identity/email_tokens.py` (the
reset refusal and the switch to e-mail), the callback and `POST
/auth/bankid/link` in `api/v1/auth.py`, `/account/identities` and `POST
/account/switch-to-email` in `api/v1/account.py`. Frontend: `/logg-inn/`,
`/registrer/`, `/fullfor-registrering/`, `/konto/`, `/bytt-til-epost/`. Built
2026-10-10, not yet switched on. The rules it follows are in
[ARCHITECTURE.md § Federated providers](../ARCHITECTURE.md#federated-providers).

## BankID is the only way into a BankID account

**An account that signs in with BankID signs in with nothing else**: no
password, no Google, no Microsoft. Decided by the owner on 2026-10-10, because
of what BankID's terms call ID-veksling.

### What the terms say

Stø's *Terms for Merchants' Access to and Use of the BankID Service* («Merchant
Terms», listed on stoe.no/standardvilkaar as April 2026; the document itself
carries no date), § 7.2, *Usage Restrictions – ID Exchange with Personal BankID*:

> ID exchange with Personal BankID is not permitted unless the Merchant has
> entered into an agreement with the Issuer for the purchase of ID exchange as
> an add-on product under the BankID Service or has entered into a separate
> agreement with the Issuer for ID exchange.

and its glossary, definition (l):

> **ID Exchange**: Authentication with and any other use of Personal BankID to
> activate, restore or increase trust in the Merchant's identification or
> authentication mechanism in its own app, establishment of derived identities
> of End Users for use in merchant's own business or within the same group as
> the Merchant, and/or to establish and forward derived identities of End Users
> to the Merchant counterparties.

*Vilkår for tillatelse til ID-veksling under BankID Brukerstedsvilkår punkt 7.2*
(standardversjon 1.0, 14 September 2026; listed as October 2026), § 2, gives the
Norwegian definition:

> «Autentisering med og enhver annen bruk av Person BankID til å aktivere,
> gjenopprette eller heve tilliten til Brukerstedets identifiserings- eller
> autentiseringsmekanisme i egen app/flate, etablering av deriverte identiteter
> av Sluttbrukere for bruk i egen virksomhet eller i annen virksomhet innenfor
> samme konsern som Brukerstedet, og/eller for å etablere og videreformidle
> deriverte identiteter av Sluttbrukere til Brukerstedets medkontrahenter (egne
> bedriftskunder).»

and says outright, in the same section:

> Brukerstedets autentisering av sine personkunder med Person BankID som
> grunnlag for Brukerstedets etablering av egne eller tredjeparts løsninger for
> aldersverifisering, kundeautentisering, pålogging eller andre former for
> identitetskontroll og verifikasjon av personkunder, omfattes av
> ID-vekslingsforbudet.

Both read on 2026-10-10 from the PDFs linked at
[stoe.no/standardvilkaar](https://stoe.no/standardvilkaar). Re-read them before
relying on this; terms change.

### What that means here

Using BankID as the login itself, every time, is ordinary authentication and
allowed. What is not allowed without a separate, paid permission is an account
BankID established or vouched for being opened afterwards with Myntos's own
password, or BankID being used to set up or restore another way in. The owner
chose not to buy the permission and to make the two mutually exclusive instead.

**A BankID account is one with a `bankid` row in `user_identity`.** Nothing else
marks it (`service.is_bankid_account`), so there is no flag to drift from the
row, and no migration was needed. Every path that could add a way in asks, not
only the paths that create a BankID link, so the rule also holds for an account
linked on the day BankID sign-in was built, before the rule existed.

| Path | On a BankID account |
| --- | --- |
| "Glemt passord" (`POST /auth/password/forgot`) | Same 202, same empty body, mail sent after the response, as for every address. The mail says the account signs in with BankID and links to `/logg-inn/`; it has no reset link. A `bankid_notice` row is written in place of the reset token, so the hourly cap counts these mails too and the database does the same one insert |
| A reset link (`POST /auth/password/reset`) | 409 `bankid_account`, even for a valid link. None should exist: the request never makes one, and the switch to BankID retires any already in the inbox |
| Change password (`POST /auth/password`) | 409 `bankid_account` |
| Password login, if a password survives from before the rule | 409 `bankid_account`, asked only after the password is known to be right, so a stranger learns nothing |
| Link Google or Microsoft from `/konto/` | Refused at `/auth/{provider}/start` (`?error=bankid_account`, no trip to the provider), and again in `link_provider` for a link started before the account became a BankID account |
| Google or Microsoft login whose **verified** address is this account's | Refused, `?error=email_belongs_to_bankid_account` on `/logg-inn/` or `/registrer/`. The older rule that attaches a provider-verified address to its account stops here. An **unverified** address gets `unverified_provider_email` first, so somebody who has not shown the address is theirs learns no more than that rule already says |
| Remove BankID (`DELETE /account/identities/bankid`) | 409 `bankid_account`, always. `removable` is false on the row. The way out is the switch to e-mail |

### The two switches

**E-mail (password, Google or Microsoft) to BankID**: «Koble til BankID» on
`/konto/`. The page shows «Når du kobler til BankID, kan du bare logge inn med
BankID. Passordet ditt og innlogging med Google eller Microsoft slutter å
virke.» and needs a ticked box. Then `POST /auth/bankid/link`, which asks for
the password (or, on an account without one, a login in the last ten minutes:
the same proof as deleting the account, `service.confirm_identity`) and answers
with the BankID URL. When the callback completes the link, in one transaction:
`password_hash` is cleared, every other `user_identity` row is deleted, unspent
reset and switch links are retired, every other session is revoked (the one
the person is in is kept), and the account's address is mailed that sign-in
changed. A BankID already linked to another account is refused as before
(`identity_already_linked`).

*Why the password, which the owner's spec did not ask for:* without it, a
hijacked session could link the attacker's own BankID, wipe the owner's
password and sign the owner out everywhere, and because a reset never reaches a
BankID account, the owner would have no way back in. A link started with a GET
(`/auth/bankid/start?intent=link`) is refused with `bytte_ikke_bekreftet` for
the same reason: a GET is anything a page can put in front of a signed-in
browser, and the POST is covered by the cookie's origin check.

**BankID to e-mail**: «Bytt til innlogging med e-post» on `/konto/`, with «Når
du bytter til e-post, slutter innlogging med BankID å virke for denne kontoen.»
and a ticked box. `POST /account/switch-to-email` mails a single-use link to
the address the account already has (the request takes no address) and changes
nothing. The link opens `/bytt-til-epost/#token=…`; when a password is chosen
there (`POST /auth/switch-to-email/confirm`), in one transaction the `bankid`
row is deleted, the password set, the address stamped confirmed, the lockout
lifted and the password links retired, and then every session is revoked. Until
then BankID works as before, and a link never used leaves the account as it
was.

**This is the one step where BankID is the gate**: the person starts it signed
in with BankID. It was designed so that BankID is not what makes the password.
The mailed link carries the proof, exactly as a reset link does, and a hijacked
BankID session gets no further than sending a message to the owner's inbox
(whose text tells them, if they did not ask, to sign the other devices out).

The token has its own purpose, `switch_to_password`, not `reset_password`. A
reset is asked for anonymously and must never reach a BankID account; this link
is asked for by a signed-in BankID account and removes the BankID. One purpose
for both would make "refuse a reset for a BankID account" and "accept this link
for a BankID account" the same lookup. It lives as long as a reset link (30
minutes) and shares the per-purpose hourly cap.

### Nothing is "BankID-verified"

No badge, no stronger identity, no `bekreftet` in the register match comes from
a BankID sign-in. The name on an account made with BankID came from BankID, and
it is an ordinary account name: `trolig` in
[register-access.md](../register-access.md), like a typed one, and it stays an
ordinary name after a switch to e-mail. Reading it as verified would be using a
BankID login to raise the trust of an identification of our own, which is the
definition above. Checked on 2026-10-10: nothing in `register_match.py` or the
frontend did so. Only the ID-porten link makes `bekreftet`. (The permission
terms say the same of their own customers, § 3.2.4: a service built on
ID-veksling may not be presented as BankID-verified.)

### Questions for Stø, through Idura, when the agreement is ordered

Ask in writing, and keep the answer here:

1. **The switch from BankID to e-mail**, described above: is a flow started from
   a BankID session, completed by a single-use link to the account's existing
   address, which removes BankID when the password is set, outside ID-veksling?
   It was designed to be, but it is the one place a BankID session leads to a
   password.
2. **Session length after a BankID login.** A session lives up to
   `SESSION_LIFETIME_DAYS` (14) with a 24-hour idle limit. That is the login
   itself, not a second mechanism, but it is worth hearing that Stø reads it the
   same way.
3. **API keys** ([agents-and-api.md](../agents-and-api.md), built 2026-10-11):
   whether a connection key issued by a signed-in BankID account, with a
   refresh chain that can live a year, counts as a derived identity. Until Stø
   answers, a BankID account makes no keys (`auth/agent_keys.issue_key`
   refuses with `bankid_account`), and an account switched to BankID loses the
   keys it had (`api/v1/auth.py`, where the switch ends the other sessions).

If BankID and a password on one account is ever wanted, the route is the
permission (*Vilkår for tillatelse til ID-veksling*, prices on Stø's site, § 4).
It comes with conditions, among them re-authenticating every customer with
BankID at least once a calendar year (§ 3.2.6), and its § 3.2.1, which allows
ongoing login with the merchant's own pin or biometrics, names only payment
wallets and accounts.

### Gaps

- **An address squatted by a BankID signup is not freed by a reset.** A BankID
  signup takes whatever address is typed, unconfirmed. Before this rule, the
  real owner of that address could ask for a reset and get in; now they get the
  "signs in with BankID" mail and nothing else, and cannot sign up with their own
  address. It needs a way for the owner of an unconfirmed address to release it
  from an account they do not hold, which does not exist yet.
- **No mail, no way out of BankID.** A deployment that sends no mail cannot
  offer the switch to e-mail (`/konto/` says so), and the account stays a BankID
  account.

## Why a broker, and why Idura

BankID's own OIDC service is sold to resellers; a merchant reaches it through
one. Idura (formerly Criipto, Idura ApS, Copenhagen) became part of Stø, the
company that runs BankID, in autumn 2024 (stoe.no/en/partners/idura). It has a
free test environment with test users, and its data processing agreement says
it "shall not under any circumstances transfer or cloud personal data outside
EU territory" (idura.eu/legal/privacy, § 1.5), with Idura as processor and the
customer as controller (§ 1.2).

Nothing in the code is Idura-specific except two things that are harmless
elsewhere: a bare domain in `BANKID_ISSUER` is read as `https://{domain}`, and
a token that carries Idura's `identityscheme` claim must say Norwegian BankID.
Another OIDC broker is a change of `.env`.

## What was verified, and where

Read on 2026-10-10. Re-read before relying on any of it; brokers move.

| Value | What it is | Source |
| --- | --- | --- |
| `https://<domain>.idura.broker` | issuer, no trailing slash; sandbox domains are `<name>.test.idura.broker` | discovery document of Idura's public sample domain, `https://docs-samples.test.idura.broker/.well-known/openid-configuration`; docs.idura.app/verify/getting-started/dashboard-setup/ |
| `{issuer}/.well-known/openid-configuration` | discovery | docs.idura.app/verify/guides/work-with-metadata/ |
| `/oauth2/authorize`, `/oauth2/token`, `/.well-known/jwks` | endpoints | the sample discovery document |
| `client_secret_basic`, `client_secret_post`, `private_key_jwt` | client authentication methods offered | the sample discovery document; docs.idura.app/verify/guides/privatekey-jwt/ |
| `RS256` | ID token signature | the sample discovery document |
| `S256` | PKCE method | the sample discovery document; docs.idura.app/verify/reference/request-parameters/ |
| `urn:grn:authn:no:bankid:substantial` | BankID Biometrics (the app), assurance level substantial. **The default** | docs.idura.app/verify/e-ids/norwegian-bankid/ |
| `urn:grn:authn:no:bankid` | the classic code-device authenticators, level high. The page also names `…:bankid:high`, which the sample discovery document does not list | the same page |
| `scope=openid` | name and birth date are always released; the fødselsnummer only with `ssn` and only under the dynamic scope strategy (Idura's default) | the same page; request-parameters |
| `sub` | "persistent pseudonym, uniquely identifies an eID user (per Idura Verify tenant)", e.g. `{cde37629-c67b-4318-988c-a0b378931e7d}` | the same page; docs.idura.app/verify/reference/token-contents/ |
| `identityscheme: nobankid-oidc` | which eID ran | the same pages |
| `prompt=login` | forces a fresh authentication | docs.idura.app/verify/guides/sso/ |
| `ui_locales=nb` | Norwegian BankID supports `nb` and `en` | docs.idura.app/verify/guides/choose-language/ |
| `private_key_jwt` assertion | `iss` = `sub` = client id, `aud` = the Idura domain, short-lived, `jti` optional except for some eIDs; JWKS registered under the application's OpenID Connect → Client JWKS | docs.idura.app/verify/guides/privatekey-jwt/ |

Not documented, so not relied on: an `acr` claim in the ID token (the examples
carry none; `authenticationtype` appears only in the biometric one), and
whether Idura keeps the `kid` of a registered JWKS unchanged. The returned
level of assurance is therefore not checked, unlike ID-porten's; for a login,
as opposed to a filing, that is a deliberate trade, and `identityscheme` is what
stops a different eID on the same tenant from passing as BankID.

**The `sub` is per tenant.** Moving to another Idura tenant, or another broker,
gives every person a new `sub`, and every link in `user_identity` stops
matching. Pick the production tenant once.

## What is not asked for, and why

The fødselsnummer. Signing in needs a stable identifier, and the `sub` is one;
the number would be held without the "saklig behov for sikker identifisering"
personopplysningsloven § 12 demands for it. Idura would also require Myntos to
collect explicit consent before each such login (the "SSN consent" setting).
The commonly repeated claim that the number costs extra per login was **not**
confirmed: Stø's 2026 list prices are "including NNIN" for both levels, and
Idura's price page names no separate charge. It is left out because it is not
needed, not because of price. If one arrives anyway, `bankid_profile` discards
it and logs a warning without the value.

Prices on 2026-10-10, for the owner's planning and not checked by anything:
Idura lists NO BankID Biometri at €0.089 and NO BankID High at €0.126 per
authentication, on top of a Verify plan from €67 a month (1 000 logins), with
testing free (idura.eu/pricing/verify).

## Setting it up

### Sandbox

1. Sign up at dashboard.idura.app (free; the test environment is the default).
2. **Domains**: register a domain such as `myntos.test.idura.broker`.
3. **Applications**: create a Verify application on that domain, type
   "Regular Web Application". Note the **Client ID / Realm**.
4. **Redirect URLs** on the application, exactly (no wildcards):
   - `http://localhost:8000/api/v1/auth/bankid/callback` for a local backend,
   - and whatever `BANKID_REDIRECT_URI` will be on the machine that uses this
     tenant.
5. Client authentication, `private_key_jwt` (recommended):
   - `cd backend && uv run python -m myntos.auth.digdir_key generate --out ../secrets/bankid-test.pem`
   - set `BANKID_JWK_PATH` and `BANKID_KID` to what it prints, then
     `uv run python -m myntos.auth.digdir_key show bankid` prints the JWKS;
   - paste it under the application's **OpenID Connect → Client JWKS**
     (static).

   Or toggle "Enable OAuth2 Code Flow" there and use the generated secret as
   `BANKID_CLIENT_SECRET`. Idura shows it once.
6. Leave the scope strategy **dynamic** (the default) and do **not** enable
   SSN. Enable Norwegian BankID on the application and nothing else.
7. Test users: ra-preprod.bankidnorge.no → test number generator, then order a
   netcentric BankID; password `qwer1234`, one-time code `otp`. Biometrics need
   Idura's test app (orders@idura.eu) and a non-synthetic number.
8. `.env`:

   ```
   BANKID_ISSUER=myntos.test.idura.broker
   BANKID_CLIENT_ID=<Client ID / Realm>
   BANKID_REDIRECT_URI=<the registered callback>
   BANKID_JWK_PATH=./secrets/bankid-test.pem
   BANKID_KID=<kid>
   ```

   Restart the backend. `/api/v1/auth/providers` lists `bankid` only once all
   of these are set and the key reads.

### Production

1. Switch the dashboard to **Production**, choose a Verify plan under Billing.
2. Order Norwegian BankID on its provider page. Myntos's company must be
   Norwegian, the signatories must be in the business register with the right
   to sign, and a production domain must exist first. Stø issues the
   credentials after signing; Idura quotes 10–13 working days.
3. Register a production domain (`<name>.idura.broker`, or a custom domain
   with a managed certificate) and create the production application. Its
   client id must be unique.
4. Redirect URL: `https://api.myntos.no/api/v1/auth/bankid/callback`.
5. A new key for production (never the sandbox one), registered as that
   application's Client JWKS; same scope rules as the sandbox.
6. Set the five `BANKID_*` values in the production `.env`, keep the backup
   beside it, restart, and sign in once with a real BankID.

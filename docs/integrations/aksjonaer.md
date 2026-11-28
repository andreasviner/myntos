# Aksjonærregisteroppgaven (RF-1086) & Altinn 3 Systembruker

Every Norwegian aksjeselskap (AS / ASA) must keep an **aksjeeierbok**
(aksjeloven § 4-5) and submit **Aksjonærregisteroppgaven (RF-1086)** to
Skatteetaten by **31 January** each year. Since June 2026, Skatteetaten retired
the altinn.no web form; RF-1086 can only be filed through an end-user system
using Maskinporten and an Altinn 3 systembruker.

## Upstream specifications

- **OpenAPI specification:** `skatteetaten/innrapportering-aksjonaerregister-api`
  v1.3.0 (OpenAPI 3.0.3, published 2026-10-05)
- **Official documentation:** `Skatteetaten/api-dokumentasjon` (`main`),
  `docs/api/innrapportering-aksjonaerregisteroppgave.md`
- **XSDs (vendored verbatim in `backend/tests/fixtures/aksjonaer/`):**
  - `aksjonaerregisteroppgaveHovedskjema.xsd` (`skjemanummer="890"`, `RF-1086`)
    SHA-256: `194670070a54772ad7e026e37be1d87e7840dab7621c966a881e909d00d119df`
  - `aksjonaerregisteroppgaveUnderskjema.xsd` (`skjemanummer="923"`, `RF-1086-U`)
    SHA-256: `a0f6b2a8d249f3f40290036314d3b1f5c1b3aed3ba90f58e5ce7b264c9ec33b7`

## Authentication & Altinn 3 systembruker

- **Maskinporten scope:** `skatteetaten:innrapporteringaksjonaerregisteroppgave`
  (granted on production client `a7693ab9` and test client `f8293b48` on
  2026-11-25)
- **Altinn resource:** `ske-innrapportering-aksjonaerregisteroppgave`
  (registered on `938193045_myntos` in both production and TT02 on 2026-11-25)
- **Per-company authorization (`company_system_user`):**
  1. Company admin calls `POST /api/v1/companies/{company_id}/altinn/systembruker`
     (on `/organisasjon/#altinn`), which calls Altinn's vendor request API and
     persists `external_ref`, `altinn_request_id`, and `confirm_url`.
  2. Representative approves Myntos on Altinn's `confirm_url`.
  3. Admin calls `POST /api/v1/companies/{company_id}/altinn/systembruker/oppdater`
     to poll Altinn until `status == "accepted"`.
  4. RF-1086 calls request a Maskinporten token with `systemuser_org=company.orgnr`
     and `external_ref=system_user.external_ref`.

## Endpoints implemented (`myntos.integrations.skatteetaten.aksjonaer`)

Base URL: `{SKATTEETATEN_API_BASE_URL}/api/aksjonaerregister/v1`
(`https://api-test.sits.no/api/aksjonaerregister/v1` in test,
`https://api.skatteetaten.no/api/aksjonaerregister/v1` in production).

| Step | Method & Path | Headers / Params | Response |
| --- | --- | --- | --- |
| 1. Hovedskjema | `POST /{inntektsaar}/1086H` | `idempotencyKey: <uuid>`, `Content-Type: application/xml` | `200 {"hovedskjemaId": "<uuid>"}` |
| 2. Underskjema (x N) | `POST /{inntektsaar}/{hovedskjemaId}/1086U` | `idempotencyKey: <uuid>`, `Content-Type: application/xml` | `200` empty |
| 3. Confirm | `POST /{inntektsaar}/{hovedskjemaId}/bekreft?antall_underskjema={N}` | `Accept: application/json` | `200 {"oppgavegiversLeveranseReferanse", "dialogId", "forsendelseId"}` |
| Prefill | `GET /prefill/{inntektsaar}/{organisasjonsnummer}?page={p}` | `Accept: application/json` | `200 PrefillPage` or `404` |
| Archive | `GET /{inntektsaar}/forsendelser/{forsendelseId}/dokumenter` | `page`, `size` | `200 DokumentPage` |
| Document | `GET /{inntektsaar}/forsendelser/{forsendelseId}/dokumenter/{dokumentId}` | | `200` binary |

## Schema & business rules enforced before sending

- **XSD exactness:** Both generated XML documents are validated against the
  official XSDs in `tests/test_aksjonaer_xml.py`, including Skatteetaten's
  historic XSD spellings (`AksjerLividasjonMvAntall`,
  `AksjerSlettedeLividasjonMvTidspunkt`,
  `AksjonarUtenlandskIdenifikasjonsnummer`).
- **Shareholder identity:** Mod-11 k1+k2 check on 11-digit fødselsnummer /
  D-nummer (accepts real numbers and Tenor synthetic numbers), mod-11 check on
  9-digit orgnr, `^UTL[0-9]{9}$` check on foreign ID (or name + ISO-2 country
  code). Only one identifier element is ever emitted per RF-1086-U.
- **Reconciliation:**
  - Opening and closing shareholder share sums must equal share class totals.
  - Post 1 (`AksjekapitalForHeleSelskapet`) sums share capital across all share
    classes; Post 2 reports the single share class being filed.
  - Closing `shares * par_value` must match share capital (unless liquidated).
  - On liquidation (`likvidasjon`), closing Post 1-6 values are forced to `0`.
  - Transfer buys and sells must balance; continuity codes (`OK`, `GK`, `ES`)
    require counterparty IDs.
  - Dividends must sum to total declared amount; withholding tax requires
    amount, rate, and country code together.
  - Closing share capital is cross-checked against ledger account `2000`
    (`merknad` if they differ).
- **Partial failure handling:** If `1086U` or `bekreft` fails after `1086H`
  succeeded, `POST /api/v1/companies/{company_id}/aksjonaer/send` returns HTTP
  502 while persisting the `feilet` row with `hovedskjema_id` and error details.
- **Archiving:** On confirmation (`bekreftet`), the exact submitted Hovedskjema
  and every Underskjema XML are archived in append-only `DocumentStorage` and
  kept for the five-year statutory retention period.

## Live TT02 verification checklist (owner action)

1. Apply Alembic migration `b8e4c2a19d07` (`alembic upgrade head`).
2. In TT02 (`SKATTEETATEN_API_BASE_URL=https://api-test.sits.no`,
   `ALTINN_SYSTEM_REDIRECT_URL=https://.../organisasjon/#altinn`), create a
   systembruker on `/organisasjon/#altinn` for a Tenor test AS, approve via
   `confirm_url`, and refresh status to `accepted`.
3. Test `POST /aksjonaer/prefill` and `POST /aksjonaer/send` end-to-end against
   `api-test.sits.no` and verify the resulting `oppgavegiversLeveranseReferanse`,
   `dialogId`, and `forsendelseId` in Altinn TT02 Dialogporten.

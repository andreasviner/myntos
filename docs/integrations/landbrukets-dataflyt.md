# Landbrukets Dataflyt

**Status 2026-10-12: not integrated, and not started.** Joining costs money
every year, which is the owner's decision; this file is what that decision
needs. Until then a farm books each settlement from its PDF as a composite
entry ([landbruk.md](../landbruk.md#4-a-settlement-is-the-farms-sale)).

## What it is

A cooperative (SA) owned by the farm sector: Bondelaget, Småbrukarlaget, Tine,
Nortura, the Felleskjøp, Strand Unikorn, Glommen Skog, Gartnerhallen,
Landkreditt, Gjensidige, SpareBank 1 SMN and NLR (owner list from 2017). It
carries data between the companies that buy from and sell to farms and the
programs farms keep their books in.

What an accounting program gets from it (Tjenestebeskrivelse, 11.11.2025):

- **Avregninger**: the settlements from Tine, Nortura, Fatland, Fiskå Mølle,
  Gartnerhallen and others, line by line, with the buyer's own posting
  proposal coded in NIBIO's Landbrukets kodesystem (account, product code,
  tillegg/trekk code, VAT code, quantities). This is what makes Duett and
  Tripletex book a melkeoppgjør without anyone typing it.
- **Tilskuddsavregning** from Landbruksdirektoratet, in the same format, with
  the tilskuddsbrev fetched from Altinn and attached.
- **Fakturatjeneste**: posting proposals for EHF invoices from FKA, FKRA,
  Gjensidige, Tine and Nortura. The invoices themselves come over Peppol.
- Livestock status from Kukontrollen and production data (Animalia, Tine,
  Nortura, KLF; Skifteplan, Agrilogg), for analysis per driftsgren.
- **Out**: the accounts file and the bank report, which banks and NIBIO's
  driftsgranskinger read. Deadline 31 August.

## Format and access

- XML in Dataflyt's own format; VAT codes use E2B's VatType/VatCategory pairs
  (NIBIO's MVATabell, where O_S is 25 % out and I_O is no deduction). No public
  API specification: implementation guides go to registered fagsystem vendors
  through "Prosjektplassen".
- **The vendor** registers in the Aktørregister and signs the user agreement
  for fagsystem.
- **The farmer** signs a user agreement and a databehandleravtale with BankID
  or through Produsentregisteret. An accountant registered as the farm's
  regnskapsfører in Brønnøysundregistrene gets consent automatically. Data for
  a farm not yet signed up is kept for up to two years.
- Farms and accounting firms use it free of charge, and vendors are "expected"
  not to charge them for it.

## What it costs a vendor

From *Priser og betingelser*, prices from 1.1.2025, excluding VAT, for a
vendor that is not an owner:

| Item | NOK |
| --- | --- |
| Etableringsavgift per fagsystem (plus a variable part set per vendor) | 65 986 once |
| Samtykke/aktørregister and kodesystem | 85 693 a year |
| Avregningstjeneste | 34 320 a year |
| Tilskuddsavregning | 51 391 a year |
| Konteringstjeneste | 154 144 a year |
| Fakturatjeneste | 34 320 a year |
| Regnskapstjeneste | 111 333 a year |
| Kommunikasjon (samtykke) | 34 320 a year |

The useful minimum (settlements, subsidies, coding, the register) is about
**325 000 kr a year**, everything about **505 500 kr a year**, before the
establishment fee. An ownership share is 200 000 kr and gives lower prices.

## Who already integrates

Duett, Tripletex, PowerOffice Go, DNB Regnskap, Eika Regnskap and Unimicro.
Fiken does not ("Nei, men vi anbefaler at du bruker EHF"), and it still has
"flere tusen bønder". So a farm can keep its books without Dataflyt; it types
or uploads every settlement instead, which is what Myntos offers today with KI
reading the PDF.

## What would change in Myntos

Little in the ledger, because the shape is already there: a settlement from
Dataflyt is a composite entry whose lines come coded instead of typed. The
work is the client, the consent flow, a mapping from NIBIO's
department.account and product codes onto the farm accounts
(`chart.LANDBRUK_ACCOUNTS` uses NIBIO's jordbruk numbers for exactly this
reason), quantities on the line, and matching a settlement to its bank line
without booking it twice (the double-posting problem Duett's help pages warn
about when Dataflyt and bank reconciliation both post).

## Sources

- Tjenestebeskrivelse: <https://www.landbruketsdataflyt.no/avtaler/tjenestebeskrivelsedataflyt.pdf>
- Priser og betingelser: <https://www.landbruketsdataflyt.no/avtaler/Priserogbetingelser.pdf>
- Aktørregister: <https://www.landbruketsdataflyt.no/areg/>
- Tripletex: <https://hjelp.tripletex.no/hc/no/articles/20663036603793>
- Duett: <https://duett.no/landbruksregnskap/dataflyt-i-duett>,
  <https://support.duett.no/hc/no/articles/32391523856402>
- Fiken: <https://hjelp.fiken.no/fiken-for-boender>

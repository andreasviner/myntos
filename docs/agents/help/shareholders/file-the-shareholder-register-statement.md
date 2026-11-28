---
title: File the shareholder register statement (RF-1086)
description: How a person reviews pre-flight checks, downloads RF-1086 XML, and sends Aksjonærregisteroppgaven (RF-1086) from Myntos to Skatteetaten via Altinn 3 systembruker. Use when someone asks how to file RF-1086 or aksjonærregisteroppgaven in Myntos.
pages: /aksjonaer/ /organisasjon/
skill: myntos-aksjonaer
---

# File the shareholder register statement (RF-1086)

Every Norwegian AS and ASA must file Aksjonærregisteroppgaven (RF-1086) to
Skatteetaten by **31 January** the year after the income year. Since June 2026
the altinn.no web form is closed and RF-1086 can only be filed through an
end-user system. Myntos sends Hovedskjema (`1086H`), all Underskjemaer
(`1086U`), and confirmation (`bekreft`) directly to Skatteetaten using the
company's approved Altinn 3 system user, and archives the submitted XML files.

## They might ask

- «Hvordan sender jeg inn aksjonærregisteroppgaven i Myntos?»
- «Hvordan leverer jeg RF-1086 til Skatteetaten?»
- «Hvor finner jeg kvittering og XML for innsendt aksjonærregisteroppgave?»
- "How do I file the shareholder register statement in Myntos?"

## Before they start

- They need the role «Medlem», «Administrator» or «Eier» to file.
- The shareholder register must be up to date with no red errors at the top of
  `/aksjonaer/`: see
  [Keep the shareholder register](https://myntos.no/llms/help/shareholders/keep-the-shareholder-register.md).
- The company must have an approved Altinn 3 system user for
  Aksjonærregisteroppgaven: see
  [Set up an Altinn system user](https://myntos.no/llms/help/shareholders/set-up-altinn-system-user.md).

## Steps

1. Open `https://myntos.no/aksjonaer/` (or click «Til aksjonærregisteroppgaven →»
   from «Frister») and select the income year with `‹` and `›`.
2. Verify that there are no red error messages at the top of the page, and check
   any yellow notes (such as a difference against account `2000`).
3. Scroll to «Kontroll og innsending til Skatteetaten».
4. If the page shows «Sett opp Altinn-systembruker →», click it to complete
   approval on «Innstillinger for selskapet» (`/organisasjon/#altinn`), then
   return to `/aksjonaer/`.
5. Check or enter «Kontakt-e-post til Skatteetaten», «Kontaktperson (navn)»,
   and optional «Rolle» and «Telefon». If the company has more than one share
   class, choose which class to file under «Aksjeklasse som skal sendes» and
   file each class separately.
6. Optionally click «Last ned hovedskjema (RF-1086 XML)» (or «XML» on any
   shareholder row) to inspect the generated XML first.
7. Read and tick the confirmation box («Jeg bekrefter at
   aksjonærregisteroppgaven (RF-1086) for … er kontrollert og skal sendes til
   Skatteetaten.»).
8. Click «Send inn aksjonærregisteroppgaven». Once Skatteetaten confirms, the
   submission appears under «Innsendinger for året» with status «Bekreftet
   mottatt av Skatteetaten», the Skatteetaten «Leveransereferanse», and download
   links for the archived Hovedskjema and Underskjema XMLs. The deadline on
   «Frister» automatically updates to «Sendt fra Myntos».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Rett feilene som står øverst på siden. Deretter kan aksjonærregisteroppgaven sendes inn.» | One or more blocking `feil` findings remain (e.g. missing fødselsnummer or share sum mismatch) | Fix each red item at the top of the page |
| «For å sende aksjonærregisteroppgaven direkte til Skatteetaten må selskapet ha en godkjent Altinn-systembruker.» | Altinn 3 system user has not been approved yet | Click «Sett opp Altinn-systembruker →» and approve it in Altinn |
| «Feilet under innsending» in «Innsendinger for året» | Skatteetaten rejected one of the forms or the connection dropped partway | Read «Feilmelding» on the filing row, correct the data if Skatteetaten reported a validation error, and submit again |

## Good to know

- A confirmed RF-1086 submission cannot be withdrawn; to correct an already
  filed year, update the aksjeeierbok and send a new complete RF-1086 for the
  same income year (Skatteetaten replaces the earlier filing with the newest
  approved one).
- Archived XML documents are kept with the company's books for five years
  (bokføringsloven § 13).

## Related

- [Keep the shareholder register](https://myntos.no/llms/help/shareholders/keep-the-shareholder-register.md)
- [Set up an Altinn system user](https://myntos.no/llms/help/shareholders/set-up-altinn-system-user.md)
- [See your deadlines](https://myntos.no/llms/help/overview/see-your-deadlines.md)
- An agent with a key can prepare the statement and submit it for your approval:
  [myntos-aksjonaer](https://myntos.no/llms/skills/myntos-aksjonaer.md)

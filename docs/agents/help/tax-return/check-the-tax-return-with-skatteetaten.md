---
title: Check the tax return with Skatteetaten
description: How a person has Skatteetaten check an aksjeselskap's (AS) or enkeltpersonforetak's (ENK) tax return (skattemelding) from the Skattemelding page in Myntos - ID-porten login, the AS shareholder question or ENK business details and hand-off choice, «Kontroller hos Skatteetaten», and what the answer means. Nothing is filed by the check. Use when someone asks how to check, validate or test their AS or ENK skattemelding before sending it.
pages: /skattemelding/
skill: myntos-skattemelding
---

# Check the tax return with Skatteetaten

The check sends the tax return to Skatteetaten's own validation, which says
whether it would be accepted and computes the tax. **Nothing is filed.** The
answer appears on the page, and the person decides afterwards whether to send.
For an AS, Myntos fetches the company's skattemelding on the orgnr, adds the
formue and the person's answer, and checks it together with the
næringsspesifikasjon. For an ENK, Myntos fetches the owner's personal
skattemelding on their fødselsnummer from ID-porten, leaves every personal
post (salary, bank accounts, loans, deductions) untouched, and checks it
together with the ENK næringsspesifikasjon.

## They might ask

- «Hvordan kontrollerer jeg skattemeldingen i Myntos?»
- «Hvordan kontrollerer jeg skattemeldingen for enkeltpersonforetaket mitt?»
- «Hva blir skatten?»
- «Hva betyr spørsmålet om ytelser mellom selskap og aksjonær?»
- «Blir de private postene mine borte når jeg sender fra Myntos?»
- "How do I validate my AS or ENK tax return before sending it?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». A «Leser» sees «Det er den som
  kan føre i regnskapet, som kontrollerer og sender skattemeldingen.»
- BankID, Buypass or Commfides for ID-porten. For an AS, the person needs the
  right to file for the company in Altinn (daglig leder, or someone given the
  right). For an ENK, the owner logs in with their own ID-porten (their own
  fødselsnummer).
- Nothing in red on the page:
  [Fix a tax return that cannot be checked](https://myntos.no/llms/help/tax-return/fix-a-tax-return-that-cannot-be-checked.md).

## Steps

1. Open `https://myntos.no/skattemelding/` and choose the year with «‹» and «›».
2. Under «Kontroll og innsending», press «Logg inn med ID-porten» and log in.
   They come back to the same year.
3. Fill in the form for the organisation form:
   - **For an AS:** answer «Skal selskapet rapportere opplysninger om ytelser
     mellom selskap og personlig aksjonær, deltaker eller nærstående til
     denne?» with «Nei» or «Ja». It is Skatteetaten's own question in the
     skattemelding; benefits are loans, pay, rent or gifts between the company
     and a shareholder who is a person, or someone close to them. Myntos
     cannot see it in the books, so it does not guess.
   - **For an ENK:** review the prefilled fields and adjust if needed:
     - «Næringstype» (industry type from Skatteetaten's list, suggested from
       Enhetsregisteret's næringskode)
     - «Næringsbeskrivelse» (short activity description)
     - «Kommunenummer (4 sifre)» (prefilled from Enhetsregisteret when
       available)
     - «Måneder drevet i året» (1 to 12, usually 12)
     - «Innehavers andel (%)» (normally 100; lower when splitting profit with
       a spouse)
     - Under «Hvordan vil du levere?», choose «Send inn næringsdelen og
       fullfør på skatteetaten.no (anbefalt)» (transfers the
       næringsspesifikasjon to the personal return so the owner checks
       personal items and submits on skatteetaten.no) or «Send inn komplett
       personlig skattemelding direkte» (submits the complete personal return
       immediately with Skatteetaten's prefilled personal items).
4. Press «Kontroller hos Skatteetaten». The page says «Henter skattemeldingen
   fra Skatteetaten og sender den til kontroll …».
5. The box «Send inn» appears with the answer:
   - «Skatteetaten fant ingenting som stopper skattemeldingen.» and «Skatteetaten
     har regnet ut skatten til …»: it can be sent:
     [Send the tax return](https://myntos.no/llms/help/tax-return/send-the-tax-return.md).
   - «Skatteetaten ville avvist skattemeldingen …»: the list under it says why.
     Correct the books and check again.
6. The check is also listed under «Skattemeldinger for året» as «Kontrollert,
   ikke sendt» or «Avvist i kontrollen», with links to the documents that were
   checked.

For a dissolved AS's last year the form asks two more things, and the check
may take half a minute:
[File the tax return when the company is dissolved](https://myntos.no/llms/help/tax-return/file-the-tax-return-when-the-company-is-dissolved.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Svar på spørsmålet først.» | Neither «Nei» nor «Ja» chosen on an AS return | Answer the question |
| «Velg næringstype først.» / «Kommunenummer må være fire sifre, f.eks. 0301.» | Missing industry type or invalid 4-digit municipality number on an ENK return | Pick a «Næringstype» and enter a valid 4-digit «Kommunenummer (4 sifre)» (or leave blank if unknown) |
| «logg inn med ID-porten for å kontrollere og sende skattemeldingen» | The ID-porten login ran out | Reload and log in again |
| «Skatteetaten svarte ikke med skattemeldingen for året. Prøv igjen om litt.» | Skatteetaten did not answer, or the person may not file in Altinn | Try again later; check the person's rights in Altinn (or for ENK, that the owner logged in with their own BankID) |
| «Innsending av skattemeldingen er ikke satt opp her. …» | This installation cannot check or file | It cannot be sent from this installation |
| «Året er ikke over. Skattemeldingen kan kontrolleres fra …» | The year has not ended (1 January the year after, or the day after a dissolved AS's last day) | Wait until that day |

## Good to know

- «Skatteetaten fylte inn … verdier selv, som summer og resultater.» is
  expected: Myntos never sends what Skatteetaten computes itself.
- Checking again is always fine; each check is kept.
- For an ENK, Myntos never alters personal items (salary, bank deposits,
  mortgages, private deductions) already in the owner's personal return from
  Skatteetaten.

## Related

- [Send the tax return](https://myntos.no/llms/help/tax-return/send-the-tax-return.md)
- [myntos-skattemelding](https://myntos.no/llms/skills/myntos-skattemelding.md)

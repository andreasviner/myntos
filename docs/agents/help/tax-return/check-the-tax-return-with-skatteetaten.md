---
title: Check the tax return with Skatteetaten
description: How a person has Skatteetaten check an aksjeselskap's tax return (skattemelding) from the Skattemelding page in Myntos - ID-porten login, the one question about shareholders, «Kontroller hos Skatteetaten», and what the answer means. Nothing is filed by the check. Use when someone asks how to check, validate or test their skattemelding before sending it.
pages: /skattemelding/
skill: myntos-skattemelding
---

# Check the tax return with Skatteetaten

The check sends the tax return to Skatteetaten's own validation, which says
whether it would be accepted and computes the tax. **Nothing is filed.** The
answer appears on the page, and the person decides afterwards whether to
send. Myntos fetches the company's skattemelding from Skatteetaten, adds the
formue and the person's answer, and sends it together with the
næringsspesifikasjon.

## They might ask

- «Hvordan kontrollerer jeg skattemeldingen i Myntos?»
- «Hva blir skatten?»
- «Hva betyr spørsmålet om ytelser mellom selskap og aksjonær?»
- "How do I validate my tax return before sending it?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». A «Leser» sees «Det er den som
  kan føre i regnskapet, som kontrollerer og sender skattemeldingen.»
- BankID, Buypass or Commfides for ID-porten, and the right to file the
  skattemelding for the company in Altinn (daglig leder, or someone given the
  right).
- Nothing in red on the page:
  [Fix a tax return that cannot be checked](https://myntos.no/llms/help/tax-return/fix-a-tax-return-that-cannot-be-checked.md).

## Steps

1. Open `https://myntos.no/skattemelding/` and choose the year with «‹» and «›».
2. Under «Kontroll og innsending», press «Logg inn med ID-porten» and log in.
   They come back to the same year.
3. Answer «Skal selskapet rapportere opplysninger om ytelser mellom selskap og
   personlig aksjonær, deltaker eller nærstående til denne?» with «Nei» or
   «Ja». It is Skatteetaten's own question in the skattemelding; benefits are
   loans, pay, rent or gifts between the company and a shareholder who is a
   person, or someone close to them. Myntos cannot see it in the books, so it
   does not guess.
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

For a dissolved company's last year the form asks two more things, and the
check may take half a minute:
[File the tax return when the company is dissolved](https://myntos.no/llms/help/tax-return/file-the-tax-return-when-the-company-is-dissolved.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Svar på spørsmålet først.» | Neither «Nei» nor «Ja» chosen | Answer the question |
| «logg inn med ID-porten for å kontrollere og sende skattemeldingen» | The ID-porten login ran out | Reload and log in again |
| «Skatteetaten svarte ikke med skattemeldingen for året. Prøv igjen om litt.» | Skatteetaten did not answer, or the person may not file for the company in Altinn | Try again later; check the person's rights in Altinn |
| «Innsending av skattemeldingen er ikke satt opp her. …» | This installation cannot check or file | It cannot be sent from this installation |
| «Året er ikke over. Skattemeldingen kan kontrolleres fra …» | The year has not ended (1 January the year after, or the day after a dissolved company's last day) | Wait until that day |

## Good to know

- «Skatteetaten fylte inn … verdier selv, som summer og resultater.» is
  expected: Myntos never sends what Skatteetaten computes itself.
- Checking again is always fine; each check is kept.

## Related

- [Send the tax return](https://myntos.no/llms/help/tax-return/send-the-tax-return.md)
- [myntos-skattemelding](https://myntos.no/llms/skills/myntos-skattemelding.md)

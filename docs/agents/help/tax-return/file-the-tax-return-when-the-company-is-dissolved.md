---
title: File the tax return when the company is dissolved
description: How a person files a dissolved aksjeselskap's last tax return early, as a forhåndsfastsetting, from the Skattemelding page in Myntos - the payout to the shareholders («Utbetalt til aksjonærene ved oppløsningen»), the revisor question for a deleted company, and why the first check can take a minute. Use when someone asks how to send the skattemelding for a company that has been wound up, dissolved or deleted, or asks what a forhåndsfastsetting is.
pages: /skattemelding/
skill: myntos-skattemelding
---

# File the tax return when the company is dissolved

An aksjeselskap that is dissolved files the tax return for its last year
straight away, before the company is deleted, not the spring after. That is
a **forhåndsfastsetting** (skatteforvaltningsloven § 8-2). Skatteetaten may
use daily fines (tvangsmulkt) when it is missing. In Myntos it is the same
page as the ordinary tax return, with two more questions, and Myntos asks
Skatteetaten to prepare the return the first time it is checked.

## They might ask

- «Hvordan sender jeg skattemeldingen for et selskap som er oppløst?»
- «Hva er forhåndsfastsetting?»
- «Selskapet er slettet. Må jeg levere skattemelding?»
- "How do I file the final tax return for a dissolved company?"

## Before they start

- The books end on the company's last day, and that year is closed:
  [End the books when the company is dissolved](https://myntos.no/llms/help/opening-balance-and-year-end/end-the-books-when-the-company-is-dissolved.md).
- Enhetsregisteret has registered the company as dissolved (oppløst) or
  deleted (slettet). Skatteetaten refuses a forhåndsfastsetting before that.
- The role «Medlem», «Administrator» or «Eier», BankID, Buypass or
  Commfides for ID-porten, and the right to file for the company in Altinn
  (daglig leder or styreleder, or someone given the right).
- Any earlier year still not filed must be filed too.

## Steps

1. Open `https://myntos.no/skattemelding/`. It opens on the company's last
   year. The card «Forhåndsfastsetting» at the top says «Selskapet er
   oppløst, og regnskapet sluttet …».
2. Read the figures as for any year. The formue is the values on the last
   day, and the equity shows the payout as «Andre negative endringer i
   egenkapital».
3. Under «Kontroll og innsending», press «Logg inn med ID-porten» and log in.
4. Answer «Skal selskapet rapportere opplysninger om ytelser mellom selskap
   og personlig aksjonær, deltaker eller nærstående til denne?»
5. Check «Utbetalt til aksjonærene ved oppløsningen»: what the shareholders
   were paid in all when the company was dissolved, in whole kroner. Myntos
   fills in what the books show; correct it if it is not right. Write 0 if
   they got nothing.
6. For a company Enhetsregisteret has deleted, answer «Hadde selskapet
   revisor?» with «Nei» or «Ja»: the register no longer shows the roles of a
   deleted company.
7. Press «Kontroller hos Skatteetaten». The page says «Ber Skatteetaten gjøre
   klar skattemeldingen og sender den til kontroll. Det kan ta et halvt
   minutt …».
8. If it says «Skatteetaten gjør klar skattemeldingen for … som
   forhåndsfastsetting. Det tar gjerne et minutt; trykk «Kontroller hos
   Skatteetaten» igjen om litt.», wait a minute and press it again.
9. The answer, and sending it, are as for any year:
   [Send the tax return](https://myntos.no/llms/help/tax-return/send-the-tax-return.md).
   The box to tick says the return is sent «som forhåndsfastsetting» and
   what was paid to the shareholders, and the filing is listed as
   «Forhåndsfastsetting».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Året er ikke over. Skattemeldingen kan kontrolleres fra …» | The last day has not passed | Wait until the day shown |
| A red message about closing the year | The last year is not closed | [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md) |
| «Skriv hva aksjonærene fikk utbetalt, i hele kroner. …» | The payout is empty, has øre, or is not a number | Type whole kroner, for example 29500 |
| «Svar på om selskapet hadde revisor.» | The revisor question is not answered | Answer it |
| «Skatteetaten tar bare imot forhåndsfastsetting for et selskap Enhetsregisteret har registrert som oppløst, …» | Brønnøysund has not registered the dissolution yet | Report the dissolution to Brønnøysundregistrene, and check again when it is registered |
| «Skatteetaten vil ha forhåndsfastsettingen for … i fjorårets skjema, …» | Early in a year, before Skatteetaten has published that year's form | Myntos does not make that one; file it on skatteetaten.no, or check again once the year's form is out |
| «Skatteetaten har gjort klar skattemeldingen for … som forhåndsfastsetting, … men regnskapet i Myntos løper til 31. desember.» | The books do not end at the dissolution in Myntos | End the books on the last day first |

## Good to know

- **What is sent:** the næringsspesifikasjon for the period up to the last
  day, and the company's skattemelding marked as fully wound up in the year
  (`erFulltAvvikletIInntektsaaret`), with the payout to the shareholders.
  Each shareholder's gain or loss on the shares is worked out from that
  payout.
- **Sending cannot be withdrawn**, only replaced by a newer one for the same
  year, and after it is sent the books' last day can no longer be undone in
  Myntos.
- **The shareholders' own tax returns** are theirs: the loss or gain on the
  shares belongs in each shareholder's skattemelding.

## Related

- [End the books when the company is dissolved](https://myntos.no/llms/help/opening-balance-and-year-end/end-the-books-when-the-company-is-dissolved.md)
- [Check the tax return with Skatteetaten](https://myntos.no/llms/help/tax-return/check-the-tax-return-with-skatteetaten.md)
- [Send the tax return](https://myntos.no/llms/help/tax-return/send-the-tax-return.md)
- An agent with a key can check and file it too, each time only after the
  person approves it on «Godkjenning» with their ID-porten login:
  [myntos-skattemelding](https://myntos.no/llms/skills/myntos-skattemelding.md)

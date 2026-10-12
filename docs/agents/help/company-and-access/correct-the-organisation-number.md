---
title: Correct the organisation number
description: How an administrator corrects a mistyped organisasjonsnummer (orgnr) on the company page in Myntos, and why it cannot be changed once the company is confirmed in Altinn. Use when someone typed the wrong orgnr when setting up their books.
pages: /organisasjon/
skill: myntos-selskap
---

# Correct the organisation number

The organisasjonsnummer (orgnr) is the nine-digit number every Norwegian
business has in Enhetsregisteret. A typo in it can be corrected for as long as
the company is unconfirmed («Ubekreftet»). Once someone has confirmed the
company through Altinn, the number is settled: a different number is a
different company, and it needs its own set of books.

## They might ask

- «Jeg skrev feil organisasjonsnummer da jeg satte opp regnskapet»
- «Hvordan endrer jeg orgnr i Myntos?»
- "I typed the wrong organisation number in Myntos"

## Before they start

- The role «Administrator» or «Eier» in this company.
- The company is not yet confirmed: the line «Organisasjonsnummer» has a
  pencil, not the word «Bekreftet».

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. In «Selskapet», press the pencil at the end of «Organisasjonsnummer»
   («Endre organisasjonsnummer»).
3. Type the right nine digits. Spaces are fine.
4. Press «Lagre». The card says «Lagret.», and Myntos fetches what
   Enhetsregisteret holds for the new number: address, organisasjonsform and
   the rest are replaced, because the old ones belonged to the other number.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Bekreftet» at the end of the line, and no pencil | The company is confirmed in Altinn, so the number is locked | It cannot be changed. If these books are really for another company, set up a new one: [Add another company](https://myntos.no/llms/help/company-and-access/add-another-company.md) |
| «Organisasjonsnummeret må være ni siffer.» | Fewer or more than nine digits | Check the number against Enhetsregisteret |
| «organisasjonsnummeret har feil kontrollsiffer» | The last digit does not match the other eight, which means a typo | Check the number again |
| «organisasjonsnummeret kan ikke endres etter at eierskapet er bekreftet i Altinn. …» | Someone confirmed the company while the line was open | Reload the page; the number is now locked |
| «organisasjonen fører allerede regnskap for …» | There is already a set of books for that number | Use those books instead; switch to them in the company menu |
| No pencil and no «Bekreftet» | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |

## Good to know

- The name is not changed with the number. Check «Navn» afterwards and correct
  it with its own pencil if it belonged to the old number.
- Confirm the company once the number is right, so it is settled before the
  first filing:
  [Confirm the company with ID-porten](https://myntos.no/llms/help/company-and-access/confirm-the-company.md).

## Related

- [Edit the company's details](https://myntos.no/llms/help/company-and-access/edit-company-details.md)
- [Confirm the company with ID-porten](https://myntos.no/llms/help/company-and-access/confirm-the-company.md)
- An agent with a key can look up a number in Enhetsregisteret but cannot
  change it: [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)

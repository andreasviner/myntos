---
title: Log in with ID-porten to file the VAT return
description: Why and how a person logs in with ID-porten (BankID, Buypass or Commfides) on the Mva-melding page in Myntos before the VAT return can be checked and sent, and what to do when the login does not take. Use when someone asks why Myntos wants an ID-porten or BankID login for the mva-melding, or why the button is still there after logging in.
pages: /mva/
skill: myntos-mva
---

# Log in with ID-porten to file the VAT return

Skatteetaten only accepts an MVA-melding from a person who has shown who they
are through ID-porten, and checks in Altinn that this person may report VAT
for the company. So before Myntos can check or send the melding, the person
logs in with ID-porten on the page. This is separate from logging in to
Myntos, and nobody else (no colleague, no AI) can do it for them.

## They might ask

- «Hvorfor må jeg logge inn med ID-porten for å sende mva-meldingen?»
- «Jeg logget inn med BankID, men knappen er der fortsatt»
- «Kan regnskapsføreren min sende mva-meldingen?»
- "Why does Myntos ask for BankID to file the VAT return?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». A «Leser» sees no button.
- BankID, Buypass or Commfides. MinID is not enough: Skatteetaten needs the
  high security level.
- The right to report VAT for the company in Altinn: the daglig leder usually
  has it; anyone else needs it given to them in Altinn by the company.

## Steps

1. Open `https://myntos.no/mva/` and click «Se meldingen» on the termin to
   send.
2. Under the totals, press «Logg inn med ID-porten».
3. Log in with BankID, Buypass or Commfides at ID-porten, within ten minutes.
4. ID-porten sends them back to the same termin on the MVA page. The button
   has been replaced by the box «Merknad til Skatteetaten» and the button
   «Kontroller hos Skatteetaten».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Logg inn med ID-porten» button, and red lines above the table | Something stops the melding being sent (period not over, not VAT-registered, a code that cannot be reported) | [Find out why the VAT return cannot be sent](https://myntos.no/llms/help/vat/find-out-why-the-vat-return-cannot-be-sent.md) |
| «Innsending fra Myntos er ikke satt opp her.» | This installation cannot file | [File the VAT return by uploading the XML](https://myntos.no/llms/help/vat/file-the-vat-return-by-uploading-the-xml.md) |
| Back on the page, and the button is still there | The login did not go through: cancelled, MinID, or more than ten minutes | Press it again and log in with BankID, Buypass or Commfides |
| «logg inn med ID-porten for å kontrollere og sende mva-meldingen» or «innloggingen i ID-porten må gjøres på nytt» | The login has run out (it lasts well under an hour) | Reload the page and log in again |

## Good to know

- The login lasts a short while, set by ID-porten. Log in when ready to check
  and send, not in advance.
- The same login is used to fetch the receipt afterwards, so it is not used
  up by sending.
- Skatteetaten itself checks whether the person may report for the company.
  If they may not, the check or the sending is refused there.

## Related

- [Check the VAT return with Skatteetaten](https://myntos.no/llms/help/vat/check-the-vat-return-with-skatteetaten.md)
- [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md)
- No key can log in to ID-porten for the person:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)

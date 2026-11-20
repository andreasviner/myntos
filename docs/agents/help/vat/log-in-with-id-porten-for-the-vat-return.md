---
title: Log in with ID-porten to file the VAT return
description: Why a person logs in with ID-porten (BankID, Buypass or Commfides) to check, send or fetch the receipt for the VAT return on the Mva-melding page in Myntos, how pressing the button takes them through ID-porten and carries on, and what to do when the login does not take. Use when someone asks why Myntos sends them to ID-porten or BankID for the mva-melding, or why nothing happened after logging in.
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
- «Hvorfor ble jeg sendt til BankID da jeg trykket Kontroller?»
- «Kan regnskapsføreren min sende mva-meldingen?»
- "Why does Myntos ask for BankID to file the VAT return?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». A «Leser» sees no buttons.
- BankID, Buypass or Commfides. MinID is not enough: Skatteetaten needs the
  high security level.
- The right to report VAT for the company in Altinn: the daglig leder usually
  has it; anyone else needs it given to them in Altinn by the company.

## Steps

There is no separate login button. The login happens on the way, when they
press a button that needs it.

1. Open `https://myntos.no/mva/` and click «Se meldingen» on the termin.
2. Press the button for what they want done: «Kontroller hos Skatteetaten»,
   «Send inn mva-meldingen (…)» or «Hent kvittering». Under
   «Kontroller hos Skatteetaten» the page says «Du logger inn med ID-porten
   først, og kontrollen fortsetter når du er tilbake.» when they are not
   logged in.
3. ID-porten opens. Log in with BankID, Buypass or Commfides within ten
   minutes. If they logged in with ID-porten a short while ago, it may go
   straight through without asking.
4. ID-porten sends them back to the same termin, and the press carries on by
   itself: the check runs, the melding is sent, or the receipt is fetched.
   They do not press again.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Kontroller hos Skatteetaten», and red lines above the table | Something stops the melding being sent (period not over, not VAT-registered, a code that cannot be reported) | [Find out why the VAT return cannot be sent](https://myntos.no/llms/help/vat/find-out-why-the-vat-return-cannot-be-sent.md) |
| «Meldingen for terminen er sendt. …» instead of the button | The termin is sent and the books still say the same | Nothing to do; see [Correct a VAT return already sent](https://myntos.no/llms/help/vat/correct-a-vat-return-already-sent.md) |
| «Innsending fra Myntos er ikke satt opp her.» | This installation cannot file | [File the VAT return by uploading the XML](https://myntos.no/llms/help/vat/file-the-vat-return-by-uploading-the-xml.md) |
| «Innloggingen ble avbrutt.», «Innloggingen tok for lang tid. Prøv en gang til.» or «Innloggingen ga ikke høyt nok sikkerhetsnivå. …» | The login did not go through: cancelled, more than ten minutes, or MinID | Press the button again and log in with BankID, Buypass or Commfides |
| Back on the page and nothing happened | The browser does not keep what was pressed (some private windows), or more than ten minutes passed | Press the button again; they are logged in now |
| «Du er logget inn, men meldingen ble ikke sendt: den står ikke lenger klar. Se over terminen.» | While they were at ID-porten, the books changed or the melding was sent from another tab | Read the termin again, check again if needed, then send |
| «logg inn med ID-porten for å kontrollere og sende mva-meldingen» or «innloggingen i ID-porten må gjøres på nytt» | They came back from ID-porten, but Skatteetaten still did not take the login | Reload the page and press the button again |

## Good to know

- The login lasts a short while, set by ID-porten. When it runs out, the next
  press goes through ID-porten again by itself.
- What was pressed is kept in that browser tab only, for ten minutes, and
  carried out once. Sending still needs the box they ticked: the melding and
  the amount have to be the same ones when they come back, or nothing is sent.
- The same login is used to fetch the receipt afterwards, so it is not used
  up by sending.
- Skatteetaten itself checks whether the person may report for the company.
  If they may not, the check or the sending is refused there.

## Related

- [Check the VAT return with Skatteetaten](https://myntos.no/llms/help/vat/check-the-vat-return-with-skatteetaten.md)
- [Send the MVA-melding](https://myntos.no/llms/help/vat/send-the-vat-return.md)
- No key can log in to ID-porten for the person:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)

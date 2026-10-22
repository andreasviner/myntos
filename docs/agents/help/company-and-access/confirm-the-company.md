---
title: Confirm the company with ID-porten
description: How an administrator confirms through ID-porten and Altinn that a company in Myntos really is theirs, which removes the «Ubekreftet» mark and the yellow warning. Use when someone asks what «Ubekreftet» or «Selskapet ditt er ikke bekreftet hos oss» means, or how to get rid of it.
pages: /organisasjon/
skill: myntos-selskap
---

# Confirm the company with ID-porten

Anyone can type nine digits into Myntos, so a new set of books starts out
unconfirmed («Ubekreftet»). Confirming means the person logs in with
ID-porten (BankID or similar) and Myntos asks Altinn which companies they may
act for; if this company is on Altinn's list, it is confirmed. The books work
the same either way: payments, invoices and everything else can be done
before it. What it settles is whose company this is, and it should be done
before the first filing to Skatteetaten.

## They might ask

- «Hva betyr "Ubekreftet" ved selskapet mitt i Myntos?»
- «Hvordan bekrefter jeg selskapet?»
- «Hvordan blir jeg kvitt det gule varselet om at selskapet ikke er bekreftet?»
- "What does 'Ubekreftet' mean in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company. Others are told «Be en
  administrator for dette regnskapet om å gjøre det.»
- The person must have rights for the company in Altinn: typically the daglig
  leder, the chair of the board, the owner of a sole proprietorship, or
  someone they have given rights to in Altinn.
- BankID, Buypass or Commfides. MinID is not enough.

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
   The small dot next to the company's name at the top, and the word
   «ubekreftet» next to the name on the overview, lead there too.
2. At the top of the page, in the yellow box «Selskapet ditt er ikke bekreftet
   hos oss», press «Bekreft selskapet».
3. If they have not logged in with ID-porten in the last hour, Myntos sends
   them to ID-porten. They log in with BankID (or Buypass or Commfides).
4. Back in Myntos, the confirmation continues by itself: the box says «Spør
   Altinn om rollen din …».
5. If somebody else has already confirmed the same company in their own
   Myntos, a window opens first: «Selskapet er allerede registrert», «… er
   allerede registrert og bekreftet i et annet regnskap i Myntos. Er du sikker
   på at du vil fortsette?». «Ja» confirms these books too; «Avbryt» leaves
   them unconfirmed, and nothing else changes. See Good to know before
   pressing «Ja».
6. When it works, the yellow box disappears, the card says «Eierskapet for …
   er bekreftet i Altinn.», and the mark beside the heading «Selskapet» says
   «Bekreftet».

The same can be done further down, in the «Selskapet» card below the bank
accounts: «Logg inn med ID-porten», and then «Bekreft eierskap i Altinn».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Altinn viser ikke at du kan opptre for dette selskapet. Rettighetene tildeles i Altinn av den som er registrert for selskapet.» | Altinn does not list this person for this organisation number | Ask whoever is registered for the company (daglig leder, chair) to give them rights in Altinn, or to confirm the company themselves. Also check the organisation number is right |
| The window «Selskapet er allerede registrert» | Someone else has already confirmed the same company in their own Myntos. Myntos does not say who | «Ja» confirms these books as well. Often better: «Avbryt», and ask whoever keeps the other books for an invitation. An accountant keeping their own copy can also just leave these unconfirmed |
| «Innloggingen ga ikke høyt nok sikkerhetsnivå. Logg inn med BankID, Buypass eller Commfides i stedet for MinID.» | They logged in with MinID | Press the button again and choose BankID |
| «Innloggingen ble avbrutt.» | The ID-porten login was cancelled | Press the button again |
| «Innloggingen kunne ikke starte. Det er en feil hos oss, ikke hos deg. Vi har fått beskjed.» | A fault on Myntos's side | Nothing for them to do; try again later |
| «innloggingen i ID-porten må gjøres på nytt» | The ID-porten login ran out | Press the button again and log in again |
| No button, and «Be en administrator for dette regnskapet om å gjøre det.» | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |

## Good to know

- Once confirmed, the organisation number can no longer be changed:
  [Correct the organisation number](https://myntos.no/llms/help/company-and-access/correct-the-organisation-number.md).
- More than one set of books can be confirmed for the same company, but only
  after the window above has asked. Both can then send the MVA-melding and
  the skattemelding for the company, and Skatteetaten treats a later filing
  as a correction of the earlier one, so the people keeping the two sets
  should know about each other.
- Confirming also fetches the company's details from Enhetsregisteret again.
- Logging in with ID-porten here also links the person's identity, so the
  board list can say «Har tilgang» about them:
  [Link your ID-porten login](https://myntos.no/llms/help/company-and-access/link-your-id-porten-login.md).
- An AI agent with a key cannot confirm the company: it needs the person's
  own ID-porten login.

## Related

- [Link your ID-porten login](https://myntos.no/llms/help/company-and-access/link-your-id-porten-login.md)
- [See what is registered about the company](https://myntos.no/llms/help/company-and-access/see-company-details.md)
- An agent with a key can read whether the company is confirmed (`verified`),
  but not confirm it: [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)

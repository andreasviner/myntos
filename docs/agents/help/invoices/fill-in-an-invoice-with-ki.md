---
title: Let KI fill in the invoice from the customer's e-mail
description: How a person pastes the customer's e-mail with invoice details, or a sentence about the sale, into the box at the top of the Faktura page in Myntos and gets the buyer, the dates, the reference and the lines filled in by KI. Use when someone asks whether Myntos can fill in an invoice from an e-mail, read the customer's invoice address for them, or where the KI box on the invoice is.
pages: /faktura/
skill: myntos-faktura
---

# Let KI fill in the invoice from the customer's e-mail

At the top of the invoice form there is a box for whatever text the person
has: usually the e-mail in which the customer sent their invoice details
(name, invoice address, organisation number, what to mark the invoice with),
or a sentence about the sale, or both. Myntos's KI reads it and fills in
the form: the buyer, «Leveringsdato», «Leveringssted», «Forfallsdato», «Deres
referanse» and, if the text says what was sold, the lines. It only fills in
the form. Nothing is saved or issued until the person has checked it and
issued the invoice themselves.

It fills in only what the text says. A name, an address or a reference is
kept only if the same words are in the text, and a date only if the text
names that day. The organisation number is read from the text by its check
digit. If the buyer is already a registered customer, Myntos chooses them in
«Kunde» itself, from the company's own customer list.

## They might ask

- «Kan Myntos fylle ut fakturaen fra e-posten fra kunden?»
- «Kunden sendte fakturaadressen på e-post, må jeg skrive den inn selv?»
- «Hvor er KI-feltet på fakturaen?»
- "Can Myntos fill in an invoice from the customer's e-mail?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- KI switched on for the company. It is on unless someone ticked «Slå av KI
  for …» on the «KI» page (`https://myntos.no/ki/`).

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`). The box
   «Lim inn fakturaopplysningene fra kunden, eller beskriv salget» is the
   first thing under «Ny faktura».
2. Paste the customer's e-mail, greeting and signature included, or write
   what was sold, for example «Faktura til Bygg AS for 10 timer rådgivning
   levert 14. oktober, 1 500 kroner timen. Deres ref: Ola Nordmann».
3. Press «Fyll ut fakturaen». The page says «Fyller ut fakturaen …».
4. Every field it filled in is tinted and marked «KI» after its label, and a
   message under the box says what it filled in, such as «Fylte inn kjøperen
   og referansen.». The mark goes away from a field when they change it.
   Lines it added are marked «KI» the same way.
5. If the text had no lines in it, as a customer's e-mail usually does,
   describe the sale in the second box, under «Linjer»:
   [Let KI write the lines](https://myntos.no/llms/help/invoices/add-lines-with-ki.md).
   Or type the lines.
6. Check everything marked «KI», then carry on:
   [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No box at the top | KI is switched off for the company, Myntos has no model available, or their role is «Leser» | Fill in the form by hand, or have KI switched on again on the «KI» page |
| «Kjøperen sto allerede i skjemaet, så den er ikke endret.» | A customer was chosen, or part of a buyer was typed, before they pressed the button. The KI never changes a buyer someone wrote | Set «Kunde» back to «Skriv inn kjøperen selv» and clear the buyer fields, then press «Fyll ut fakturaen» again |
| «Kunden er satt til «…», fordi det er den eneste kunden som passer til «…». Kontroller at det er riktig kunde.» | The text named the customer by part of their name | Check «Kunde», and choose another if it is wrong |
| «Flere kunder passer til «…»: …» | More than one registered customer fits the name | Choose the right one in «Kunde» |
| «Teksten har en annen fakturaadresse enn kunden har i registeret: …» | The customer is registered with another address than the e-mail gives. The invoice uses the registered one | Correct the customer: [Correct a customer's or supplier's orgnr or address](https://myntos.no/llms/help/customers-and-suppliers/correct-an-orgnr-or-address.md) |
| «Teksten sier at fakturaen skal sendes til …» | The e-mail gives an address for the invoice. Myntos does not send invoices yet | After issuing, download the PDF and send it there: [Send an invoice to the customer](https://myntos.no/llms/help/invoices/send-an-invoice-to-the-customer.md) |
| «Teksten sier ikke hva som er solgt, så fakturaen har ingen linjer ennå.» | Normal for a customer's e-mail | Describe the sale under «Linjer», or type the lines |
| «Vi fant verken kjøper eller fakturalinjer i teksten. …» | Nothing in the text could be used | Paste the part with the invoice details, or fill in the form by hand |
| «Vi fikk ikke svar fra Løynd nå. Fyll ut selv, eller prøv igjen.» | The model did not answer | Try again in a moment, or fill in the form |

## Good to know

- The text goes to Løynd, the model Myntos uses, which runs in Norway. What
  it sees is explained on the «KI» page (`https://myntos.no/ki/`). The
  company's customer list is not sent to it.
- A date or a place the form filled in by itself (today, 14 days ahead, the
  company's town) is replaced if the text names another. Anything they typed
  is never replaced.
- With an organisation number, Myntos also looks the buyer up in
  Enhetsregisteret as if it were typed, and says under the number if the
  company is deleted or bankrupt. The address from the e-mail is kept.
- The person issuing the invoice is responsible for what it says, whoever
  filled it in.

## Related

- [Choose who the invoice is to](https://myntos.no/llms/help/invoices/choose-who-the-invoice-is-to.md)
- [Let KI write the lines](https://myntos.no/llms/help/invoices/add-lines-with-ki.md)
- An agent with a key can ask for the same proposal through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)

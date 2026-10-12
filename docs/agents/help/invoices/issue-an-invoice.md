---
title: Issue an invoice
description: How a person writes, checks and issues a sales invoice (faktura) on the Faktura page in Myntos, from the empty form to the PDF. Use when someone asks how to invoice or bill a customer, or how to send an invoice in Myntos.
pages: /faktura/
skill: myntos-faktura
---

# Issue an invoice

An invoice is written in one form, looked over in a review, and then issued
with one button. Until it is issued it is a draft: it has no number, is not in
the books, and can be changed or deleted. **Issuing is final.** The invoice
gets the next number in an unbroken series, is posted to the books, and can
afterwards only be corrected with a credit note (kreditnota). Myntos does not
send the invoice to the customer; the person downloads it and sends it.

## They might ask

- «Hvordan sender jeg en faktura i Myntos?»
- «Hvordan fakturerer jeg en kunde?»
- «Hvordan lager jeg en faktura?»
- "How do I bill a customer in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». A «Leser» sees the invoices
  but not the form.
- A kontonummer on the company. Without one, the page says so at the top and
  no invoice can be issued:
  [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md).
- A customer under «Kunder og leverandører» helps but is not required:
  [Choose who the invoice is to](https://myntos.no/llms/help/invoices/choose-who-the-invoice-is-to.md).

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`). The form
   «Ny faktura» is on the left, the list of invoices on the right.
2. Under «Kjøper», pick the customer in «Kunde», or leave it at «Skriv inn
   kjøperen selv» and fill in «Organisasjonsnummer», «Navn», «Adresse»,
   «Postnummer» and «Poststed». Untick «Kjøperen er bokføringspliktig
   (næringsdrivende)» if the buyer is a private person.
3. Under «Levering og betaling», check «Leveringsdato» (today is filled in),
   «Forfallsdato» (14 days ahead is filled in) and «Leveringssted» (the
   company's own town is filled in). Fill in «Deres referanse» if the customer
   gave one; public sector customers require it.
4. Under «Linjer», describe each thing sold: what it is («Hva er solgt?»),
   «Antall», the unit, «Pris» **without VAT**, the VAT rate and «Rabatt %».
   «Legg til linje» adds another line, «Fjern» removes one. The sums «Sum eks.
   mva», «Merverdiavgift» and «Å betale» update as they type.
5. Press «Se over fakturaen». The draft is saved and the review opens. It
   shows what the customer will receive («Kjøper», «Levert», «Forfall», «Å
   betale», «Betales til», «KID» and the rest) and anything still missing.
   «Se utkastet som PDF» shows the invoice exactly as it will look, marked as a
   draft.
6. If it says «… ting må rettes først», press «Tilbake og endre», fix what is
   listed, and press «Se over fakturaen» again:
   [Fix an invoice that cannot be issued](https://myntos.no/llms/help/invoices/fix-an-invoice-that-cannot-be-issued.md).
7. When nothing blocks it, read it through and press «Utsted fakturaen». This
   is the step that cannot be undone; there is no extra confirmation, because
   the review is the confirmation.
8. The card «Faktura … er utstedt» shows the amount, due date and KID. Press
   «Last ned PDF» (and «Last ned EHF» for a business customer) and send the
   file to the customer:
   [Send an invoice to the customer](https://myntos.no/llms/help/invoices/send-an-invoice-to-the-customer.md).
   «Skriv en ny faktura» starts the next one.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Du har lesetilgang» and no form | Their role is «Leser» | Ask an «Administrator» or «Eier» for a role that may write |
| «Selskapet har ikke noe kontonummer, så ingen faktura kan utstedes.» | The company has no bank account number | An «Administrator» or «Eier» adds one: [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md) |
| «Utsted fakturaen» is greyed out | The review found something that must be fixed | [Fix an invoice that cannot be issued](https://myntos.no/llms/help/invoices/fix-an-invoice-that-cannot-be-issued.md) |
| «Vi fikk ikke lagret.» or another message under the form | The draft could not be saved, often a line with a number that is not a number | Check «Antall», «Pris» and «Rabatt %» on every line, and try again |

## Good to know

- Bokføringsforskriften § 5-1 (Norwegian rules for what an invoice must say)
  requires the buyer's name with an address or organisation number, what was
  sold and how much, the delivery date and place, a due date and the VAT. The
  review checks all of it before the invoice is issued.
- The number is given at issue, not before, so a draft that is deleted leaves
  no gap: [How invoice numbers work](https://myntos.no/llms/help/invoices/understand-invoice-numbers.md).
- After issue the PDF is filed in the bilag archive and kept for five years,
  as the law requires.
- A wrong invoice is never edited: [Correct an issued invoice](https://myntos.no/llms/help/invoices/correct-an-issued-invoice.md).

## Related

- [Let KI write the lines](https://myntos.no/llms/help/invoices/add-lines-with-ki.md)
- [Copy the lines from a previous invoice](https://myntos.no/llms/help/invoices/copy-lines-from-a-previous-invoice.md)
- [Choose the VAT rate on a line](https://myntos.no/llms/help/invoices/choose-the-vat-rate-on-a-line.md)
- [Use a KID on an invoice](https://myntos.no/llms/help/invoices/use-a-kid-on-an-invoice.md)
- An agent with a key can do this through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)

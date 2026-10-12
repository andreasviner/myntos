---
title: Choose the VAT rate on a line
description: How a person picks the VAT (mva) rate for each invoice line on the Faktura page in Myntos, what each choice means, why prices are entered without VAT, and what reverse charge (omvendt avgiftsplikt) does. Use when someone asks which VAT rate to use on an invoice, or why their invoice shows no VAT.
pages: /faktura/
skill: myntos-faktura
---

# Choose the VAT rate on a line

Every invoice line has its own VAT rate, chosen from a list next to the price.
The rate decides how much VAT is added and which income account the sale is
posted to, which in turn decides where it lands in the tax return. The price
on the line is always **without VAT**; Myntos adds the VAT and shows it at the
bottom.

## They might ask

- «Hvilken mva-sats skal jeg bruke på fakturaen?»
- «Hvorfor står det ingen mva på fakturaen min?»
- «Skal prisen være med eller uten mva?»
- «Hva er omvendt avgiftsplikt?»
- "Which VAT rate do I use on a Norwegian invoice?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».

## Steps

1. Open «Faktura» in the top menu (`https://myntos.no/faktura/`).
2. On each line under «Linjer», type «Pris» as the price per unit before VAT.
3. Choose the VAT rate in the list on the same line. Hovering over a choice
   shows a short explanation:
   - «25 % (alminnelig sats)»: ordinary goods and services.
   - «15 % (næringsmidler)»: food and drink.
   - «12 % (persontransport, overnatting)»: passenger transport, hotels,
     cinema, museums and sports events.
   - «0 % (avgiftsfritt salg)»: sales exempt under chapter 6 of the VAT act,
     such as books and newspapers.
   - «Unntatt merverdiavgift»: outside the VAT act, such as health care,
     education and financial services.
   - «Utførsel til utlandet»: export.
   - «Ikke mva-registrert»: for a business that is not VAT registered.
4. Check «Merverdiavgift» and «Å betale» under the lines.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| Only «Ikke mva-registrert» in the list, and a note that the company is not registered in Merverdiavgiftsregisteret | The company is not VAT registered, so it may not charge VAT | If it has become registered, change it on the company page: [Set the VAT registration](https://myntos.no/llms/help/company-and-access/set-vat-registration.md) |
| «omsetning utenfor merverdiavgiftsloven kan ikke stå på samme faktura som avgiftspliktig omsetning. Skriv to fakturaer» | «Unntatt merverdiavgift» is mixed with lines that carry VAT | Write two invoices, one for each |
| «selskapet er ikke registrert i Merverdiavgiftsregisteret og kan ikke oppgi merverdiavgift på salgsdokumentet» | A line has a VAT rate, but the company is not registered | Use «Ikke mva-registrert» |

## Good to know

- «Omvendt avgiftsplikt (kjøper beregner mva)» under «Levering og betaling»
  takes the VAT off every line and prints «Omvendt avgiftsplikt –
  Merverdiavgift ikke beregnet» on the invoice. Use it only when the rules say
  the buyer accounts for the VAT; the review warns if lines with a VAT rate
  are affected.
- The rate that applies is the one in force on the delivery date.
- VAT charged when it should not have been is still owed to the state.
  If unsure which rate applies, ask an accountant or Skatteetaten before
  issuing; an issued invoice can only be corrected with a credit note.

## Related

- [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md)
- An agent with a key can set VAT rates on lines through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)

---
title: Fix an invoice that cannot be issued
description: What each finding in the invoice review on the Faktura page in Myntos means and how to fix it, starting with the missing kontonummer (bank account number) that stops every invoice. Use when someone says «Utsted fakturaen» is greyed out, the review says something must be fixed first, or Myntos says the company has no account number.
pages: /faktura/
skill: myntos-faktura
---

# Fix an invoice that cannot be issued

Before an invoice is issued, the review «Se over fakturaen» checks it against
the Norwegian rules for what a sales document must contain
(bokføringsforskriften § 5-1). Anything that would make it unlawful is listed
under «… ting må rettes først», and «Utsted fakturaen» stays greyed out until
it is fixed. Findings under «… ting er verdt å vite» are only warnings; the
invoice can be issued anyway. The check is strict on purpose: an issued
invoice cannot be edited, only credited and written again.

## They might ask

- «Hvorfor kan jeg ikke utstede fakturaen?»
- «Det står at selskapet ikke har noe kontonummer»
- «Utsted-knappen er grå»
- «Hva betyr "leveringsstedet mangler"?»
- "Why can't I issue my invoice in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier». Some fixes are on the company
  page and need «Administrator» or «Eier»; the table says which.

## Steps

1. In the review, read the list under «… ting må rettes først». Each finding
   names the rule it comes from in small text below it.
2. Fix each one as the table below says. For anything on the invoice itself,
   press «Tilbake og endre», change it in the form, and press «Se over
   fakturaen» again.
3. When the list has nothing that must be fixed, «Utsted fakturaen» can be
   pressed. The draft is kept the whole time; nothing is lost by going back.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Selskapet har ikke noe kontonummer, så ingen faktura kan utstedes.» at the top of the page, or «selskapet har ikke noe kontonummer, …» in the review | The company has no bank account number, so the invoice would not say where to pay | An «Administrator» or «Eier» adds it on the company page: [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md). Then reload the «Faktura» page, open the draft with «Endre» and press «Se over fakturaen» again |
| «kjøperens navn mangler» | No buyer name | Fill in «Navn» under «Kjøper», or choose a customer |
| «kjøperen må identifiseres med adresse eller organisasjonsnummer, navnet alene er ikke nok» | Only a name | Fill in «Adresse» and «Postnummer», or «Organisasjonsnummer» |
| «leveringstidspunktet mangler» | No delivery date | Fill in «Leveringsdato» |
| «leveringsstedet mangler» | No delivery place | Fill in «Leveringssted», where the goods or work were delivered |
| «forfallsdato mangler» | No due date | Fill in «Forfallsdato» |
| «salgsdokumentet må beskrive hva som er solgt; det har ingen linjer», or «linje … mangler beskrivelse av ytelsens art» | No lines, or a line without a description | Add the line, or describe it in «Hva er solgt?» |
| «salgsdokumentet har ikke noe vederlag» | The total is zero | Check «Pris» on the lines |
| «selskapet er ikke registrert i Merverdiavgiftsregisteret og kan ikke oppgi merverdiavgift på salgsdokumentet» | VAT on a line, but the company is not VAT registered | Choose «Ikke mva-registrert», or [set the VAT registration](https://myntos.no/llms/help/company-and-access/set-vat-registration.md) if the company is registered |
| «omsetning utenfor merverdiavgiftsloven kan ikke stå på samme faktura som avgiftspliktig omsetning. Skriv to fakturaer» | «Unntatt merverdiavgift» mixed with lines that carry VAT | Split it into two invoices |
| «kjøperen er bokføringspliktig, og da skal salgsdokumentet sendes som elektronisk faktura. …» | A business buyer without an organisation number. A warning until 1 January 2027, then a stop | Fill in «Organisasjonsnummer», or untick «Kjøperen er bokføringspliktig (næringsdrivende)» if it is a private person |
| «foretak registrert i Foretaksregisteret må oppgi hovedkontorets adresse …», or «… da skal ordet «Foretaksregisteret» stå på salgsdokumentet. …» | The company's own address or Foretaksregisteret status is missing | An «Administrator» or «Eier» fixes it on the company page: [Edit the company's details](https://myntos.no/llms/help/company-and-access/edit-company-details.md) |

## Good to know

- Warnings worth reading before issuing: «salgsdokumentet skulle vært utstedt
  senest en måned etter levering …» (the invoice is late, issue it anyway),
  «forfallsdatoen … har allerede vært …» (check the due date), and a KID whose
  check digit does not add up.
- The bank account number is not a legal requirement, but Myntos stops on it
  anyway: an invoice without one tells the customer what they owe and not
  where to pay, and could only be fixed by crediting it.
- A registered customer missing both address and organisation number shows a
  warning under «Kunde» already in the form.

## Related

- [Issue an invoice](https://myntos.no/llms/help/invoices/issue-an-invoice.md)
- [Change a draft invoice](https://myntos.no/llms/help/invoices/change-a-draft-invoice.md)
- An agent with a key can read the same review through the API:
  [myntos-faktura](https://myntos.no/llms/skills/myntos-faktura.md)

---
title: Reverse a VAT adjustment
description: How a person undoes an uttak, tap på krav, justering or tilbakeføring recorded by mistake in Myntos, with «Reverser» on the page «Uttak og justering», and what that does to the MVA-melding. Use when someone asks how to delete, undo or correct a VAT adjustment (mva-justering) in Myntos.
pages: /mva-justeringer/
skill: myntos-mva-justering
---

# Reverse a VAT adjustment

A posted entry is never deleted (bokføringsloven). A VAT adjustment recorded
by mistake is undone by a reversal: a new voucher, its mirror image, dated
like the original so the two cancel in the same termin's melding. Both stay
in the books. To replace it with the right one, reverse it and record the
right one.

## They might ask

- «Jeg førte feil mva-justering. Hvordan sletter jeg den?»
- «Kan jeg angre et uttak i Myntos?»
- «Kunden betalte likevel, hvordan reverserer jeg tap på krav?»
- "How do I undo a VAT adjustment in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The year of the entry is not closed.

## Steps

1. Open `https://myntos.no/mva-justeringer/`. Use ‹ and › at the top right to
   go to the right year.
2. In the table, find the entry by «Bilag», «Dato» and «Hva».
3. Press «Reverser» at the end of its row.
4. Confirm the question «Reversere bilag …? Begge står igjen i regnskapet.»
   This cannot be undone.
5. A new row appears with «Reversering:» in front of the kind, and the
   original no longer has a «Reverser» button.
6. If something should have been recorded instead, record it now:
   [Record private use (uttak)](https://myntos.no/llms/help/vat/record-private-use-uttak.md),
   [Claim back VAT on a customer who never paid](https://myntos.no/llms/help/vat/claim-back-vat-on-a-customer-who-never-paid.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Reverser» button | Their role is «Leser», the entry is already reversed, or the row is itself a reversal | Ask a «Medlem» or above; a reversal is not reversed, record the entry again instead |
| «bilaget er allerede reversert» | It was reversed before | Nothing to do |
| «regnskapsåret … er avsluttet» | The entry's year is closed | An administrator must reopen the year under «Årsavslutning» first |

## Good to know

- The reversal lands in the same termin as the original. If that termin is
  already sent, the MVA page marks it «Endret etter innsending», and a new
  melding is due:
  [Correct a VAT return already sent](https://myntos.no/llms/help/vat/correct-a-vat-return-already-sent.md).
- Payments and invoices are not reversed here; they have their own pages.

## Related

- [See what is owed for a VAT period](https://myntos.no/llms/help/vat/see-what-is-owed-for-a-termin.md)
- An agent with a key can reverse it through the API:
  [myntos-mva-justering](https://myntos.no/llms/skills/myntos-mva-justering.md)

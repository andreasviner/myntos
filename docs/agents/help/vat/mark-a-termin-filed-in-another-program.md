---
title: Mark a VAT period as filed from another program
description: How a person records on the Mva-melding page in Myntos that a VAT period (termin) was already filed from the program they used before (Tripletex, Fiken, Conta …), so Myntos stops calling it overdue and does not send a second melding that would replace it; how to compare the amounts, and how to undo it. Use when someone moved to Myntos during the year, runs two programs side by side, or sees old terms marked «Ikke sendt» or «Over fristen».
pages: /mva/ /tidligere-program/
skill: myntos-mva
---

# Mark a VAT period as filed from another program

A company that moved to Myntos during the year, or runs Myntos beside its old
program for a while, has terms the old program already filed. Myntos computes
those terms from the imported vouchers and, until told, shows them as not sent
and overdue. **Do not send them again from Myntos**: a second melding for a
term replaces the first at Skatteetaten. Mark them instead.

## They might ask

- «Myntos sier at 1. termin ikke er sendt, men vi sendte den fra Tripletex»
- «Hvorfor står gamle terminer som over fristen?»
- «Hvordan sammenligner jeg mva-tallene i Myntos med det gamle programmet?»
- "Myntos says my VAT return is overdue but I filed it from Fiken"

## Before they start

- The role «Medlem» or more (the same as for sending a melding).
- The term has ended and nothing has been sent for it from Myntos.

## Steps

1. Open `https://myntos.no/mva/` and press «Se meldingen» on the term.
2. If the old program's own VAT settlement came across with the SAF-T import,
   the page says so above the form: «Det forrige programmet førte et
   mva-oppgjør for terminen …», with the amounts, and whether they agree with
   Myntos. The form is then already filled in.
3. Open «Sendt fra et annet program?».
4. Under «Sendt dato», the day the old program filed it.
5. Under «Beløp (valgfritt)», what it filed: positive owed, negative a refund.
   With an amount, Myntos compares its own figure with it.
6. Press «Merk som sendt fra et annet program».

The term then reads «Sendt fra et annet program», is no longer «Over fristen»,
and cannot be checked or sent from Myntos. The term's card says whether
Myntos comes to the same figure; less than a krone apart counts as the same,
because some programs round to whole kroner. A difference marked «Avvik mot
Myntos» means the two programs' books disagree on that term: look for
vouchers that are missing or booked differently.

## Several at once, right after the import

When the books have just been brought over from the old program's SAF-T files,
the finished import on «Hent regnskapet fra et annet program»
(`https://myntos.no/tidligere-program/`) asks the same question for every term
of the imported years at once, under «Mva-meldingene det gamle programmet
leverte». Tick the terms the old program filed and press «Merk som levert fra
det gamle programmet». The terms where the files carry the old program's own
settlement are ticked already, with its amount recorded; the day recorded is
the term's deadline. Only «Medlem» and up see the list.

## Undo

Press «Angre» on the term's card. The term can then be checked and sent from
Myntos again. Do this if the mark was a mistake, or to send a corrected
melding for that term from Myntos once the move is finished; the corrected
melding replaces the old program's at Skatteetaten. The history under
«Meldinger for terminen» keeps both the mark and the undo.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «… kan bare være sendt mellom … og i dag» | The date is before the term ended or in the future | Use the day it was actually filed |
| «… er sendt fra Myntos, så den kan ikke merkes …» | Myntos filed this term itself | Nothing to mark |
| No «Sendt fra et annet program?» | The term is not over, is already sent, or the role is «Leser» | Wait, or ask someone with access |

## Good to know

- Marking sends nothing to Skatteetaten. It is only Myntos's record.
- Mark only what was really filed: a marked term is not reported as overdue,
  so a wrong mark hides a deadline.
- A request from an AI to send that term is closed when it is marked.

## Related

- [Bring your books over from SAF-T files](https://myntos.no/llms/help/opening-balance-and-year-end/bring-your-books-over-from-saf-t-files.md)
- [Compare Myntos with the previous program](https://myntos.no/llms/help/opening-balance-and-year-end/compare-myntos-with-the-previous-program.md)
- [Correct a VAT return already sent](https://myntos.no/llms/help/vat/correct-a-vat-return-already-sent.md)
- An agent with a key can read the proposals and mark terms through the API:
  [myntos-mva](https://myntos.no/llms/skills/myntos-mva.md)

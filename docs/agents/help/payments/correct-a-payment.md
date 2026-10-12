---
title: Correct a payment
description: How a person fixes a registered payment whose amount, date, type, VAT code, counterparty or description is wrong, with «Rediger» and «Lagre endringer» on the Betaling page in Myntos, and why the books then keep three entries. Use when someone asks how to change, edit or fix a payment, or why a corrected payment shows «Rettet».
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Correct a payment

A registered payment is posted, and Norwegian bookkeeping law
(bokføringsloven) does not allow a posted entry to be changed. What «Rediger»
does instead, when the person saves, is two things in one step: it reverses
(motposterer) the original entry and registers the corrected one. The books
keep all three, so an auditor can see both the mistake and the correction,
but the register shows them as one line. Use it when the payment did happen
and only a detail is wrong; a payment that should not be there at all is
[reversed](https://myntos.no/llms/help/payments/reverse-a-payment.md).

## They might ask

- «Hvordan endrer jeg en betaling jeg har ført?»
- «Jeg førte feil beløp, hvordan retter jeg det?»
- «Jeg valgte feil type på en betaling»
- "How do I edit a payment in Myntos?"

## Before they start

- They need the role «Medlem», «Administrator» or «Eier».
- The payment's financial year must still be open. A payment in a year that
  has been closed («Årsavslutning») cannot be corrected until an
  administrator reopens the year.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`).
2. Find the payment in the register and click «Rediger» at the end of its
   row. Nothing changes in the books yet.
3. The form at the top fills with the payment. Its heading becomes «Rediger
   bilag 2026-0007», and a note says: «Du redigerer bilag 2026-0007. Når du
   lagrer, blir den opprinnelige føringen motpostert og den rettede lagt inn,
   slik bokføringsloven krever. Regnskapet viser da alle tre.»
4. Change what was wrong: «Penger inn»/«Penger ut», «Hva gjaldt det?»,
   «Dato», «Beløp inkl. mva», «Motpart», «Mva-kode», «Konto», «Betalt via» or
   «Beskrivelse».
5. Press «Lagre endringer». To back out without changing anything, click
   «Avbryt redigering» instead.
6. The form says «Bilag 2026-0007 er rettet. Motposteringen og den rettede
   føringen (2026-0021) står i registeret.» The register shows the corrected
   payment with «Rettet. 2 eldre bilag.» under its description.
7. To see the original and its reversal, click the small triangle in front of
   the date on that row.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Rediger» on the row | The entry has already been reversed, it is a reversal itself, or their role is «Leser» | Correct the newest entry in the group instead, or ask someone with «Medlem» or more |
| «bilaget er allerede reversert» | The entry was reversed or corrected before | Reload the page and correct the newest entry |
| «regnskapsåret 2025 er avsluttet» | The original's financial year is closed | An administrator must reopen the year under «Årsavslutning» first |
| «inngående balanse og årsavslutning rettes fra sine egne sider, ikke fra registeret» | It is the opening balance or a year-end entry | Correct it on «Inngående balanse» or «Årsavslutning» |
| «Vi fikk ikke lagret endringen. Den opprinnelige føringen står som før.» | The save failed, and nothing was changed | Try again |

## Good to know

- Either everything happens or nothing does: there is never a reversed
  payment without its replacement.
- The original's files stay with the original; the archive cannot move them.
  The corrected payment counts as documented. A new or better receipt can be
  chosen in the form before «Lagre endringer», and it is attached to the
  corrected entry. Choosing one makes Myntos read it and fill in the form from
  it, so check every field again before saving.
- Each correction adds two bilag numbers to the books. That is how it is
  supposed to look.
- The reversal is dated in the original's period, so the year's figures are
  right afterwards; the overview counts the payment once.

## Related

- [Reverse a payment](https://myntos.no/llms/help/payments/reverse-a-payment.md)
- [Delete a payment](https://myntos.no/llms/help/payments/delete-a-payment.md)
- [Read the payment register](https://myntos.no/llms/help/payments/read-the-payment-register.md)
- An agent with a key can do this through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)

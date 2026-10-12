---
title: Correct an opening balance
description: How a person fixes an opening balance (inngående balanse) that is already posted in Myntos, with «Rett inngående balanse», which reverses the old one and posts the corrected one in one step. Use when someone says their opening balance is wrong, an account or amount is missing, or the old accountant sent final figures that differ.
pages: /inngaende-balanse/
skill: myntos-inngaende-balanse
---

# Correct an opening balance

A posted opening balance cannot be edited or deleted: Norwegian bookkeeping
law keeps every posted voucher as it was. It is corrected instead. «Rett
inngående balanse» opens the posted lines in the form; when the corrected
version is posted, Myntos reverses the old voucher and posts the new one in
the same step, linked to each other. **Both stay in the books**, and the new one
cannot be edited either, so check it before posting.

## They might ask

- «Inngående balanse er feil, hvordan retter jeg den?»
- «Jeg glemte en konto i inngående balanse»
- «Regnskapsføreren har sendt nye tall for i fjor, hvordan endrer jeg inngående balanse?»
- "How do I change my opening balance in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The company's first year in Myntos must still be open. If it is closed,
  an «Administrator» or «Eier» reopens it first:
  [Reopen a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md).
- The corrected figures for every account, not only the one that changed: the
  new balance replaces the old one completely.

## Steps

1. Open `https://myntos.no/inngaende-balanse/` (from «Oversikt», the link
   «Inngående balanse» in the note under «Største poster i år»).
2. Under «Ført inngående balanse», press «Rett inngående balanse». The form
   «Rettet inngående balanse per 1. januar …» opens with the posted lines.
3. Change, add («Legg til linje») or remove («Fjern») rows until the form
   holds the whole corrected balance.
4. Press «Kontroller». The review says «Dette erstatter den førte balansen.
   Den gamle reverseres, og denne føres per 1. januar …». Fix any red message
   with «Tilbake til skjemaet».
5. Press «Før inngående balanse». This cannot be undone. The page says
   «Inngående balanse er ført som bilag …», and «Historikk» lists the old
   balance as «(reversert)», its reversal, and «Rettet balanse».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Rett inngående balanse» button | Their role is «Leser», or the first year is closed | Ask someone with «Medlem» or more; if closed, reopen the year first |
| «Regnskapsåret … er avsluttet, og inngående balanse kan bare føres i et åpent år. Gjenåpne året først hvis balansen må rettes.» | The first year is closed | An «Administrator» or «Eier» reopens it on «Årsavslutning» |
| «Før inngående balanse» cannot be pressed | Red messages in the review | [Review an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/review-an-opening-balance.md) |

## Good to know

- The correction is dated 1 January like the original, so all reports show the
  corrected opening figures; the old voucher and its reversal cancel out.
- To remove the opening balance without a new one, use «Trekk den tilbake»
  instead: [Reverse an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/reverse-an-opening-balance.md).

## Related

- [Review an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/review-an-opening-balance.md)
- [Reverse an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/reverse-an-opening-balance.md)
- An agent with a key can do this through the API:
  [myntos-inngaende-balanse](https://myntos.no/llms/skills/myntos-inngaende-balanse.md)

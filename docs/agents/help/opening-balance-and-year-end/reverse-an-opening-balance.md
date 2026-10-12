---
title: Reverse an opening balance
description: How a person withdraws a posted opening balance (inngående balanse) in Myntos with «Trekk den tilbake», which posts a reversal so the company has no opening balance afterwards. Use when someone posted an opening balance that should not be there at all, or asks how to delete or undo the opening balance.
pages: /inngaende-balanse/
skill: myntos-inngaende-balanse
---

# Reverse an opening balance

A posted opening balance cannot be deleted, because Norwegian bookkeeping law
keeps every posted voucher. «Trekk den tilbake» posts a reversal that cancels
it: afterwards the company has no opening balance, but the original and the
reversal both stay in the books. Use it only when there should be no opening
balance at all; to change figures,
[correct it](https://myntos.no/llms/help/opening-balance-and-year-end/correct-an-opening-balance.md)
instead, which does the reversal and the new balance in one step.

## They might ask

- «Hvordan sletter jeg inngående balanse?»
- «Jeg førte inngående balanse ved en feil»
- «Kan jeg angre inngående balanse?»
- "How do I delete the opening balance in Myntos?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- The company's first year in Myntos must still be open.

## Steps

1. Open `https://myntos.no/inngaende-balanse/`.
2. Under «Ført inngående balanse», press «Trekk den tilbake».
3. The browser asks «Reversere den førte inngående balansen? Den blir stående
   i regnskapet med en reversering.» Confirming posts the reversal, which
   cannot be undone.
4. The page says «Inngående balanse er reversert med bilag …», and the form
   for a new opening balance appears.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Trekk den tilbake» button | Their role is «Leser», or the first year is closed | Ask someone with «Medlem» or more; if closed, an «Administrator» or «Eier» reopens the year first |
| «det finnes ingen inngående balanse å reversere» | It was already withdrawn | Nothing to do |

## Good to know

- The ordinary correction of a voucher refuses the opening balance; this
  button and «Rett inngående balanse» are its only ways out.
- A new opening balance can be posted afterwards, in the same form.

## Related

- [Correct an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/correct-an-opening-balance.md)
- [Enter an opening balance](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md)
- An agent with a key can do this through the API:
  [myntos-inngaende-balanse](https://myntos.no/llms/skills/myntos-inngaende-balanse.md)

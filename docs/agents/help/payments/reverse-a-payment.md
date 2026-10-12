---
title: Reverse a payment
description: How a person undoes a payment that should never have been registered (it did not happen, or it was entered twice) with «Reverser» on the Betaling page in Myntos, what the confirmation says, and how the reversed payment looks afterwards. Use when someone asks how to undo, cancel or remove a duplicate payment, or what «Motpostert» means.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Reverse a payment

A payment registered by mistake cannot be deleted (bokføringsloven). It is
reversed instead: Myntos posts a mirror entry (a motpostering) beside it, so
the two cancel to zero and the books show both what happened and that it was
undone. «Reverser» is for a payment that should not be there at all, such as
one entered twice. A payment that did happen but with a wrong detail is
[corrected with «Rediger»](https://myntos.no/llms/help/payments/correct-a-payment.md)
instead. A reversal cannot itself be undone; the payment would have to be
registered again.

## They might ask

- «Hvordan angrer jeg en betaling?»
- «Jeg førte samme kvittering to ganger, hvordan fjerner jeg den ene?»
- «Hva betyr «Motpostert» i registeret?»
- "How do I undo a payment in Myntos?"

## Before they start

- They need the role «Medlem», «Administrator» or «Eier».
- The payment's financial year must still be open.

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`).
2. Find the payment in the register and click «Reverser» at the end of its
   row.
3. The browser asks: «Betalingen på bilag 2026-0007 skjedde ikke, og skal ut
   av regnskapet?», and explains that the original stays and an opposite entry
   is added beside it, and that «Rediger» is the choice if the payment should
   stand with other details. Click OK to reverse, or Cancel to stop.
4. The page says «Bilag 2026-0007 er motpostert.» The payment is now greyed
   out and struck through in the register, with «Motpostert av bilag
   2026-0022. Går i null.» under its description.
5. To see the reversal itself, click the small triangle in front of the date.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Reverser» on the row | It is already reversed, it is a reversal itself, or their role is «Leser» | Nothing to do, or ask someone with «Medlem» or more |
| «bilaget er allerede reversert» | Someone reversed or corrected it already | Reload the page |
| «regnskapsåret 2025 er avsluttet» | The payment's year is closed | An administrator must reopen the year under «Årsavslutning» first |
| «inngående balanse og årsavslutning rettes fra sine egne sider, ikke fra registeret» | It is the opening balance or a year-end entry | Use «Inngående balanse» or «Årsavslutning» |
| «Motposteringen feilet.» | Something went wrong, and nothing was posted | Try again |

## Good to know

- The reversal is dated in the same period as the original, so the year's
  figures and the VAT for that period come out right.
- The reversed payment and its reversal are left out of the figures in
  «Oversikt», so they no longer count as money in or out.
- Its files stay in the archive. A reversed payment is not counted as missing
  a bilag.

## Related

- [Correct a payment](https://myntos.no/llms/help/payments/correct-a-payment.md)
- [Delete a payment](https://myntos.no/llms/help/payments/delete-a-payment.md)
- An agent with a key can do this through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)

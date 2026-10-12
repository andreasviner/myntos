---
title: Reopen a financial year
description: How an owner or administrator reopens a closed financial year on the Årsavslutning page in Myntos with «Gjenåpne året», giving a reason and confirming the annual accounts are not adopted, so something more can be posted in it. Use when someone needs to post a forgotten invoice or receipt into a year that is already closed, or asks how to unlock a year.
pages: /arsavslutning/
skill: myntos-arsavslutning
---

# Reopen a financial year

A closed year can be opened again, for the ordinary case where a supplier
invoice or receipt for last year turns up after the year was closed. Nothing
is deleted: Myntos reverses the closing entries, which stay in the books beside
their reversals, and logs who reopened the year and why. It is only for
**before the annual accounts (årsregnskap) have been adopted** (fastsatt, for
an AS by the general meeting). Once they have been, the error is corrected in
the current year instead, not by changing the year that was reported. The year
then has to be closed again.

## They might ask

- «Hvordan gjenåpner jeg et avsluttet år i Myntos?»
- «Jeg har funnet en faktura fra i fjor, men året er låst»
- «Kan jeg føre noe i 2025 etter at året er avsluttet?»
- "How do I reopen a closed year in Myntos?"

## Before they start

- The role «Administrator» or «Eier».
- A reason, in a sentence of at least 10 characters. It is saved in the log
  with their name and shown to everyone with access to the books.
- Certainty that the annual accounts for the year have **not** been adopted.
- Only the latest closed year can be reopened. If a later year is closed too,
  that one is reopened first.

## Steps

1. Open `https://myntos.no/arsavslutning/` (from «Oversikt», the link
   «Årsavslutning» in the note under «Største poster i år»).
2. Choose the closed year with «‹» and «›». The card «Året er avsluttet» shows
   when it was closed.
3. Under «Begrunnelse», write why, for example «Leverandørfaktura fra Elektro
   AS for desember kom etter avslutningen».
4. Tick «Årsregnskapet for året er ikke fastsatt.»
5. Press «Gjenåpne året». The page says «Regnskapsåret … er gjenåpnet.
   Avslutningen er reversert.», and «Logg» shows the reopening with the
   reason.
6. Post what was missing, then close the year again:
   [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Året er avsluttet» but no «Begrunnelse» or «Gjenåpne året» | Their role is not «Administrator» or «Eier», or a later year is also closed | Ask an «Administrator» or «Eier»; reopen the later year first |
| «Bekreft at årsregnskapet ikke er fastsatt.» | The box was not ticked | Tick it, if it is true |
| «gjenåpning av et avsluttet år krever en begrunnelse og en bekreftelse på at årsregnskapet ikke er fastsatt. …» | The reason is shorter than 10 characters | Write a fuller sentence |
| «regnskapsåret … er avsluttet og bygger på …. Gjenåpne … først» | A later year is closed | Reopen the later year first |

## Good to know

- If the annual accounts have already been adopted, do not reopen: the
  correction is posted in the current year, as the accountant advises.
- The closing entries are reversed, not removed, so the books show both the
  first close and its reversal, and the log keeps every close and reopening.
- While the year is open, anyone with «Medlem» or more can post in it again.

## Related

- [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md)
- [What closing a year locks](https://myntos.no/llms/help/opening-balance-and-year-end/what-closing-a-year-locks.md)
- An agent with a key can do this through the API, with the person's own
  reason: [myntos-arsavslutning](https://myntos.no/llms/skills/myntos-arsavslutning.md)

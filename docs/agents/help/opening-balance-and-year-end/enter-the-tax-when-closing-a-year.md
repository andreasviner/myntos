---
title: Enter the tax when closing a year
description: How an owner or administrator of an aksjeselskap (AS) enters the year's tax cost («Betalbar skatt for året») when closing the financial year in Myntos, and why an enkeltpersonforetak (ENK) enters none. Use when someone asks where to post the company's tax, whether Myntos calculates it, or what the note about a missing tax cost means.
pages: /arsavslutning/
skill: myntos-arsavslutning
---

# Enter the tax when closing a year

An aksjeselskap (AS) pays tax on its profit, and that tax belongs in the year
the profit was made: it is posted as a cost (account 8300) and as a debt the
company owes (2500) before the result is moved to equity. **Myntos does not
calculate the tax**, nor deferred tax (utsatt skatt); the person closing the
year types in the figure, usually from their accountant or the tax return
draft. An enkeltpersonforetak (ENK) enters no tax at all: the owner pays it
personally, not the business.

## They might ask

- «Hvor fører jeg skattekostnaden i Myntos?»
- «Regner Myntos ut skatten for aksjeselskapet?»
- «Hva betyr "Det er ikke ført noen skattekostnad"?»
- "Where do I enter the corporate tax in Myntos?"

## Before they start

- The role «Administrator» or «Eier».
- The company is an AS, as shown under «Organisasjonsform» on the page.
- The tax figure for the year.

## Steps

1. Open `https://myntos.no/arsavslutning/` and pick the year with «‹» and «›».
2. In «Betalbar skatt for året», type the amount, for example `48 210,00`.
3. Leave the field (press Tab or click elsewhere). The figures update:
   «Årsresultat» is now after tax, and the table «Dette blir ført per 31.
   desember» shows 8300 and 2500 first. The confirmation box is unticked
   again, so they read it once more.
4. Continue closing the year:
   [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Betalbar skatt for året» field | The company is not an AS, or the year is already closed | An ENK enters no tax; a closed year must be reopened to change it |
| Grey: «Det er ikke ført noen skattekostnad. …» | An AS with a profit and no tax entered | Enter the tax, or close without it only if the accountant says there is none (for example losses carried forward) |
| «Skattekostnaden kan ikke være negativ.» | A minus sign | Enter zero or a positive amount |
| «Skattebeløpet kan ikke leses.» | Not an amount | Digits, with a comma for øre |
| «Et enkeltpersonforetak betaler ikke skatt selv. …» | Tax was entered for an ENK | Leave it empty |

## Good to know

- If the tax assessment later differs from the figure entered, the difference
  is normally posted in the year it becomes known, as the accountant advises,
  rather than by reopening the closed year.
- The tax is a figure the person states. An AI helping them should not work it
  out and present it as the tax.

## Related

- [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md)
- An agent with a key can do this through the API, with the figure the person
  gives it: [myntos-arsavslutning](https://myntos.no/llms/skills/myntos-arsavslutning.md)

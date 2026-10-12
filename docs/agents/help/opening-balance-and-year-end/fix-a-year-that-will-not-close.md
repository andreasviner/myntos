---
title: Fix a year that will not close
description: Explains each red message that stops a financial year from closing on the Årsavslutning page in Myntos, and the grey notes worth reading before closing, with what to do about each. Use when someone cannot press «Avslutt» on Årsavslutning, or asks what a message there means.
pages: /arsavslutning/
skill: myntos-arsavslutning
---

# Fix a year that will not close

On «Årsavslutning», Myntos checks the year before offering to close it. A red
message means the year cannot be closed yet, and «Avslutt …» stays greyed out
until it is sorted. Grey messages do not stop the close, but each one points
at something that is hard to fix once the year is locked, so read them first.

## They might ask

- «Hvorfor kan jeg ikke avslutte året i Myntos?»
- «Knappen Avslutt 2025 er grå»
- «Hva betyr "Årene avsluttes i rekkefølge"?»
- "Why can't I close the year in Myntos?"

## Before they start

They are on `https://myntos.no/arsavslutning/` with the year in question
chosen, as «Administrator» or «Eier».

## Steps

1. Read the red messages under the figures and use the table below.
2. Fix what each asks, then come back to the page (or change the year with «‹»
   and «›» and back) so it checks again.
3. Read the grey messages and decide whether to act on them before closing.
4. Tick the confirmation box; «Avslutt …» can be pressed once no red message
   is left.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Regnskapsåret … varer til …. Et år avsluttes etter at det er over, …» | The year is not over yet | Wait until after 31 December |
| «Regnskapsåret … er ikke avsluttet. Årene avsluttes i rekkefølge, …» | An earlier year in Myntos is still open | Close that year first with «‹» |
| «Selskapet har ingen registrert organisasjonsform, …» | Myntos does not know if it is an AS or an ENK | Fetch the company's details again with «Hent på nytt» on the company page: [Fetch the company from Enhetsregisteret again](https://myntos.no/llms/help/company-and-access/fetch-from-enhetsregisteret-again.md) |
| «Myntos avslutter foreløpig bare aksjeselskap (AS) og enkeltpersonforetak (ENK). …» | Another company form | Myntos cannot close it yet; the accountant handles the year-end |
| «Året har allerede … på skatte- og disponeringskontoene (8300, 8800, 8960, 8990) …» | Someone posted tax or a disposition by hand | Find and correct that entry first; Myntos posts those itself |
| «Skattekostnaden kan ikke være negativ.» or «… brøkdeler av et øre.» | A wrong tax figure | Correct «Betalbar skatt for året» |
| «Et enkeltpersonforetak betaler ikke skatt selv. …» | Tax entered for an ENK | Empty the tax field |
| «Regnskapsåret … er allerede avsluttet.» | Already closed | Nothing to do; to change it, [reopen it](https://myntos.no/llms/help/opening-balance-and-year-end/reopen-a-financial-year.md) |
| Grey: «Mva for … er ikke gjort opp mot 2740. …» | The VAT for a period of the year is not settled in Myntos | Settle it on «Mva-melding» first if it is to be sent from Myntos; it cannot be posted in a closed year |
| Grey: «Det er ikke ført noen inngående balanse for …. …» | The first year in Myntos has no opening balance | If the company existed before Myntos, [enter it](https://myntos.no/llms/help/opening-balance-and-year-end/enter-an-opening-balance.md) first |
| Grey: «Det er ikke ført noen skattekostnad. …» | An AS with a profit and no tax | [Enter the tax](https://myntos.no/llms/help/opening-balance-and-year-end/enter-the-tax-when-closing-a-year.md) |
| Grey: «Konto 2060 Privatkonto har en saldo på …. …» | An AS has money on a private account | An AS has no private account: money taken out is a loan or a dividend. Ask the accountant; the balance is left as it is |

## Good to know

- The check runs again when «Avslutt …» is pressed, so if something changed
  in between, the close is refused with the reason.
- Only an «Administrator» or «Eier» sees the button at all.

## Related

- [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md)
- [What closing a year locks](https://myntos.no/llms/help/opening-balance-and-year-end/what-closing-a-year-locks.md)
- An agent with a key can read the same check through the API:
  [myntos-arsavslutning](https://myntos.no/llms/skills/myntos-arsavslutning.md)

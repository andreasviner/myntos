---
title: Run a deep KI analysis of a period
description: How a person starts a «Dyp KI-analyse» under «Gå dypere» on the «Analyse» page in Myntos, chooses the KI level («Gratis», «Medium» or «Smart»), watches progress or cancels it, and opens or re-runs finished reports under «Tidligere analyser». Use when someone asks how to get a deeper AI summary of their income and costs, how it counts against the weekly KI quota, why an old report says «Laget før … nye bilag», or why the button is missing.
pages: /analyse/
skill: myntos-analyse
---

# Run a deep KI analysis of a period

Under the standard figures and charts on «Analyse» (`https://myntos.no/analyse/`),
the **«Gå dypere»** card lets a person ask Myntos's own KI (Løynd) to look
closer at the chosen period: patterns, recurring costs, major changes and
suggestions worth following up. Every chart inside the report is drawn live
from the books, and every number the model writes in the text is checked
against the company's figures before the report is shown. Nothing is posted or
changed in the books.

## They might ask

- «Hvordan kjører jeg en dyp KI-analyse?»
- «Hva gjør knappen «Dyp KI-analyse» på Analyse?»
- «Teller dyp KI-analyse mot ukekvoten i KI-planen?»
- «Kan jeg lukke siden mens analysen kjører?»
- «Hvor finner jeg tidligere analyser?»
- «Det står «Laget før 3 nye bilag» på rapporten. Hva betyr det?»

## Before they start

- The company has KI switched on ([Turn Myntos's KI on or off](https://myntos.no/llms/help/ai/turn-myntos-ki-on-or-off.md)).
- The company has at least one posted income or cost voucher in the chosen
  period.
- To start or cancel a run: a role that may write in the books («Eier»,
  «Administrator» or «Medlem»). A person with «Leser» cannot start a new
  analysis, but can open and read finished reports under «Tidligere analyser».
- For «Medium» or «Smart»: an active KI plan with weekly quota left
  ([Buy a KI plan](https://myntos.no/llms/help/ai/buy-a-ki-plan.md)).
  «Gratis» works for every company with KI on, with no plan needed.

## Steps

1. Open «Analyse» in the top menu (`https://myntos.no/analyse/`) and pick the
   period at the top («I år», «Siste 12 måneder», «I fjor» or an older year
   under «Eldre år»).
2. Scroll to the **«Gå dypere»** card.
3. Under «KI-nivå», choose «Gratis», «Medium» or «Smart» (when the company
   has a KI plan, the line underneath shows the remaining weekly percentage).
   The choice is shared with the other KI boxes in the same browser.
4. Press **«Dyp KI-analyse»**.
   - If the books have not changed since an analysis of the same period at that
     level (or a higher level) was made, the stored report opens right away
     without asking the model again and without using any quota.
   - Otherwise a progress bar appears, stepping through «Leser bilagene» (only
     for unread vouchers, always at «Gratis» so it counts against no quota),
     «Regner ut tallene», «Spør KI-en (… )» and «Sjekker svaret».
   - The line underneath says **«Du kan lukke siden. Analysen blir liggende
     her.»** They can leave the page and come back later; the report stays
     under «Tidligere analyser».
   - To stop a run that is still going, press **«Avbryt»**. Any vouchers
     already read stay saved so a later run will not read them again.
5. When the run finishes, the report opens in place under **«Tidligere
   analyser»**:
   - The heading and stamp, such as «Laget 26. nov. med Smart», with the
     period dates beside it.
   - A short summary, followed by sections with text and live charts (each
     chart has «Vis tallene» for exact figures to the øre).
   - At the bottom, «Forslag å se nærmere på» with follow-up points, if any.
   - Press **«Lukk»** (or click the report row again) to close the report.
6. To open an older report, click its row in **«Tidligere analyser»** (newest
   first). If new income or cost vouchers in that period were booked after the
   report was made, the row shows a mark such as **«Laget før 3 nye bilag»**,
   and the open report offers **«Lag ny»** to run a fresh analysis for that
   same period.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «KI er slått av for dette selskapet.» inside «Gå dypere», and no button | KI is switched off for this company | An administrator or owner can switch it on under «Innstillinger» → «KI i Myntos» (`https://myntos.no/organisasjon/#ki`). No earlier reports are shown while KI is off |
| No «Gå dypere» card, only «Tidligere analyser» | Their role is «Leser», which can read reports but not start new runs | Read the existing reports, or ask a colleague with write access to start one |
| «Medium» and «Smart» are greyed out | The company has no active KI plan or this week's quota is spent | Run with «Gratis», or have the owner activate or upgrade a plan via «Se KI-planene» (`https://myntos.no/ki/about/#planer`) |
| A notice with «Se KI-planene» saying the analysis answered at «Gratis» | The plan expired, this week's quota ran out, or the larger model was busy just then | The report is still made at «Gratis» and counts against no quota. Wait for Monday's weekly reset, switch plan, or try again later |
| «Løynd svarte ikke akkurat nå. Prøv igjen om litt.» | Løynd did not answer | Wait a moment and press «Dyp KI-analyse» again; any vouchers already read are reused |
| «KI ble slått av underveis, så analysen ble stoppet.» | Someone switched KI off for the company while the run was going | Switch KI back on if wanted, then start again |

## Good to know

- Only the analysis step itself counts against a KI plan at «Medium» or
  «Smart». Reading unread vouchers first always runs at «Gratis» on Løynd's
  own machines in Norway and counts against no quota.
- Pressing «Dyp KI-analyse» again for the same unchanged period and level (or a
  lower level) returns the stored report without using any quota. Choosing a
  higher level for the same period runs at that higher level.
- What is sent to Løynd is a compiled summary of the period's figures, splits,
  top counterparties, recurring costs and summaries of the largest vouchers
  (never the PDF or image files). See «Slik bruker Myntos KI»
  (`https://myntos.no/ki/about/#analyse`).
- Finished reports are stored in Myntos's database in the EEA for the company's
  own use only, are not data sharing, and are deleted with the company.

## Related

- [See how the business is going, month by month](https://myntos.no/llms/help/analysis/see-income-and-costs.md)
- [Choose the KI level, and add a picture to a KI box](https://myntos.no/llms/help/ai/choose-a-ki-level.md)
- [What Myntos's own KI does](https://myntos.no/llms/help/ai/what-myntos-ki-does.md)
- [Buy a KI plan](https://myntos.no/llms/help/ai/buy-a-ki-plan.md)
- An agent with a key can start, watch and read deep analyses through the API:
  [myntos-analyse](https://myntos.no/llms/skills/myntos-analyse.md)

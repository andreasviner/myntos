---
title: Send the tax return
description: How a person sends (files) an aksjeselskap's tax return (skattemelding) with the næringsspesifikasjon to Skatteetaten from the Skattemelding page in Myntos, after the check, and what happens with a revisor. Use when someone asks how to send, file or deliver the skattemelding from Myntos, or when their AI has asked them to file it.
pages: /skattemelding/ /godkjenning/
skill: myntos-skattemelding
---

# Send the tax return

Sending files the tax return with Skatteetaten through Altinn, exactly as it
was checked. **A sent skattemelding cannot be withdrawn.** It can be replaced
by sending a new one for the same year. An aksjeselskap files by 31 May the
year after.

## They might ask

- «Hvordan sender jeg skattemeldingen fra Myntos?»
- «KI-en min ba meg sende skattemeldingen. Hva gjør jeg?»
- «Hvordan leverer jeg næringsspesifikasjonen?»
- "How do I file my company's tax return from Myntos?"

## Before they start

- A check Skatteetaten accepted:
  [Check the tax return with Skatteetaten](https://myntos.no/llms/help/tax-return/check-the-tax-return-with-skatteetaten.md).
- Nothing entered in the year since that check.

## Steps

1. On `https://myntos.no/skattemelding/`, in the box «Send inn», read
   Skatteetaten's answer and the tax it computed. If an AI asked them to file,
   the request under «Godkjenning» has a button «Gå til skattemeldingen» that
   leads here.
2. Tick «Jeg har gått gjennom skattemeldingen og vil sende den inn for …»,
   which names the company, its organisation number, the income year and the
   skattemessig resultat. Check all four.
3. Press «Send inn skattemeldingen». This is the step that cannot be undone.
4. The page says one of:
   - «Skattemeldingen er sendt, og svaret fra Skatteetaten er lagret.»
   - «Skattemeldingen er sendt. Svaret fra Skatteetaten kommer litt senere.»
   - «Skattemeldingen er sendt. Nå må revisoren bekrefte den i Skatteetatens
     løsning.»
5. Under «Skattemeldinger for året», the year reads «Sendt og mottatt»,
   «Sendt, venter på svar» or «Sendt, venter på revisor»:
   [Get Skatteetaten's answer](https://myntos.no/llms/help/tax-return/get-skatteetatens-answer.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No box «Send inn», and «Regnskapet for året er endret etter kontrollen. …» | Something was entered in the year after the check | Check again, read the new figures, then send |
| «bekreft det skattemessige resultatet i skattemeldingen du kontrollerte før den sendes» | The confirmation did not match the checked figure | Reload, check again, tick and send |
| «Innsendingen stoppet underveis. Ingenting er bekreftet sendt. …» | Altinn stopped partway | Look in Altinn's inbox for the company before trying again; then check again and send |
| «logg inn med ID-porten for å kontrollere og sende skattemeldingen» | The ID-porten login ran out | Reload and log in again |

## Good to know

- **A company with a revisor**: the box says «Revisoren må bekrefte
  næringsspesifikasjonen i Skatteetatens løsning etter at du har sendt.» The
  revisor confirms in Skatteetaten's own viewer («Åpne hos Skatteetaten» on
  the filing), and Skatteetaten processes the return only after that.
- Real estate and shares are not in the formue Myntos sends; add them on
  skatteetaten.no afterwards:
  [What the tax return from Myntos leaves out](https://myntos.no/llms/help/tax-return/what-the-tax-return-leaves-out.md).
- Paying the tax is separate, after Skatteetaten's tax settlement.

## Related

- [File what an AI asks you to file](https://myntos.no/llms/help/ai/file-what-an-ai-asks-you-to-file.md)
- An agent with a key can review the return and ask the person to send it:
  [myntos-skattemelding](https://myntos.no/llms/skills/myntos-skattemelding.md)

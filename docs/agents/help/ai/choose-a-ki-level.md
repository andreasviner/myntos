---
title: Choose the KI level, and add a picture to a KI box
description: How a person chooses «Gratis», «Medium» or «Smart» under «KI-nivå» beside a KI text box in Myntos (the bilag on «Betaling», both boxes on «Faktura») or on «Analyse» and «Mottatt», what each level is, what the line under the box means, why an answer can come from «Gratis» anyway, and how «Legg ved bilde» turns a screenshot into text in the box. Use when someone asks which KI level to choose, why «Medium» or «Smart» cannot be chosen, why the answer came from the free model, or whether they can paste a screenshot into the KI box.
pages: /analyse/ /faktura/ /innkommende/ /ki/ /kontantstrom/
skill: myntos-ki
---

# Choose the KI level, and add a picture to a KI box

Every box where a person writes to Myntos's own KI has the same bar under the
text: a picture button, the choice «KI-nivå» and the page's own button. There
are three such boxes: the bilag on «Betaling» (`https://myntos.no/kontantstrom/`)
and both boxes on «Faktura» (`https://myntos.no/faktura/`). The same «KI-nivå»
choice also sits where KI is used without a text box: on «Analyse»
(`https://myntos.no/analyse/`) under «Gå dypere» for «Dyp KI-analyse», and on
«Mottatt» (`https://myntos.no/innkommende/`) under «Bokfør som bilag» for
«Les vedlegg med KI».

| Level | What it is | Needs |
| --- | --- | --- |
| «Gratis» | Løynd's own model, on Løynd's machines in Norway. Chosen until someone picks another | Nothing. Every company has it, with no limit |
| «Medium» | A larger model, which gets more out of a messy bilag or a long e-mail | A KI plan |
| «Smart» | The largest model. It thinks before it answers, so it is slower, but best at what is hard to decide | A KI plan |

The same things are sent at every level. Only the model that reads them
changes, and only «Medium» and «Smart» count against the plan's weekly quota.

## They might ask

- «Hvilket KI-nivå bør jeg velge?»
- «Hvorfor kan jeg ikke velge «Smart»?»
- «Svaret sier at det kom fra den gratis modellen. Hvorfor?»
- «Kan jeg lime inn et skjermbilde av e-posten fra kunden?»
- "What is the difference between Medium and Smart?"

## Before they start

- The role «Medlem», «Administrator» or «Eier».
- KI switched on for the company under «Innstillinger for selskapet» →
  «KI i Myntos» (`https://myntos.no/organisasjon/#ki`). With KI off,
  «KI-nivå» is not shown at all.
- For «Medium» or «Smart»: an active KI plan with weekly quota left
  ([Buy a KI plan](https://myntos.no/llms/help/ai/buy-a-ki-plan.md)).

## Steps

1. Write in the box as usual, or add a picture (below).
2. Under the box, at «KI-nivå», choose «Gratis», «Medium» or «Smart». Holding
   the pointer over a choice says what it is. «Medium» and «Smart» are
   greyed out while the company has no plan, or has spent its weekly quota.
3. Read the line under the box. It says what the company has, and always ends
   in a link to the levels and plans on «Om KI og KI-plan»
   (`https://myntos.no/ki/about/#planer`):
   - «Medium og Smart krever en KI-plan.», with the link «Se KI-planene»,
     when the company has no plan;
   - «KI Pluss: 85 % igjen denne uken.», with the link «Om KI-nivåene», while
     weekly quota remains;
   - «Ukekvoten i KI Pluss er brukt opp (0 % igjen). Nullstilles mandag 4. mai.»,
     with the link «Bytt plan» (for owners) or «Om KI-nivåene», when the weekly
     quota is spent.
4. Press the page's button («Fyll ut fakturaen», «Foreslå linjer», or, on
   «Betaling», pick the files). The choice is remembered in this browser for
   every KI box in Myntos, and the boxes show «Gratis» whenever the company
   cannot use it.

### Add a picture to the box

On «Faktura», the button is «Legg ved bilde». It is for a screenshot or photo
of text: the customer's e-mail with their invoice details, a note about the
sale.

1. Press «Legg ved bilde» and choose a picture or a PDF, or paste an image
   straight into the box (Ctrl+V / Cmd+V). The line under the box says «Leser»
   and the file's name, such as «Leser e-post.png …» or «Leser skjermbilde-1.png …».
2. The text read from it is added at the end of the box, after anything
   already written, and the line says «Teksten fra bildet er lagt inn. Se over
   den før du sender.».
3. Read it through and correct it, then press the page's button as usual.

On «Betaling», the same place says «Legg ved bilag». Choosing a file there, or
pasting an image straight into «Beskrivelse» (Ctrl+V / Cmd+V), adds the file
as the payment's bilag, the same as «Last opp faktura, kvittering eller
skjermbilder»
([Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)).
The bilag is read as a bilag and kept in the archive; it does not become text
in «Beskrivelse».

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «KI-nivå» under the box | KI is switched off for the company, or this copy of Myntos has no model | Switch KI on on the KI page if wanted; otherwise fill in by hand |
| «Medium» and «Smart» are greyed out | The company has no KI plan, or this week's quota is spent. The line under the box says which | Carry on with «Gratis», wait for Monday reset, or have the owner choose or upgrade a plan with the link under the box |
| The answer says ««Medium» krever en KI-plan. Svaret kommer fra den gratis modellen. …» | The company's plan ended while the page was open. The boxes move to «Gratis» by themselves | Carry on with «Gratis», or have the owner choose a plan: «Se KI-planene» under the box leads to the plans |
| «Ukekvoten i KI-planen er brukt opp, så svaret kommer fra den gratis modellen. …» | This week's quota ran out, perhaps during this answer | Carry on with «Gratis», wait until Monday 00:00 when the quota resets, or have the owner switch to a larger plan under «Om KI» |
| «Den betalte modellen svarte ikke akkurat nå, så svaret kommer fra den gratis modellen, som ikke teller mot kvoten.» | The larger model was busy or refused, so «Gratis» answered in its place | Check the answer as usual, or try again in a moment with the same level |
| «Vi fant ingen tekst i bildet. Skriv det inn selv.» | The picture has no text Myntos could read | Type it in the box |
| «Teksten fra bildet er lagt inn, men ble for lang og er kuttet. …» | The text did not fit in the box | Check the end of the box, and remove what is not needed |
| «Vi fikk ikke lest filen nå. Skriv det inn selv, eller prøv igjen.» | Løynd did not answer | Try again in a moment, or type it |

## Good to know

- An answer is never stopped for want of quota: it comes from «Gratis» and
  says why, so the form is still filled in.
- Only the person's own choice is used. A key's request without a level is
  answered by «Gratis».
- «Medium» and «Smart» run on Løynd's own machines in Norway when they are
  free, and otherwise on extra KI capacity in the EU that Løynd uses. Nothing
  leaves the EEA, nothing is stored there, and nothing trains a model.
- A picture added with «Legg ved bilde» is always read on Løynd's own machines
  in Norway, whatever the level, and counts against no quota. It is not stored: only the
  text it became goes on, once they press the button. An iPhone photo (HEIC)
  is read through a copy without its location data.
- A KI plan is the company's, so everyone who may write in it can choose
  «Medium» or «Smart», not only the owner who bought it.

## Related

- [Buy a KI plan](https://myntos.no/llms/help/ai/buy-a-ki-plan.md)
- [Let KI fill in the invoice from the customer's e-mail](https://myntos.no/llms/help/invoices/fill-in-an-invoice-with-ki.md)
- [Let KI write the lines](https://myntos.no/llms/help/invoices/add-lines-with-ki.md)
- [Record a payment from a receipt](https://myntos.no/llms/help/payments/record-a-payment-from-a-receipt.md)
- An agent with a key sends the level with the request:
  [myntos-ki](https://myntos.no/llms/skills/myntos-ki.md)

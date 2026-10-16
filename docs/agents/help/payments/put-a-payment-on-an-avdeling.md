---
title: Put a payment on an avdeling or prosjekt
description: How a person puts a payment on an avdeling, prosjekt or driftsgren when registering it on the Betaling page in Myntos, and how to move one already posted. Use when someone asks how to book a payment to a department, a project or a farm's production line.
pages: /kontantstrom/
skill: myntos-kontantstrom
---

# Put a payment on an avdeling or prosjekt

When the company has avdelinger, prosjekter or driftsgrener, the payment form
on «Betaling» has one list for each, and the choice is posted with the
payment. It is what the result per avdeling is built from. The lists are not
there at all when the company has none.

## They might ask

- «Hvordan fører jeg en betaling på en avdeling?»
- «Hvordan velger jeg prosjekt på en kvittering?»
- «Melkeoppgjøret skal på driftsgren melk»
- "How do I assign a payment to a project in Myntos?"

## Before they start

- The role «Medlem» or more.
- The avdeling or prosjekt set up under «Avdelinger»:
  [Set up avdelinger and prosjekter](https://myntos.no/llms/help/departments/set-up-departments-and-projects.md).

## Steps

1. Open «Betaling» in the top menu (`https://myntos.no/kontantstrom/`) and fill
   in the payment as usual.
2. Below «Betalt via», choose in the list named after the dimension
   («Avdeling», «Prosjekt», «Driftsgren»). «Ingen» leaves the payment on none.
3. Press «Registrer betaling».
4. On a payment split into lines, each line has its own lists:
   [Split a payment into several lines](https://myntos.no/llms/help/payments/split-a-payment-into-lines.md).

To move a payment already posted to another avdeling, correct it: «Rediger»
on its row, choose the right one, and «Lagre endringer». The books keep the
original, its reversal and the corrected payment.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No list for avdeling | The company has none set up, or all are switched off | Set them up under «Avdelinger» |
| «… er deaktivert og brukes ikke i nye føringer» | The chosen value was switched off | Choose another, or «Ingen» |
| «en linje kan ha bare én verdi for …» | Not possible from the page; an agent sent two | Split the payment into one line per avdeling |

## Good to know

- The choice goes on the income or cost line of the payment, for its whole
  amount without VAT, not on the bank or VAT lines.
- It is also written into the SAF-T file.

## Related

- [See the result per avdeling or prosjekt](https://myntos.no/llms/help/departments/see-the-result-per-department.md)
- [Correct a payment](https://myntos.no/llms/help/payments/correct-a-payment.md)
- An agent with a key can do this through the API:
  [myntos-kontantstrom](https://myntos.no/llms/skills/myntos-kontantstrom.md)

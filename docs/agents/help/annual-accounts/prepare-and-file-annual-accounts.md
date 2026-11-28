---
title: Prepare and file annual accounts (årsregnskap)
description: How a person prepares resultatregnskap, balanse, comparative prior-year figures, mandatory small-company notes, board and general-meeting minutes, and files årsregnskap (RR-0002) to Regnskapsregisteret via Altinn 3 in Myntos. Use when someone asks how to prepare or submit årsregnskap in Myntos.
pages: /aarsregnskap/
skill: myntos-aarsregnskap
---

# Prepare and file annual accounts (årsregnskap)

Every Norwegian aksjeselskap (AS or ASA) must prepare annual accounts
(**årsregnskap**) and send them to Regnskapsregisteret (Brønnøysundregistrene)
within one month after the general meeting adopts them and no later than
**31 July** the following year. Myntos computes resultatregnskap and balanse
from your closed financial year, pre-fills mandatory small-company notes,
generates printable PDFs for the accounts and minutes, and sends RR-0002
through Altinn 3.

## They might ask

- «Hvordan sender jeg årsregnskapet til Brønnøysund i Myntos?»
- «Hvor fyller jeg ut noter og fjorårstall i årsregnskapet?»
- «Hvordan lager jeg styreprotokoll og generalforsamlingsprotokoll?»
- "How do I prepare and file annual accounts in Myntos?"

## Before they start

- They need the role «Medlem», «Administrator» or «Eier» to edit notes and
  submit. A «Leser» can view figures and download PDFs and XML.
- Close the financial year first under «Årsavslutning»: see
  [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md).
- Signing in Altinn requires a board member, daglig leder, or authorized
  representative to log in with ID-porten. Optionally, an owner or admin can
  also set up an Altinn system user first so an accountant or team member can
  prepare and lock the filing before a board member signs: see
  [Set up an Altinn system user](https://myntos.no/llms/help/shareholders/set-up-altinn-system-user.md).

## Steps

1. Open `https://myntos.no/aarsregnskap/` (or click «Årsregnskap» from the
   overview page or «Til årsregnskapet» from «Frister») and select the
   financial year with `‹` and `›`.
2. Review any red error messages or yellow notes at the top of the page.
3. Check «Resultatregnskap og balanse». If the previous year was not kept in
   Myntos, click «Rediger sammenligningstall (fjoråret)», type whole-NOK
   prior-year figures, and click «Lagre sammenligningstall».
4. Under «Fastsettelse, revisjon og protokoller», fill in «Fastsettelsesdato
   (generalforsamling)», «Bekreftende representant (fullt navn)», optional
   «E-post for vedtak fra Brønnøysund (valgfritt)», and verify the checkboxes
   for small-company rules and audit opt-out. Expand «Opplysninger til styre-
   og generalforsamlingsprotokoll» to fill in meeting dates, places, and board
   members.
5. Under «Noter til årsregnskapet (små foretak)», review Note 1 through Note 7
   (including «Antall årsverk i regnskapsåret» in Note 2, which Brønnøysund
   requires even when it is `0`). Click «Legg til egen note» if you need extra
   disclosures, then click «Lagre utkast og noter».
6. Use the download buttons at the top of the summary card to print or save
   «Årsregnskap (PDF)», «Styreprotokoll (PDF)», «Generalforsamling (PDF)»,
   «Hovedskjema (XML)», or «Selskapsregnskap (XML)».
7. Scroll to «Send til Regnskapsregisteret (Altinn 3)». Tick the confirmation
   box («Jeg bekrefter at resultatregnskap, balanse og noter er kontrollert og
   i samsvar med generalforsamlingens vedtak.») and choose one of the paths:
   - Click «Klargjør og lås i Altinn (systembruker)» to upload and lock the
     filing via the company's approved Altinn system user, then have a board
     member log in with «Logg inn med ID-porten» and click «Signer og send inn
     med ID-porten» on the row under «Innsendinger og arkiv» (or click «Åpne i
     Altinn ↗» to sign on altinn.no).
   - Or click «Logg inn med ID-porten» and click «Send og signer med ID-porten»
     (or «Klargjør i Altinn (ID-porten)» to inspect before signing).
8. Once signed, click «Hent status og kvittering» if the receipt PDF did not
   arrive immediately. Archived XML, PDF attachment, and «Kvittering fra Altinn
   (PDF)» stay under «Innsendinger og arkiv» for five years.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «Regnskapsåret … er ikke avsluttet ennå» | The financial year is still open | Finish closing under «Årsavslutning» (`/arsavslutning/`) first |
| «Oppgi dato for fastsettelse av årsregnskapet» or «Oppgi fullt navn på representant» | Required Hovedskjema fields are blank | Fill in «Fastsettelsesdato (generalforsamling)» and «Bekreftende representant (fullt navn)» and click «Lagre utkast og noter» |
| «Rett feilene øverst på siden før årsregnskapet kan sendes til Altinn.» | One or more red findings remain | Fix every red item listed at the top of `/aarsregnskap/` |
| «Regnskapet er endret siden klargjøring» | Vouchers or notes changed after preparing the Altinn instance | Prepare a new instance so the signed accounts match the books |

## Good to know

- Everything sent with an årsregnskap (including optional PDF attachments)
  becomes public in Regnskapsregisteret. Never type fødselsnummer or sensitive
  personal data into the notes.
- Signing in Altinn automatically submits the annual accounts to
  Regnskapsregisteret and attaches the signer's fødselsnummer.
- Once signed and received, the årsregnskap deadline on «Frister» automatically
  updates to «Sendt fra Myntos».

## Related

- [Close a financial year](https://myntos.no/llms/help/opening-balance-and-year-end/close-a-financial-year.md)
- [Set up an Altinn system user](https://myntos.no/llms/help/shareholders/set-up-altinn-system-user.md)
- [See your deadlines](https://myntos.no/llms/help/overview/see-your-deadlines.md)
- An agent with a key can prepare the annual accounts and queue submission for
  approval: [myntos-aarsregnskap](https://myntos.no/llms/skills/myntos-aarsregnskap.md)

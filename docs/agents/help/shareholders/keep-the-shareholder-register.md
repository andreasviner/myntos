---
title: Keep the shareholder register (aksjeeierbok)
description: How a person sets up share classes, adds shareholders, imports last year's figures from Skatteetaten, and records corporate or share transfer events on the Aksjonærregisteroppgave page in Myntos. Use when someone asks how to maintain an aksjeeierbok, add shareholders, record dividends, share issues, or share sales in Myntos.
pages: /aksjonaer/
skill: myntos-aksjonaer
---

# Keep the shareholder register (aksjeeierbok)

Norwegian law (aksjeloven § 4-5) requires every aksjeselskap (AS or ASA) to
keep an up-to-date aksjeeierbok showing share classes, shareholders, share
counts, and changes. Myntos calculates Aksjonærregisteroppgaven (RF-1086)
directly from this register and cross-checks total share capital against ledger
account `2000` (Aksjekapital).

## They might ask

- «Hvordan fører jeg aksjeeierboken i Myntos?»
- «Hvordan legger jeg til en aksjonær eller registrerer utbytte?»
- «Hvordan registrerer jeg salg av aksjer eller nyemisjon?»
- "How do I update the shareholder register in Myntos?"

## Before they start

- They need the role «Medlem», «Administrator» or «Eier» to add or change
  entries («Leser» can view and download XML).
- For Norwegian personal shareholders, have their 11-digit fødselsnummer or
  D-nummer ready; for Norwegian company shareholders, their 9-digit
  organisasjonsnummer; for foreign shareholders, their UTL ID (if issued by
  Skatteetaten) or name and ISO-2 country code.

## Steps

1. Open «Aksjonærregisteroppgave» from the note at the bottom of «Oversikt»,
   from «Frister» («Til aksjonærregisteroppgaven →»), or go directly to
   `https://myntos.no/aksjonaer/`.
2. Pick the income year with the `‹` and `›` arrows at the top right.
3. **Set up the share class (existing company):**
   - If the company has an approved Altinn system user, click «Hent fra
     fjorårets oppgave» to import share classes, shareholders, and opening
     holdings directly from Skatteetaten's prefill.
   - Or click «Ny aksjeklasse», choose `01 Ordinære aksjer` (or another code
     from `01` to `13`), enter opening figures from 31.12 of the prior year,
     and click «Lagre aksjeklasse».
   - For a newly founded AS, leave opening figures at `0` and record the
     founding under «Hendelser i året».
4. **Add shareholders:** under «Aksjeeierbok», click «Legg til aksjonær», pick
   «Norsk person», «Norsk selskap (AS mv.)» or «Utenlandsk aksjonær», fill in
   name, identifier, address (optional), and any opening shares, then click
   «Lagre aksjonær». Use «Endre» on a row to update details later (leave
   fødselsnummer blank while editing to keep the stored number).
5. **Record events during the year:** under «Hendelser i året», click
   «Registrer hendelse», choose the share class, event type («Stiftelse»,
   «Nyemisjon», «Overdragelse (kjøp/salg/arv/gave)», «Utbytte», «Aksjesplitt»,
   «Aksjespleis», «Fondsemisjon», «Kapitalforhøyelse (økt pålydende)»,
   «Kapitalnedsettelse» or «Likvidasjon»), date, totals, and one line per
   affected shareholder («Legg til linje»), then click «Lagre hendelse».
6. Check any messages at the top of the page. Red items block RF-1086
   submission; yellow notes (such as a difference against ledger account `2000`)
   are advisory checks to verify.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| «fødselsnummeret har feil kontrollsiffer» | Typo in the 11-digit fødselsnummer or D-nummer | Check the digits and save again |
| «Aksjeklassen har registrerte hendelser og kan ikke slettes …» | Events or filings depend on the class | Delete the events under «Hendelser i året» first |
| «Aksjekapitalen i aksjeeierboken … avviker fra konto 2000 i regnskapet …» | Booked share capital on account `2000` does not match the register | Post the missing capital voucher (or opening balance) in the ledger, or fix the capital event in the register |

## Good to know

- Personal fødselsnummer is masked as `DDMMYY *****` in the register table and
  is included only in the RF-1086-U XML sent to Skatteetaten.
- If ownership and share capital did not change during the year, no events are
  needed; opening holdings carry straight through to 31.12.

## Related

- [File the shareholder register statement](https://myntos.no/llms/help/shareholders/file-the-shareholder-register-statement.md)
- [Set up an Altinn system user](https://myntos.no/llms/help/shareholders/set-up-altinn-system-user.md)
- An agent with a key can maintain the register through the API:
  [myntos-aksjonaer](https://myntos.no/llms/skills/myntos-aksjonaer.md)

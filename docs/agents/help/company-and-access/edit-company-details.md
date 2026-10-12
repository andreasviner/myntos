---
title: Edit the company's details
description: How an administrator changes the company's name, address or Foretaksregisteret status on the company page in Myntos, with the pencil next to each line. Use when someone asks how to change the company name or address, or says the details in Myntos are wrong.
pages: /organisasjon/
skill: myntos-selskap
---

# Edit the company's details

The company's name, address, VAT registration and Foretaksregisteret status
are printed on every invoice, so they have to be right. Each line in the
«Selskapet» section has its own pencil, and pressing it turns just that line
into a small form. Most of the values were copied from Enhetsregisteret; if
the register is right and Myntos is not, fetching again is often quicker than
typing.

## They might ask

- «Hvordan endrer jeg adressen til selskapet i Myntos?»
- «Navnet på selskapet er feil på fakturaen»
- «Hvordan endrer jeg firmanavnet?»
- "How do I change my company's address in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company. Everyone else sees the
  details without pencils:
  [Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md).

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. In «Selskapet», find the line and press the pencil at the end of it. Its
   label says what it changes: «Endre navn», «Endre adresse», «Endre
   foretaksregisteret», and so on.
3. Change the value:
   - «Navn»: one box.
   - «Adresse»: three boxes, for the street address, «Postnummer» and «Sted».
   - «Foretaksregisteret»: choose «Registrert i Foretaksregisteret» or «Ikke
     registrert».
   - «Merverdiavgift» and «Registrert fra» have their own article:
     [Set the VAT registration](https://myntos.no/llms/help/company-and-access/set-vat-registration.md).
   - «Organisasjonsnummer» too:
     [Correct the organisation number](https://myntos.no/llms/help/company-and-access/correct-the-organisation-number.md).
4. Press «Lagre». «Avbryt» closes the line without changing anything. The card
   says «Lagret.» when it is saved.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No pencils at all | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» of these books to make the change |
| «Organisasjonsform», «Næring», «Telefon» and similar have no pencil | They belong to Enhetsregisteret | Fetch from the register again: [Fetch the company from Enhetsregisteret again](https://myntos.no/llms/help/company-and-access/fetch-from-enhetsregisteret-again.md) |
| «Ble ikke lagret.» | The save failed | Try again; if it keeps failing, reload the page |

## Good to know

- Invoices already issued keep the details they were issued with. A change
  here applies to invoices made from now on; an issued invoice can only be
  corrected with a credit note (kreditnota).
- Fetching from Enhetsregisteret never changes the name, so a name changed by
  hand stays. It does overwrite the address and the VAT and
  Foretaksregisteret status with what the register says.
- With Foretaksregisteret on, every invoice prints the word
  «Foretaksregisteret» and the head office address, as the Norwegian
  bookkeeping rules (bokføringsforskriften § 5-1-2) require. Every AS, ASA and
  NUF is registered there; a sole trader (ENK) may or may not be.
- An AI agent with a key cannot change these details, by design. The person
  makes the change themselves.

## Related

- [See what is registered about the company](https://myntos.no/llms/help/company-and-access/see-company-details.md)
- [Fetch the company from Enhetsregisteret again](https://myntos.no/llms/help/company-and-access/fetch-from-enhetsregisteret-again.md)
- An agent with a key can read the details and fetch them from the register,
  but not edit them: [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)

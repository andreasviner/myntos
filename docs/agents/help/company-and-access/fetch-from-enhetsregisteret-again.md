---
title: Fetch the company from Enhetsregisteret again
description: How an administrator updates the company's address, VAT registration, Foretaksregisteret status and other register details in Myntos from Enhetsregisteret (Brønnøysundregistrene) with «Hent på nytt». Use when the company has moved, become VAT-registered or changed its business, and Myntos still shows the old details.
pages: /organisasjon/
skill: myntos-selskap
---

# Fetch the company from Enhetsregisteret again

Most of what Myntos knows about the company is a copy of Enhetsregisteret,
the national business register in Brønnøysund. When the business moves,
becomes VAT-registered or changes its industry, the register is updated but
nothing tells Myntos. «Hent på nytt» asks the register again and copies what
it says now. It changes what every future invoice says about the company.

## They might ask

- «Vi har flyttet. Hvordan oppdaterer jeg adressen fra Brønnøysund?»
- «Vi er mva-registrert nå, men Myntos vet det ikke»
- «Hvordan henter jeg opplysningene fra Enhetsregisteret på nytt?»
- "How do I update my company details from Brønnøysund in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company. Everyone else can read
  what was fetched, but has no «Hent på nytt».

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. Scroll to the section «Fra Enhetsregisteret». Next to the heading it says
   when it was last fetched («Hentet …»).
3. Press «Hent på nytt». The section says «Henter fra Enhetsregisteret …».
4. Read the answer:
   - «Alt stemmer med registeret.»: nothing had changed.
   - «Oppdatert fra registeret: …» names what changed, for example
     «adresse, mva-registrering». The «Selskapet» section above shows the new
     values.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Hent på nytt» | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |
| «fant ingen enhet med dette organisasjonsnummeret» | The organisation number in Myntos is not in the register, usually a typo | [Correct the organisation number](https://myntos.no/llms/help/company-and-access/correct-the-organisation-number.md) |
| «enheten er slettet fra Enhetsregisteret» | The business has been deleted from the register | Nothing was changed. Check the business's status with Brønnøysundregistrene |
| «vi fikk ikke kontakt med Enhetsregisteret» or «Enhetsregisteret svarte ikke som forventet» | Brønnøysund did not answer | Nothing was changed, and what is on screen stays. Try again later |

## Good to know

- What it updates: address, postnummer and sted, organisasjonsform, VAT
  registration, Foretaksregisteret, næringskode, website, phone, e-mail, and
  the main unit for a subunit. A value the register no longer has is not
  removed from Myntos.
- **The name is never changed.** If the company has changed its name, edit it
  by hand: [Edit the company's details](https://myntos.no/llms/help/company-and-access/edit-company-details.md).
- **A VAT status set by hand is overwritten** with the register's. If the
  company has just been registered and the register has not caught up, set it
  by hand and wait before fetching:
  [Set the VAT registration](https://myntos.no/llms/help/company-and-access/set-vat-registration.md).
- Invoices already issued are not changed.
- Myntos also fetches by itself when the company is set up, when it is
  confirmed, and when its organisation number is corrected.
- The board list in the same section is updated in the same fetch:
  [Read the board list](https://myntos.no/llms/help/company-and-access/read-the-board-list.md).

## Related

- [See what is registered about the company](https://myntos.no/llms/help/company-and-access/see-company-details.md)
- An agent with a key and permission to make changes can do this through the
  API: [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)

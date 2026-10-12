---
title: Set the VAT registration
description: How an administrator tells Myntos whether the company is registered for VAT (merverdiavgift, mva) and from which date, on the company page. Use when someone has just become VAT-registered, asks why their invoices show no VAT, or asks how to change the mva status in Myntos.
pages: /organisasjon/
skill: myntos-selskap
---

# Set the VAT registration

Whether the company is registered in Merverdiavgiftsregisteret (the VAT
register, «mva-registrert») decides whether its invoices may charge VAT and
what VAT codes new entries get. A business that is not registered must not
charge VAT: the money would be owed to the state anyway. Myntos normally
copies the status from Enhetsregisteret, but it can be set by hand, together
with the date the registration took effect.

## They might ask

- «Vi har nettopp blitt mva-registrert. Hva gjør jeg i Myntos?»
- «Hvorfor står det ikke mva på fakturaene mine?»
- «Hvordan endrer jeg mva-status i Myntos?»
- "How do I mark my company as VAT registered in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company.
- The registration itself is done with Skatteetaten (through Altinn), not in
  Myntos. Myntos only records that it has happened.

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. In «Selskapet», press the pencil at the end of «Merverdiavgift» («Endre
   merverdiavgift»).
3. Choose «Registrert i Merverdiavgiftsregisteret» or «Ikke registrert», and
   press «Lagre».
4. When the company is registered, a line «Registrert fra» appears. It says
   «Fra regnskapets start» until a date is set. If the registration took
   effect after the books began, press its pencil («Endre registrert fra»),
   type the date as ÅÅÅÅ-MM-DD (for example 2026-03-01), and press «Lagre».
   Leave it empty if the company was registered when the books started.

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No pencil | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |
| The status went back after «Hent på nytt» | Fetching from Enhetsregisteret copies the register's VAT status over the one set by hand | Wait until the register shows the registration, or set it again by hand and do not fetch until then |

## Good to know

- Only entries made from now on are affected. Everything already posted keeps
  the VAT code and rate it was posted with, and invoices already issued keep
  what they said.
- Purchases dated before «Registrert fra» give no VAT deduction (fradrag), so
  new entries before that date get no VAT, whenever they are entered.
- The easiest way to bring the status in line with the register is «Hent på
  nytt»:
  [Fetch the company from Enhetsregisteret again](https://myntos.no/llms/help/company-and-access/fetch-from-enhetsregisteret-again.md).
- How often the VAT return (mva-melding) is filed is set on the MVA page, not
  here.

## Related

- [Edit the company's details](https://myntos.no/llms/help/company-and-access/edit-company-details.md)
- [See what is registered about the company](https://myntos.no/llms/help/company-and-access/see-company-details.md)
- An agent with a key can read the VAT status and fetch it from the register,
  but not set it by hand: [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)

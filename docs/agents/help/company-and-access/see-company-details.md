---
title: See what is registered about the company
description: What the Selskapet section of the company page in Myntos shows (name, organisasjonsnummer, address, VAT registration, Foretaksregisteret, bank accounts, subscription, your own role) and where to find it. Use when someone asks where their company settings, orgnr or role are in Myntos.
pages: /organisasjon/
skill: myntos-selskap
---

# See what is registered about the company

Every company in Myntos (one set of books for one business) has a page with
its details: the name and organisasjonsnummer, the address, whether it is
registered for VAT, its bank accounts, and who has access. The page shows them
as facts, not as a form. A small pencil at the end of a line is how an
administrator changes that line.

## They might ask

- «Hvor finner jeg innstillingene for selskapet i Myntos?»
- «Hvor ser jeg organisasjonsnummeret vårt?»
- «Hvilken rolle har jeg i regnskapet?»
- "Where are the company settings in Myntos?"

## Before they start

Anyone with access to the company can open the page, whatever their role.
The page is about the company named at the top right of the screen; if that is
the wrong one, switch first:
[Switch between companies](https://myntos.no/llms/help/overview/switch-company.md).

## Steps

1. Click the company's name at the top right of any page, and choose
   «Innstillinger for selskapet» in the menu that opens. The gear icon next to
   it («Innstillinger») goes to the same page:
   `https://myntos.no/organisasjon/`.
2. The heading is the company's name. The first section, «Selskapet», lists:
   - «Navn» and «Organisasjonsnummer».
   - «Adresse», or «Ikke oppgitt» when there is none.
   - «Merverdiavgift»: «Registrert» or «Ikke registrert», and, when
     registered, «Registrert fra».
   - «Foretaksregisteret»: «Registrert» or «Ikke registrert».
   - «Abonnement»: the subscription for these books.
   - «Din rolle»: the person's own role in this company: «Leser», «Medlem»,
     «Administrator» or «Eier».
   - When the register has them: «Organisasjonsform», «Næring», «Nettsted»,
     «Telefon», «E-post» and «Underenhet av».
3. Under those, «Kontonummer» lists the company's bank accounts. The one
   marked «Hovedkonto» is where invoices are paid unless an invoice says
   otherwise.
4. The mark next to the heading «Selskapet» says «Bekreftet» or
   «Ubekreftet»: whether someone has proved through Altinn that they may act
   for the company.
5. Further down, «Tilgang» lists who has access to these books, and «Fra
   Enhetsregisteret» shows what the national business register says about the
   company, including its board.

## Good to know

- Most of these values come from Enhetsregisteret, the national register of
  Norwegian businesses. «Organisasjonsform», «Næring», «Nettsted», «Telefon»,
  «E-post» and «Underenhet av» belong to the register and have no pencil; they
  change when the company is fetched from the register again.
- A role belongs to one company. The same person can be «Eier» of their own
  books and «Leser» in a client's.
- «Abonnement» and «Din rolle» cannot be changed on this line. A role is
  changed by an administrator in the member list.
- A «Leser» or «Medlem» sees the same facts but no pencils and no buttons.

## Related

- [Edit the company's details](https://myntos.no/llms/help/company-and-access/edit-company-details.md)
- [Add a bank account](https://myntos.no/llms/help/company-and-access/add-a-bank-account.md)
- [Confirm the company with ID-porten](https://myntos.no/llms/help/company-and-access/confirm-the-company.md)
- [Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md)
- An agent with a key can read all of this through the API:
  [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)

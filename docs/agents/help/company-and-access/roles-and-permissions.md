---
title: Roles and what each may do
description: The four roles a person can hold in a company's books in Myntos (Leser, Medlem, Administrator, Eier), what each may do, and the rules about owners. Use when someone asks which role to give a colleague or accountant, why they cannot see a button, or what their own role allows.
pages: /organisasjon/
skill: myntos-selskap
---

# Roles and what each may do

Every person with access to a company's books holds one of four roles in
that company. The role decides what they see and can do: a button the role
does not allow is simply not shown, so a missing button is usually a role,
not a fault. A role belongs to one company: the same person can be «Eier» of
their own books and «Leser» in a client's.

## They might ask

- «Hvilken rolle bør regnskapsføreren min ha?»
- «Hva er forskjellen på Medlem og Administrator i Myntos?»
- «Hvorfor ser jeg ikke knappen?»
- "What can a Leser do in Myntos?"

## The roles

| Role | As the invitation form describes it | May do |
| --- | --- | --- |
| «Leser» | «Leser: ser regnskapet, kan ikke føre» | Read everything: the books, the invoices, the company page and its member list. Cannot register or change anything. Suits an auditor or a board member who only wants to look |
| «Medlem» | «Medlem: kan føre bilag» | Everything a Leser may, and keep the books: register payments and bilag, make and issue invoices, add customers and suppliers, work with bank statements, prepare the VAT return. Suits a bookkeeper |
| «Administrator» | «Administrator: kan også styre selskaper og medlemmer» | Everything a Medlem may, and decide about the company: its details, bank accounts and VAT registration, confirming it with ID-porten, fetching it from Enhetsregisteret, the chart of accounts (kontoplan), the VAT period settings, closing and reopening a financial year, the company's KI switch, what the company shares with Myntos; and decide about access: invite, change roles, remove members, handle pending invitations |
| «Eier» | «Eier: kan alt, inkludert abonnement» | Everything an Administrator may, and the subscription when there is one to manage. The only role that can make someone else «Eier», or change or remove another «Eier» |

The person's own role is on the company page under «Selskapet», as «Din
rolle».

## Rules worth knowing

- **There is always at least one «Eier».** The last one cannot be demoted or
  removed, and cannot give up their own access, until someone else is made
  «Eier».
- **An «Administrator» cannot touch an «Eier».** They cannot invite one,
  promote anyone to one, or change or remove one.
- **Anyone can give up their own access**, whatever their role:
  [Give up your access](https://myntos.no/llms/help/company-and-access/give-up-your-access.md).
- **Sending to Skatteetaten** also needs the person's own ID-porten login and
  rights in Altinn, whatever their role in Myntos.
- **An AI agent never does more than its person.** A key someone makes for
  their AI acts with that person's role, and loses whatever the role loses:
  [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).

## Related

- [Change someone's role](https://myntos.no/llms/help/company-and-access/change-someones-role.md)
- [Invite a colleague](https://myntos.no/llms/help/company-and-access/invite-a-colleague.md)
- [Remove a member](https://myntos.no/llms/help/company-and-access/remove-a-member.md)
- An agent with a key reads its person's role and permissions through the
  API: [myntos-selskap](https://myntos.no/llms/skills/myntos-selskap.md)

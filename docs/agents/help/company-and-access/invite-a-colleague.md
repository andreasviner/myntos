---
title: Invite a colleague
description: How an administrator gives a colleague, accountant or co-owner access to one company's books in Myntos by sending an invitation from the Tilgang section of the company page. Use when someone asks how to give another person access, add a user, or share the books with their accountant.
pages: /organisasjon/
skill: myntos-selskap
---

# Invite a colleague

Access to the books is given per company, by invitation. The colleague gets
an e-mail with a link, signs in or creates an account with that same e-mail
address, and can then open this company's books in the role they were given,
and nothing else: not the other companies the inviter keeps.

## They might ask

- «Hvordan gir jeg regnskapsføreren min tilgang i Myntos?»
- «Hvordan legger jeg til en bruker?»
- «Kan kollegaen min også føre i regnskapet?»
- "How do I give my accountant access in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company. Only an «Eier» can
  invite someone as «Eier».
- The colleague's e-mail address: the one they will sign in with.
- Which role to give:
  [Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md).

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. In the section «Tilgang», on the card «Medlemmer i …», press «Inviter en
   kollega».
3. In «E-postadresse», type the colleague's address. The invitation works
   only for that address; a forwarded link does not let anyone else in.
4. In «Rolle», choose one:
   - «Leser: ser regnskapet, kan ikke føre»
   - «Medlem: kan føre bilag» (chosen unless changed)
   - «Administrator: kan også styre selskaper og medlemmer»
   - «Eier: kan alt, inkludert abonnement»
5. Press «Send invitasjon». The card says «Invitasjonen er sendt til …», and
   the invitation appears under «Ventende invitasjoner» until it is accepted.
6. The colleague opens the link in the e-mail within 7 days:
   [Accept an invitation](https://myntos.no/llms/help/getting-started/accept-an-invitation.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Inviter en kollega» | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |
| «Invitasjonen for … er opprettet, men e-posten gikk ikke ut.» and «Vi fikk ikke sendt e-posten.» | The invitation exists, but the e-mail failed | Press «Kopier» and send the link to the colleague some other way. It is shown only this once; later, use «Send på nytt» |
| «denne e-postadressen har allerede tilgang til selskapet» | The colleague is already a member | Change their role instead: [Change someone's role](https://myntos.no/llms/help/company-and-access/change-someones-role.md) |
| «only an owner can grant the owner role» | An «Administrator» chose «Eier» | Choose «Administrator», or ask an «Eier» to send it |
| The colleague says the link does not work | It expired after 7 days, they are signed in with another address, or it was sent again and the old link stopped | [Send an invitation again](https://myntos.no/llms/help/company-and-access/send-an-invitation-again.md) |

## Good to know

- One invitation gives one company. To give access to several of an
  account's companies, switch to each and invite there.
- Inviting an address that already has a pending invitation replaces it; the
  earlier link stops working.
- To invite someone straight from the board list, so the board row shows
  «Invitert»: [Invite a board member](https://myntos.no/llms/help/company-and-access/invite-a-board-member.md).
- An AI assistant is not invited this way and must never be given a person's
  password. It gets its own key:
  [Invite an AI](https://myntos.no/llms/help/ai/invite-an-ai.md).
- No AI agent with a key can invite anyone or change who has access.

## Related

- [Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md)
- [Withdraw an invitation](https://myntos.no/llms/help/company-and-access/withdraw-an-invitation.md)
- [Remove a member](https://myntos.no/llms/help/company-and-access/remove-a-member.md)

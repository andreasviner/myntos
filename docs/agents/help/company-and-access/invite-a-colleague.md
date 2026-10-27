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
5. Press «Send invitasjon». The card says «Invitasjonen er sendt til …» and
   shows the link under «Vi kan ikke se om e-posten kommer fram. …», with
   «Kopier». The invitation appears under «Ventende invitasjoner» until it is
   accepted.
6. If the colleague says no e-mail came, press «Kopier» and send them the link
   some other way (a text message, a chat). It is shown only this once.
7. The colleague opens the link within 7 days:
   [Accept an invitation](https://myntos.no/llms/help/getting-started/accept-an-invitation.md).

## If something stops them

| What they see | Why | What to do |
| --- | --- | --- |
| No «Inviter en kollega» | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |
| «Invitasjonen for … er opprettet, men e-posten gikk ikke ut.» and «Vi fikk ikke sendt e-posten.» | The invitation exists, but the e-mail failed | Press «Kopier» and send the link to the colleague some other way. It is shown only this once; later, use «Send på nytt» |
| The colleague got no e-mail, and the page with the link is closed | Myntos only sees that the mail service took the message; the service can still refuse it as spam, or it lands in a spam folder | [Send an invitation again](https://myntos.no/llms/help/company-and-access/send-an-invitation-again.md) and copy the new link the page shows |
| «denne e-postadressen har allerede tilgang til selskapet» | The colleague is already a member | Change their role instead: [Change someone's role](https://myntos.no/llms/help/company-and-access/change-someones-role.md) |
| «only an owner can grant the owner role» | An «Administrator» chose «Eier» | Choose «Administrator», or ask an «Eier» to send it |
| The colleague says the link does not work | It expired after 7 days, they are signed in with another address, or it was sent again and the old link stopped | [Send an invitation again](https://myntos.no/llms/help/company-and-access/send-an-invitation-again.md) |

## Good to know

- One invitation gives one company. To give access to several of an
  account's companies, switch to each and invite there.
- Inviting an address that already has a pending invitation replaces it; the
  earlier link stops working.
- The e-mail names the inviter's own address, for a colleague unsure whether
  it is genuine, once the inviter has confirmed that address. A reply to the
  e-mail itself does not reach the inviter.
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

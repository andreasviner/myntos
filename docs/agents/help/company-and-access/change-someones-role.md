---
title: Change someone's role
description: How an administrator changes what a member may do in a company's books in Myntos, by picking a new role in the member list on the company page. Use when someone wants to give a colleague more or less access, make someone an owner, or step down as owner.
pages: /organisasjon/
skill: myntos-selskap
---

# Change someone's role

A member's role is changed in the member list, and the change takes effect
at once, with no confirmation step. It applies to this company only.

## They might ask

- «Hvordan gjør jeg en kollega til administrator?»
- «Hvordan gir jeg regnskapsføreren mindre tilgang?»
- «Hvordan overfører jeg eierskapet til en annen?»
- "How do I change a user's role in Myntos?"

## Before they start

- The role «Administrator» or «Eier» in this company. Only an «Eier» can make
  someone «Eier» or change another «Eier»'s role.
- What each role allows:
  [Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md).

## Steps

1. Open the company page: click the company's name at the top right and
   choose «Innstillinger for selskapet» (`https://myntos.no/organisasjon/`).
2. Under «Tilgang», in the table «Medlemmer i …», find the person.
3. In the «Rolle» column, pick the new role from the list: «Leser»,
   «Medlem», «Administrator» or «Eier». It is saved as soon as it is picked.

To hand over ownership: first make the other person «Eier», then change one's
own role (or give up access).

## If something stops them

The message appears at the top of the page, and the list goes back to the
role that is still in force.

| What they see | Why | What to do |
| --- | --- | --- |
| The role is plain text, with no list | Their role is «Leser» or «Medlem» | Ask an «Administrator» or «Eier» |
| «only an owner can grant the owner role» | An «Administrator» tried to make someone «Eier» | Ask an «Eier» |
| «only an owner can modify another owner» | An «Administrator» tried to change an «Eier» | Ask an «Eier» |
| «the organization would be left without an owner; appoint another owner first» | This is the last «Eier» | Make someone else «Eier» first |
| «Rollen kunne ikke endres.» | The change failed for another reason | Reload the page and try again |

## Good to know

- An AI key the person made keeps working, but can only do what the new role
  allows.
- A role cannot be changed on a pending invitation. Withdraw it and invite
  again with the right role:
  [Withdraw an invitation](https://myntos.no/llms/help/company-and-access/withdraw-an-invitation.md).
- No AI agent with a key can change anyone's role.

## Related

- [Roles and what each may do](https://myntos.no/llms/help/company-and-access/roles-and-permissions.md)
- [Remove a member](https://myntos.no/llms/help/company-and-access/remove-a-member.md)

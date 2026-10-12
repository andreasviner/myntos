# Who on the board can open the books

Two lists on the company page answer almost the same question. **Medlemmer**
says who can sign in and see the accounts. **Roller**, from Enhetsregisteret,
says who is registered as being behind the company: styrets leder, daglig leder,
revisor. They are usually meant to be the same people, and until the access mark
existed nothing said whether they were. A resigned chair who kept their login
and a daglig leder nobody ever invited both looked exactly like a company whose
access was in order.

The mark in the Merknad column joins the two lists. Everything hard about it is
that **the join has no key**.

## There is no identifier to join on

Brønnøysund gives a name. Not a fødselsnummer, and not even a birth date: the
register does pair a person with one, and
[`integrations/brreg.py`](../backend/src/myntos/integrations/brreg.py) drops it
on the way in, because a board list is what an accountant needs and a date of
birth is personal data with no bookkeeping use. That decision stands. It means a
name is the only thing there is to match on, and the design has to be honest
about what a name is worth rather than pretending to a certainty it cannot have.

So the rule is not "matched or not matched". It is **how much this particular
match is worth**, and that depends entirely on where the other name came from.

## The five states

`RegisterAccess` in
[`domain/identity/register_match.py`](../backend/src/myntos/domain/identity/register_match.py),
served on every role by `GET /companies/{id}/register` and `POST
/companies/{id}/hent`, rendered as a pill in the Merknad column.

| Value | Shown as | Means |
| --- | --- | --- |
| `bekreftet` | Har tilgang | A member whose **ID-porten** login carries this name. That name comes from Folkeregisteret, which is where the register's own names come from, so this is one administrative fact twice |
| `trolig` | Har trolig tilgang | A member whose **account** name matches. The account name is whatever the person typed at signup: probably them, and nobody has proved it |
| `invitert` | Invitert | A pending invitation addressed to this name. Only an invitation that carries a name can reach it, and the **Inviter** button in this table is the only thing that attaches one |
| `ingen` | Ingen tilgang | Nobody with access to these books goes by this name |
| `selskap` | (nothing) | The holder is a revisor or regnskapsfører firm. It is not a person with a login here, and "ingen tilgang" would answer a question nobody asked |

The invite form does not ask for a name. An address and a role is the whole
errand, the name is a label rather than a check, and an empty field above "Send
invitasjon" asked for something the invitation does not need in order to work.
So `invitert` is not a state an ordinary invitation can be in: the board list is
the one place a name exists *before* the invitation does, which is why inviting
from a row here is the only route to it. Inviting the same person from the
member card instead leaves their row saying `ingen` until they accept.

**The difference between `bekreftet` and `trolig` is the whole reason linking an
ID-porten login is worth offering.** It is the step that turns a guess into an
answer, and the member list says so in its own ID-porten column.

Two properties hold, and both are load-bearing:

- **The mark grants nothing and enforces nothing.** It is shown to an
  administrator who knows their own colleagues. Nothing in the backend reads it
  back, and no permission depends on it. That is what makes an occasional false
  positive tolerable rather than a security bug.
- **Where a name came from outranks how exactly it matches.** A verified name
  agreeing on first-and-last beats an account name agreeing letter for letter:
  anybody can type a name into a signup form, and only the person themselves can
  pass ID-porten as it. Exactness breaks ties *within* one source, never across
  them.
- **A BankID sign-in to Myntos is not a verified name.** An account made with
  BankID carries the name BankID gave, and it is matched as an account name,
  `trolig`. Reading it as `bekreftet` would use a BankID login to raise the
  trust of an identification of our own, which is the ID-veksling BankID's
  merchant terms forbid ([integrations/bankid.md](integrations/bankid.md)). Only
  the ID-porten link makes `bekreftet`.

## What counts as the same name

`name_key` casefolds, collapses whitespace, and treats hyphens and periods as
spaces, so "ANNE-LISE EK" and "Anne Lise Ek" are one name. It normalises Unicode
composition, so a decomposed å equals a composed one.

It deliberately does **not** accent-fold. å and a are different letters in
Norwegian, "Håkon" and "Hakon" are different names, and folding them would be a
false match dressed up as tolerance.

`short_key` drops the middle names, because people sign up as "Andreas Lindeman"
and stand in the register as "Andreas Edvardsen Lindeman". Both sides are
reduced before the looser comparison, so it works whichever spelling is the
fuller one. This admits a false positive: a father and son, or two brothers,
sharing both names on one set of books. That cost is accepted for the reason
above, and it is why the matched name is shown under the mark when it differs
from the register's, so the reader can see the join and disagree with it.

## Resolved on read, never stored

`company_register_snapshot` holds Brønnøysund's answer verbatim, so the page can
render without a round trip to Brønnøysund on every visit. **Access is not part
of that answer.** It changes when somebody is invited, accepts, links an
ID-porten login or is removed, and the register hears about none of those.

That is why `RoleAccessResponse` is a subclass of `RoleResponse` rather than
three more fields on it: the snapshot stores a dump of `RoleResponse`, so a mark
added there would be written into the snapshot and served back weeks later as
though the register had said it. It would be right on the day it was stored and
quietly wrong every day after.

The consequence for the frontend is that anything changing the member list has
to re-read the stored register, which `refresh()` in
[`organisasjon.astro`](../frontend/src/pages/organisasjon.astro) does. It is a
local read, not a call to Brønnøysund.

## The invited address is not for everyone

The register list is readable by anyone who can open the company, a viewer included.
The invitation list is not: it needs `member:manage`. So `access_email` is
withheld unless the caller holds that permission, while the `invitert` state
itself still shows. "This board member has been asked" is a fact about the
board; the address it was asked at belongs to the invitation list.

## The ID-porten link behind `bekreftet`

`user_government_identity` (one row per account) is written by the ID-porten
callback in [`api/v1/skatt.py`](../backend/src/myntos/api/v1/skatt.py), after the
assurance-level check and never before it.

It is deliberately **not** `government_login_attempt`, which already holds a
`pid`. That row is an authorization: minutes old, single-use, worthless once its
token expires. Who somebody is does not expire, and folding the two together
would mean the link vanishing an hour after it was made, which is exactly when
it starts being useful. `GET /skatt/status` reports `logged_in` and `linked`
separately for the same reason.

The `full_name` on it comes from ID-porten's `name` claim, which requires the
`profile` scope on the client. **A deployment without that scope is a supported
deployment**: the link still exists and a submission is still attributed to it,
the register match just falls back to the account's own name and says `trolig`
where it could have said `bekreftet`. See
[integrations/maskinporten.md](integrations/maskinporten.md#which-scope-is-where-probed-2026-08-28)
and the `IDPORTEN_SCOPES` comment in `.env.example`.

## Where the code is

| Piece | File |
| --- | --- |
| The rule, pure and testable without a company or a register | [`domain/identity/register_match.py`](../backend/src/myntos/domain/identity/register_match.py) |
| The rule's tests | [`test_register_match.py`](../backend/tests/test_register_match.py) |
| Resolving it against the membership tables | `_with_access` in [`api/v1/companies.py`](../backend/src/myntos/api/v1/companies.py) |
| End-to-end, including the snapshot and the viewer boundary | [`test_register_access_api.py`](../backend/tests/test_register_access_api.py) |
| The durable ID-porten link | `UserGovernmentIdentity` in [`domain/identity/models.py`](../backend/src/myntos/domain/identity/models.py) |
| The pill and the Inviter button | [`organisasjon.astro`](../frontend/src/pages/organisasjon.astro) |

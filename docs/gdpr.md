# Deleting a person without deleting the books

Somebody can delete their Myntos account themselves, from `/konto/`. That is
GDPR art. 17, and it runs straight into bokføringsloven: the account is the
person's, but much of what they did while signed in is a company's
bookkeeping, which the company must keep for five years whatever happens to the
person who kept it. This file is where the line between the two is drawn, and
why it is drawn there. Read it before touching
[`domain/identity/erasure.py`](../backend/src/myntos/domain/identity/erasure.py),
any column that points at `app_user`, or the session list.

A company can be deleted too, and there the same law points the other way:
the books must outlive the owner's request by five years, and must not
outlive the law. [Deleting a company](#deleting-a-company) is that half, and
[`domain/identity/closure.py`](../backend/src/myntos/domain/identity/closure.py)
is its code.

---

## What is whose

| Personal data of the account: **deleted** | The books' record: **kept** |
| --- | --- |
| Email address, password hash, lockout counters, confirmation, reset and switch-to-e-mail links, and the record of each "you sign in with BankID" mail (`user_email_token`) | Posted vouchers (`gl_transaction`), with `created_by_user_id` |
| Linked Google / Microsoft / BankID identities (`user_identity`), and unfinished links to one (`oauth_login_attempt.user_id`) | The bilag archive (`bilag_attachment`), with `uploaded_by_user_id` |
| The ID-porten link and its fødselsnummer (`user_government_identity`) | MVA filings, with who validated and who submitted them, and who recorded a term as filed from another program or took that back (`mva_filing_elsewhere.withdrawn_by_user_id`) |
| Government login attempts, which carry a pid and a sealed token | Who confirmed a company's ownership in Altinn (`company.verified_by_user_id`) |
| Every session, with its IP address and user agent | Salgsdokumenter, parties, opening balances |
| | Imported bank statements and who imported them (`bank_statement_file.uploaded_by_user_id`), and who confirmed a bank match (`bank_statement_line.matched_by_user_id`) |
| | Who closed or reopened a financial year (`financial_year_event.user_id`) |
| | The written reason beside a manual voucher or its reversal (`voucher_explanation`), which has no actor column: the voucher's own author is its author, so it names nobody the voucher does not |
| | Who chose an account's næringsspesifikasjon post (`account_grouping.chosen_by_user_id`) |
| The tokens of every key the person made for an agent (`agent_token`), and the name of the program that last used each key (`agent_key.last_used_client`) | Each key the person made, stopped: its name, its company, its dates (`agent_key`), because a voucher registered through it says so (`gl_transaction.created_via_agent_key_id`) |
| The files in requests those keys were still waiting to have approved (`agent_approval_file.content`) | The requests themselves, as a record of what was asked and decided (`agent_approval`, with `decided_by_user_id` SET NULL like every actor column) |
| | SAF-T imports and who started and posted them (`saft_import`, actor columns SET NULL like every other: the vouchers it posted name their author themselves, so the row keeps no one's name). The uploaded files (`saft_import_file.content`) are the company's books, not the person's, and are emptied once posted, thrown away or 14 days old whatever happens to the person |
| Every company and account membership | The person's **id and name**, on a tombstone row, when any of the above names them |
| Invitations addressed to them, pending or accepted | Invitations they *sent* to somebody else |

The left column is everything that exists because the person has an account.
The right column is everything that exists because a company keeps books, and
art. 17(3)(b) exempts processing that a legal obligation requires.
Bokføringsloven § 13 is that obligation for the books themselves.

### Invitations they sent stay

Each was given on behalf of a company to somebody else, and that person's way
in is not the sender's to withdraw by leaving. On the pending ones the link to
the sender is cut, so a stranger opening the invitation is not shown the name
of somebody who has left (`inviter_name` is already `None`-able for exactly
this). Invitations addressed *to* them go, because the address is theirs.

---

## Tombstone, not pseudonym, and only when needed

Every actor column (`created_by_user_id`, `uploaded_by_user_id`, the MVA
columns, `verified_by_user_id`) is `ON DELETE SET NULL`, deliberately: deleting
a person must never delete the bookkeeping they did. But SET NULL alone loses
something the books must keep. A voucher whose author is NULL reads «ukjent» in
the register, and its SAF-T `Transaction/SourceID` (exported as the user id)
stops meaning anyone. Bokføringsforskriften requires that it be possible to see
who registered an entry, for as long as the entry is kept, and an auditor reads
`SourceID` to ask exactly that. *(The paragraph number is not cited here because
it was not re-verified for this change; check it before relying on one.)*

So the account row has two possible ends:

- **Nothing retained names them: deleted outright.** The row goes, SET NULL
  takes care of any invitation that pointed at it, and nothing remains.
- **Something retained names them: a tombstone.** Same id, same `full_name`,
  `deleted_at` set, and nothing else: the email becomes
  `deleted-{id}@erased.invalid` (RFC 2606 reserved, refused by EmailStr, so
  nobody can register it first and make the erasure collide), no password, not
  active, no identities, no sessions, no memberships. The real address is free
  for a new signup, which starts from nothing.

**Pseudonymising the name was considered and rejected.** Replacing it with
"Slettet bruker 41" keeps an actor in the letter and defeats the purpose: the
company could no longer tell an auditor who booked an entry, and the books are
the company's record, not Myntos's to make less true. The name is the minimum
that keeps the record answerable; the address, the login and the
fødselsnummer are not needed for that and are not kept.

The tombstone's name is shown only where it already was: on the vouchers that
person entered, to people who can open those books
([company-isolation.md](company-isolation.md#the-one-query-that-reads-a-row-by-id-on-purpose)
explains why that lookup is safe). It is in no member list, since the
memberships are gone.

---

## Keys for agents

A key a person made (docs/agents-and-api.md) is a way into the books that works
without anybody signing in, so erasure stops it with the rest: its tokens are
deleted and it is marked stopped, `account_deleted`. The row stays, with the
name the person gave it and nothing that identifies a device: a voucher posted
through a key reads «Kari Nordmann via «Claude»», and the second half is part
of how that entry was registered, as much as the first.

`agent_key.user_id` cascades, which looks like it could delete that record and
does not: a voucher made through a key names the key's person as its author
too, so a person any such voucher names is kept as a tombstone, and their keys
with them. A person deleted outright registered nothing, with a key or
without.

A request a key was waiting to have approved (agents-and-api.md rule 13) can
never run once the key stops, so erasure empties the files sent with it. The
request row stays with the key, as the record of what was asked; one that ran
left its files in the bilag archive, where the books keep them. Files are
emptied the same way whenever a request stops waiting for any other reason.

The name of the agent's software (`last_used_client`, the first word of the
User-Agent it sent) is what the Tilgang page shows so a person can tell their
own agent from somebody else's. Coarse on screen like the session list, and
cleared on erasure, since only the person could ask what it was.

## When deletion is refused

Never half-done. Erasure is refused, with a message naming each company and
what to do, while the person is the **last owner** of books somebody still
needs:

- **Other people are on the books.** They would be left with no owner, which
  nothing else in Myntos allows either (the last-owner rule on
  `CompanyMembership`). Make one of them an owner first.
- **Anything has been entered.** A voucher, a bilag, a salgsdokument (even a
  draft), an MVA filing, a party or an opening balance. The books must stay
  readable and printable for five years (bokføringsforskriften § 7-5 puts that
  on the company; see ARCHITECTURE.md § Why EEA hosting is legal), and only an
  owner can hand them on. Invite whoever takes over as owner first.

Books with **nothing entered and nobody else on them** are deleted with the
account: there is nothing to keep, and leaving them would leave an unopenable
company holding a confirmed claim on an orgnr (`uq_company_verified_orgnr`),
which would stop its real owner setting up books.

Books with entries that nobody will take over have a third way out: the owner
**deletes the company** first (below), and the refusal says so. Books already
deleted that way never block the erasure (fate `closed`): they are kept until
their date whoever owns them, and the owner's membership goes with the rest,
which means nobody can open them again before the purge.

An account (`organization`) left with no members and no companies is deleted.
One that still holds companies keeps them, and if its name was the placeholder
signup made from this person's name ("Kari Nordmann (privat)"), it is renamed,
slug included. A name somebody chose is left alone, because nothing knows whose
it is.

## Deleting a company

An owner can delete a company from `/slett-selskap/`. The person's data is
not what is at stake here; the books are, and two laws decide what may happen
to them.

**The books must outlive the request.** Bokføringsloven § 13 second paragraph:
the material in nr. 1 to 4 of the first paragraph (annual accounts and other
mandatory reporting, specifications, the documentation of what was entered,
the auditor's letters) is kept for **five years after the end of the financial
year**, and nr. 5 to 8 (agreements, correspondence, packing slips, price
lists) for three and a half. Lov 19. juni 2026 nr. 39, in force from 2027,
rewrites the paragraph and keeps both periods (read on Lovdata 2026-10-15).
The duty is the company's, and it does not end with the business: aksjeloven
§ 16-10 fourth paragraph makes whoever winds a company up see to it, and
Skatteetaten's principle statement «Oppbevaring av regnskapsmateriale ved
avvikling og konkurs» says there is no general exemption on winding-up or
bankruptcy (only a dispensation on application).

**The books must not outlive the law.** Past their retention they hold
personal data about customers, suppliers and employees that nothing requires
any more, and art. 5(1)(e) says it goes.

So deleting a company is one of two things:

| | When | What happens |
| --- | --- | --- |
| **Deleted at once** | Nothing entered: `has_books` in `erasure.py` says no, the same question the account erasure asks | The company row goes, and the cascades take what setup seeded |
| **Locked, then deleted** | Anything entered | `closed_at` and `retain_until` are set; the purge deletes it the day after `retain_until` |

**`retain_until`** is 31 December, five years after the later of this year
and the last financial year with a voucher in it (`closure.retention_end`).
This year because anything entered up to today belongs to a year no later than
this one; the last voucher year because a voucher may be dated ahead. Financial
years in Myntos are calendar years. The date is the date in Norway, not UTC:
deleted at 00:30 on 1 January, a company has entered something in the new year.

**Locked means**, all at once, in `closure.close_company`:

- `get_company_for_user` refuses it with the same 404 as an id nobody holds,
  so every route under `/companies/{id}/` is shut to it, the owner's included,
  and `/companies` leaves it out;
- every membership but the owners' is deleted, pending invitations go, every
  key stops (`company_closed`) with its tokens, waiting approvals lose their
  files, and `agent_access_enabled` is switched off;
- uploaded SAF-T import files are emptied (they are a working copy, and the
  14-day sweep only runs when somebody reads the import);
- the confirmed claim on the orgnr is released: `uq_company_verified_orgnr`
  binds open companies only, so whoever runs the business next can confirm
  it in their own books.

Refused while a SAF-T import is being read or posted into the books.

**The orgnr is free in the account too.** `uq_company_open_orgnr` allows one
set of *open* books per orgnr per account, so the same number can be set up
again at once beside books kept locked (migration `f4a9d2c63b18`). The first
version kept the account-wide constraint, and a person who had tried Myntos
with a made-up orgnr, deleted it to start over, was refused for five years.
Unconfirmed books are not deleted at once on the strength of being
unconfirmed: plenty of real companies never confirm, and Myntos cannot tell a
made-up number from a real one.

**An owner may open it again** (`POST /companies/slettede/{id}/gjenapne`, the
«Slettede selskaper» card on `/konto/`) until the purge. Only the books come
back; colleagues, keys and the agent switch do not. Refused, `reopen_blocked`,
when new books for the same orgnr have been set up in the account since
(delete those first), or somebody has confirmed the same orgnr in other books
meanwhile: two open
confirmed claims is what the index forbids, and which one is right is not
Myntos's to decide. This is what makes locking rather than deleting worth it:
SAF-T is the company's copy of the entries, but it carries no bilag, so a
company that left Myntos and is asked for a receipt by a bokettersyn three
years later gets it by opening the books again.

**The owner is sent to SAF-T first.** The page lists every year with vouchers
and links each to `/saft/?aar=…`, and asks them to tick that they have the
files before the button works. That is the "tell them to take their books"
half of the duty, and the part Myntos cannot do for them.

**The purge** (`closure.purge_expired`) runs once a day in the backend process
(`myntos.main._purge_daily`), so it is deployed with the code that decides
what it deletes and cannot be forgotten on a rebuilt machine. For each
company past its date it deletes the stored files first (bilag and their
thumbnails, bank statements, MVA receipts: `DocumentStorage.delete`, which
nothing else calls), then the rows, referrers first, because a few links
inside the books are RESTRICT on purpose and PostgreSQL checks those at once.
One commit per company. Files before rows, because a failure halfway then
leaves rows pointing at missing files in books past retention, which is
harmless, rather than files nothing names, which nothing would ever delete. An
account left with no members and no companies goes too.

**Which company, as well as who.** Deleting also asks everybody to type the
company's name (`closure.require_name`, case and spacing ignored, checked
before the password so a typo never counts towards the lockout). The proof of
identity says it is the owner; the name says which of their companies they
meant, since the button sits on the settings page of whichever is active.
Considered and rejected: a typed «SLETT» instead of any proof for accounts
without a password. Every account has either a password or a provider it can
sign in with again in two clicks, so there is no account the weaker check
would be needed for, and the ID-porten login is not available for this
(Digdir's terms allow it for reaching public services, not as a product's own
step-up).

**Not recorded: who deleted it.** Only an owner can, it can be undone, and an
actor column would be one more place the erasure has to keep a name.

**Why not delete at once when the owner insists.** Myntos is the company's
processor for the books, and art. 28(3)(g) lets a processor delete on
instruction at the end of the service. It was considered and rejected: SAF-T
leaves the bilag behind, so deleting on request would let a company destroy
the primary documentation it must keep, in one click, from a session that may
not be the owner's. Keeping it locked costs a few megabytes; the other mistake
cannot be undone.

## Proving it is the owner

A session is not enough: it is what a borrowed laptop or a stolen cookie gives
somebody, and this is the one action nothing can undo.

- An account with a **password** must type it, every time. A wrong one counts
  towards the same lockout as a wrong login, so a hijacked session cannot use
  this endpoint as an unthrottled password oracle.
- An account with **no password** (Google, Microsoft or BankID only) has nothing to
  type, so the session must have been created within the last ten minutes:
  the page offers "logg inn på nytt" through the same provider and comes back.

Nobody is ever asked for a password they never made: whether the field is
shown is the backend's answer (`requires_password`), and an account that
signed up with BankID, Google or Microsoft is offered «Bekreft med …», which is
that fresh login.

**Deleting a company is gentler** (`or_recent_login=True`): a sign-in within
the window is proof for every account, password or not. The page asks for
nothing else: no password field, and when the sign-in is too old, one line
and «Logg inn på nytt», which signs out and back in through whatever the
person uses and returns to the page (`signInAgain` in `lib/session.ts`). A
password field next to provider buttons was tried first and read as
clutter, and it asked a Google user for a password they had once set and
never used, which was reported. Accepted there because a deleted company can
be opened again, or lost nothing; deleting the account and switching to
BankID cannot be undone and keep the strict rule, where an account with a
password always types it. The backend still takes a password on that route,
for API clients that have one.

The same proof (`service.confirm_identity`) guards deleting a company, and
the switch to BankID, which
deletes the password and every other way in and so is, for the owner, almost
as final: a hijacked session that could do it would keep the account.

Both refusals are 403, never 401: a 401 tells the frontend the person is signed
out, and would send them to the login page mid-deletion.

---

## The session list

`GET /account/sessions` lists the live sessions (not revoked, not expired, not
idle past the timeout), marks the one in hand, and lets the person end any one
of them or all the others. A session id that is not the caller's is a 404, the
same as one that does not exist.

**What is stored did not change.** `user_session` has carried the user agent
(truncated to 255 characters) and the IP address since the first login, for
the purpose the personvernerklæring already states: recognising your own
logins, and investigating an incident. Nothing new was added for this list.

**What is shown is less than what is stored.** The list says "Firefox på
Linux", derived on read by `auth/devices.py`, and never the raw user agent
(which is closer to a fingerprint than a description) or the IP address. The
address answers the incident question, which is asked of us; on screen it
would invite a lookup of where it is, and answering that means sending it to a
geolocation service.

Ended sessions stay as rows for their audit value, until the account is
deleted: then they go, because "when was I logged out, and from where" is a
question only the person could ask.

## BankID

A BankID login keeps **one value**: the broker's `sub` on `user_identity`, a
pseudonym Idura documents as stable per tenant. Everything else the broker
sends is read and dropped, or not read at all:

- **The fødselsnummer is not asked for.** `BANKID_SCOPE` is `openid`, and Idura
  releases the number only for the `ssn` scope. Signing in needs a stable
  identifier, which `sub` already is, and personopplysningsloven § 12 allows a
  fødselsnummer only where there is a "saklig behov for sikker identifisering"
  that the method is needed for. A login has no such need. (The ID-porten link
  does store one, in `user_government_identity`, because filings are
  attributed to it; that is a different purpose with its own basis, and it is
  not touched by BankID.) If the broker sends the number anyway, for instance
  from an application switched to Idura's static scope strategy with SSN on,
  `bankid_profile` discards it and logs a warning without the value. A test
  reads the database file and asserts it is not there.
- **The name** becomes the account's name when somebody signs up with BankID,
  because that is what an account needs. A login or a link never changes the
  name on an existing account.
- **The birth date, `uniqueuserid`** (BankID's own identifier for the legal
  person, which Idura calls not sensitive but which is the same across every
  service that person uses) **and the certificate fields** are not read.
- **No address** comes from BankID. The one a BankID account has is what the
  person typed, unconfirmed until the ordinary confirmation link is opened.

A **held signup** (`pending_provider_signup`: the `sub`, the name, a token
digest) exists only between the BankID login and the typed address. It is
deleted when the account is created, and any expired one (after thirty
minutes) is deleted whenever another is written or redeemed. A person who
abandons signup therefore leaves their name behind until the next BankID
signup comes along, which on a quiet installation can be a while; see Gaps. It
is not tied to an account, so erasure has nothing to do with it.

**An account that signs in with BankID signs in with nothing else**, because
BankID's terms forbid using it to set up or restore a sign-in of our own
([integrations/bankid.md](integrations/bankid.md#bankid-is-the-only-way-into-a-bankid-account)).
For stored data that means less, not more: switching an account to BankID
deletes its password hash and every other `user_identity` row, and switching
back deletes the BankID row and the broker's pseudonym with it. Nothing new is
kept for either. The links are `user_email_token` rows like the reset link,
and so is the record that a reset request for a BankID account was answered
with "you sign in with BankID" (`bankid_notice`), which exists for the hourly
cap and holds no link.

**Erasure** deletes the `user_identity` row like any other provider's, and the
link attempts that name the account, which a tombstone would otherwise keep.
The broker, Idura, is a processor and keeps what its own agreement says;
Myntos sends it nothing on erasure, because nothing was sent to it in the first
place beyond the login itself.

## Gaps

- **The tombstone never expires.** Once the last voucher, bilag or filing that
  names a person is past its retention period, the name could go too. The
  daily purge of deleted companies (above) is the job that could do it, and
  does not yet; nor does it prune `OAuthLoginAttempt` and
  `GovernmentLoginAttempt`.
- **Open books are never purged.** Retention is enforced only for companies an
  owner deleted. Books somebody simply stopped using keep every year, however
  old, because nothing asks the company whether a year past its five may go.
- **No download of all bilag at once.** Deleting a company sends the owner to
  SAF-T, which carries no bilag; they reach those by opening the company again
  (FEATURES.md #44, full export).
- **The purge has only run against SQLite and local storage.** R2's DELETE is
  signed like its PUT and GET, but no object has been deleted from the real
  bucket yet. The first purge is due in 2032 at the earliest.
- **An abandoned BankID signup waits for the next one.** Expired holds are
  swept by the application, not by a schedule, so the last one on a quiet
  installation keeps a name and a broker pseudonym past its thirty minutes. The
  same pruning job would close it.
- **Bilag contents are not searched.** An uploaded receipt may well carry the
  person's name. It is the company's primary documentation and stays, by the
  same exemption, but nothing here tells the person that in more than a
  sentence.
- **Backups.** Neon's point-in-time restore keeps deleted rows for its
  retention window. That is ordinary under GDPR as long as a restore does not
  bring an erased account back into use, and nothing currently guarantees that.

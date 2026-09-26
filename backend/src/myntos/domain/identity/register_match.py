"""Matching the people in Enhetsregisteret to the people who can see the books.

The board list on a company page answers "who is behind this company". The
member list answers "who can open the accounts". They are almost always meant to
be the same people, and until now nothing said whether they were. A resigned
chair who kept their login, and a daglig leder nobody ever invited, both looked
exactly like a company whose access was in order.

This module joins the two lists, and everything hard about it is that the join
has no key.

**Brønnøysund gives a name and nothing else.** The register identifies a person
by name *and* birth date, and :mod:`myntos.integrations.brreg` deliberately
drops the birth date on the way in: a board list is what an accountant needs,
and a date of birth is personal data with no bookkeeping use. There is no
fødselsnummer in the register's answer at all. So a name is the only thing there
is to match on, and this module is honest about what that is worth rather than
pretending to a certainty it cannot have.

**How much a name is worth depends on where the other one came from.** Two
sources, and the difference between them is the whole reason
:class:`RegisterAccess` has more than two values:

- The name on a Myntos account is whatever the person typed at signup. "Andreas
  Lindeman" is probably the "ANDREAS EDVARDSEN LINDEMAN" on the board, and might
  be his father. That is :attr:`RegisterAccess.LIKELY`, and the interface says
  "trolig".
- The name on a linked ID-porten login comes from Folkeregisteret, which is
  where the register's own names come from. When those two agree it is the same
  administrative fact twice, and that is :attr:`RegisterAccess.CONFIRMED`.

Which is what makes linking an ID-porten login worth offering: it is the step
that turns a guess into an answer.

**A middle name is not a different person.** People sign up as "Andreas
Lindeman" and stand in the register as "Andreas Edvardsen Lindeman", so a first
name plus a last name matches. That admits a false positive (two brothers, or a
father and son sharing both names inside one organization), which is a real
cost, and the reason nothing here *does* anything: the mark is shown to an
administrator who knows their own colleagues, and it grants nothing.

**Where a name came from outranks how exactly it matches.** A verified name
that agrees on first-and-last beats an account name that agrees letter for
letter, because anybody can type a name into a signup form and only the person
themselves can pass BankID as it. Exactness breaks ties *within* one source,
never across them.

Pure functions over plain data. No database, no HTTP, no ORM: the caller reads
the rows and this decides what they mean, which is what makes the rule testable
without a company, a register or a login.
"""

from __future__ import annotations

import enum
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from myntos.domain.identity.models import OrganizationRole


class RegisterAccess(enum.StrEnum):
    """What is known about one registered role holder's access to the books.

    Ordered from most to least certain, and the interface renders each one
    differently on purpose: a mark that reads the same for "we checked" and "we
    guessed" is worse than no mark, because it is the guess that gets acted on.
    """

    # A member whose ID-porten login carries this name. Both names come from
    # Folkeregisteret, so this is the same person twice.
    CONFIRMED = "bekreftet"
    # A member whose *account* name matches. Probably them; nobody has proved it.
    LIKELY = "trolig"
    # Asked, not yet joined. Only reachable for an invitation somebody addressed
    # to a name, which is what inviting straight from this table does.
    INVITED = "invitert"
    # Nobody in the organization goes by this name.
    NONE = "ingen"
    # The holder is a company, a revisor or a regnskapsfører, so the question
    # is not "does this person have a login" and no mark should suggest it is.
    COMPANY = "selskap"


@dataclass(frozen=True, slots=True)
class MemberName:
    """One member of the organization, under both names we might know them by."""

    user_id: int
    role: OrganizationRole
    # What they typed when they signed up. Always present; a Myntos account
    # cannot exist without one.
    account_name: str
    # What ID-porten says, when they have linked a login. None is ordinary.
    verified_name: str | None = None


@dataclass(frozen=True, slots=True)
class InvitedName:
    """One pending invitation, and the person it names."""

    invitation_id: int
    role: OrganizationRole
    email: str
    # None for an invitation sent to an address with no name attached, which
    # then cannot be matched to anybody in the register. That is the honest
    # outcome rather than a reason to guess from the local part of the address.
    full_name: str | None = None


@dataclass(frozen=True, slots=True)
class RegisterMatch:
    """What was found for one registered role holder."""

    access: RegisterAccess
    # The organization role they hold, or would hold once they accept.
    role: OrganizationRole | None = None
    # The name on the Myntos side of the match, when it is not word-for-word the
    # register's. Shown so an administrator can see *why* two names were treated
    # as one person, and disagree.
    matched_name: str | None = None
    user_id: int | None = None
    invitation_id: int | None = None
    # The address an invitation went to, so "invitert" can say where.
    email: str | None = None


def name_key(name: str) -> str:
    """A name reduced to what two spellings of it have in common.

    Case, punctuation and runs of whitespace go; the letters stay, æøå included.
    Deliberately *not* accent-folding: å and a are different letters in
    Norwegian, "Håkon" and "Hakon" are different names, and a folding that
    conflated them would be a false match dressed up as tolerance. What is
    normalised is Unicode composition, so a decomposed å from one system equals
    a composed å from another.
    """
    composed = unicodedata.normalize("NFC", name)
    # Hyphens join names ("Anne-Lise") and are written both ways; a period is
    # an abbreviation mark and never part of the name itself.
    cleaned = composed.replace("-", " ").replace(".", " ").replace(",", " ")
    return " ".join(cleaned.casefold().split())


def short_key(name: str) -> str | None:
    """First name plus last name, when there is something in between.

    ``None`` when the name is already two words or fewer, so a caller never
    compares a short key against a full one that happens to be identical. The
    two indexes stay distinct, and a two-word name matches only as itself.
    """
    parts = name_key(name).split()
    if len(parts) < 3:
        return None
    return f"{parts[0]} {parts[-1]}"


def _loose_key(name: str) -> str:
    """The key a name is compared under when middle names are allowed to differ.

    ``short_key`` for a name that has something in the middle, and the name
    itself for one that does not, so "Andreas Edvardsen Lindeman" and "Andreas
    Lindeman" arrive at the same string from opposite directions. Both spellings
    have to be reduced, or the looser comparison only ever works when the
    register is the fuller of the two.
    """
    return short_key(name) or name_key(name)


@dataclass(frozen=True, slots=True)
class _Index:
    """One source of names, looked up two ways.

    ``exact`` is the name as written; ``loose`` is the same name with any middle
    names dropped. Two tables rather than one because the interface would
    otherwise have no way to prefer a word-for-word match over an approximate
    one within the same source.

    First entry wins on a collision. Two members with the same name is a
    situation nothing here can resolve, and picking the first is at least
    stable; the alternative, picking whoever the database returned last, would
    make the mark flicker between page loads.
    """

    exact: dict[str, RegisterMatch]
    loose: dict[str, RegisterMatch]

    def find(self, *, key: str, loose_key: str) -> RegisterMatch | None:
        return self.exact.get(key) or self.loose.get(loose_key)


def _index(names: Iterable[tuple[str, RegisterMatch]]) -> _Index:
    exact: dict[str, RegisterMatch] = {}
    loose: dict[str, RegisterMatch] = {}
    for name, match in names:
        key = name_key(name)
        if not key:
            continue
        exact.setdefault(key, match)
        loose.setdefault(_loose_key(name), match)
    return _Index(exact=exact, loose=loose)


def match_person(
    register_name: str,
    *,
    orgnr: str | None = None,
    members: Sequence[MemberName] = (),
    invitations: Sequence[InvitedName] = (),
) -> RegisterMatch:
    """Decide what is known about one register role holder's access.

    The order of the checks is the order of certainty, and the first hit wins:
    every ID-porten name is tried before any account name, and every account
    name before any invitation. So somebody who has both linked ID-porten *and*
    a pending invitation shows as the member they are, and a colleague who has
    linked outranks one who only shares a spelling.
    """
    if orgnr is not None:
        # A revisor or regnskapsfører firm. It may well be a Myntos customer;
        # it is not a person with a login here, and marking it "ingen tilgang"
        # would answer a question nobody asked.
        return RegisterMatch(access=RegisterAccess.COMPANY)

    key = name_key(register_name)
    if not key:
        return RegisterMatch(access=RegisterAccess.NONE)
    loose = _loose_key(register_name)

    sources = (
        # ID-porten names first, then account names, then invitations. Within
        # one source an exact spelling beats an approximate one; across sources
        # it does not, which is the ordering worth stating plainly: a
        # Folkeregisteret name that matches on first-and-last is better evidence
        # than a self-typed one that matches letter for letter, because anybody
        # can type a name and only one person can pass BankID as it.
        _index(
            (
                member.verified_name,
                _member_match(member, RegisterAccess.CONFIRMED, member.verified_name),
            )
            for member in members
            if member.verified_name is not None and member.verified_name.strip()
        ),
        _index(
            (
                member.account_name,
                _member_match(member, RegisterAccess.LIKELY, member.account_name),
            )
            for member in members
        ),
        _index(
            (
                invitation.full_name,
                RegisterMatch(
                    access=RegisterAccess.INVITED,
                    role=invitation.role,
                    matched_name=invitation.full_name,
                    invitation_id=invitation.invitation_id,
                    email=invitation.email,
                ),
            )
            for invitation in invitations
            if invitation.full_name is not None and invitation.full_name.strip()
        ),
    )
    for source in sources:
        found = source.find(key=key, loose_key=loose)
        if found is not None:
            return _tidy(found, register_name)
    return RegisterMatch(access=RegisterAccess.NONE)


def _member_match(member: MemberName, access: RegisterAccess, name: str | None) -> RegisterMatch:
    return RegisterMatch(
        access=access,
        role=member.role,
        matched_name=name,
        user_id=member.user_id,
    )


def _tidy(match: RegisterMatch, register_name: str) -> RegisterMatch:
    """Drop ``matched_name`` when it says the same thing the register does.

    The field exists to show a *difference*: "matched to Andreas Lindeman" next
    to "ANDREAS EDVARDSEN LINDEMAN". Repeating the register's own name back at
    the reader is noise, and the interface would have to filter it out anyway.
    """
    if match.matched_name is None:
        return match
    if name_key(match.matched_name) == name_key(register_name):
        return RegisterMatch(
            access=match.access,
            role=match.role,
            matched_name=None,
            user_id=match.user_id,
            invitation_id=match.invitation_id,
            email=match.email,
        )
    return match

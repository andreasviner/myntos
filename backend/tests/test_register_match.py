"""Matching Enhetsregisteret's board list to the people who can open the books.

The join has no key: Brønnøysund gives a name and nothing else. So every test
here is about what a name is worth and what it is not. The rule lives in
:mod:`myntos.domain.identity.register_match`, and it is pure, so none of this
needs a company, a register or a login.
"""

from myntos.domain.identity.models import OrganizationRole
from myntos.domain.identity.register_match import (
    InvitedName,
    MemberName,
    RegisterAccess,
    match_person,
    name_key,
    short_key,
)


def _member(
    name: str,
    *,
    verified: str | None = None,
    role: OrganizationRole = OrganizationRole.MEMBER,
    user_id: int = 1,
) -> MemberName:
    return MemberName(user_id=user_id, role=role, account_name=name, verified_name=verified)


# --- Reducing a name to what two spellings share -----------------------------------


def test_case_and_spacing_do_not_make_a_different_person() -> None:
    assert name_key("ANDREAS EDVARDSEN LINDEMAN") == name_key("Andreas  Edvardsen Lindeman")


def test_norwegian_letters_are_letters_and_not_accents() -> None:
    """Folding å to a would match "Håkon" with "Hakon", which are two names."""
    assert name_key("Håkon Aas") != name_key("Hakon Aas")
    # But the same letter written two ways in Unicode is one letter.
    assert name_key("Håkon") == name_key("Håkon")


def test_a_hyphenated_name_matches_the_spaced_spelling() -> None:
    assert name_key("Anne-Lise Ek") == name_key("Anne Lise Ek")


def test_a_short_key_exists_only_when_something_sits_in_the_middle() -> None:
    """Otherwise a two-word name would be indexed twice and match itself twice."""
    assert short_key("Andreas Edvardsen Lindeman") == "andreas lindeman"
    assert short_key("Andreas Lindeman") is None


# --- What a match is worth ---------------------------------------------------------


def test_a_verified_name_is_the_strong_answer() -> None:
    """Both sides came from Folkeregisteret, so this is one fact twice."""
    found = match_person(
        "Andreas Edvardsen Lindeman",
        members=[_member("Andreas Lindeman", verified="Andreas Edvardsen Lindeman")],
    )
    assert found.access is RegisterAccess.CONFIRMED
    assert found.user_id == 1
    assert found.role is OrganizationRole.MEMBER


def test_an_account_name_alone_is_only_probable() -> None:
    """Nobody has proved that the person who typed this is the one on the board."""
    found = match_person("Andreas Edvardsen Lindeman", members=[_member("Andreas Lindeman")])
    assert found.access is RegisterAccess.LIKELY
    # And the interface is told which name was matched, because it is not the
    # one on screen and an administrator should be able to disagree.
    assert found.matched_name == "Andreas Lindeman"


def test_the_matched_name_is_dropped_when_it_adds_nothing() -> None:
    found = match_person("Andreas Lindeman", members=[_member("Andreas Lindeman")])
    assert found.access is RegisterAccess.LIKELY
    assert found.matched_name is None


def test_a_middle_name_the_account_leaves_out_is_still_the_same_person() -> None:
    found = match_person("Kari Berit Nordmann", members=[_member("Kari Nordmann")])
    assert found.access is RegisterAccess.LIKELY


def test_a_middle_name_the_register_leaves_out_is_too() -> None:
    """Both directions: the account may be the fuller spelling."""
    found = match_person("Kari Nordmann", members=[_member("Kari Berit Nordmann")])
    assert found.access is RegisterAccess.LIKELY


def test_certainty_wins_over_exactness() -> None:
    """A verified first-and-last beats an unverified word-for-word match.

    Two people, and the register name spells out a middle name. The account
    that matches it letter for letter has proved nothing; the other has been
    through ID-porten. The stronger evidence is the one to report.
    """
    found = match_person(
        "Kari Berit Nordmann",
        members=[
            _member("Kari Berit Nordmann", user_id=2),
            _member("Kari Nordmann", verified="Kari B Nordmann", user_id=3),
        ],
    )
    assert found.access is RegisterAccess.CONFIRMED
    assert found.user_id == 3


def test_somebody_who_has_only_been_asked_shows_as_asked() -> None:
    found = match_person(
        "Jacob Fjørstad Hansson",
        invitations=[
            InvitedName(
                invitation_id=7,
                role=OrganizationRole.ADMIN,
                email="jacob@example.no",
                full_name="Jacob Fjørstad Hansson",
            )
        ],
    )
    assert found.access is RegisterAccess.INVITED
    assert found.invitation_id == 7
    assert found.email == "jacob@example.no"
    assert found.role is OrganizationRole.ADMIN


def test_a_member_beats_a_pending_invitation_to_somewhere_else() -> None:
    found = match_person(
        "Kari Nordmann",
        members=[_member("Kari Nordmann")],
        invitations=[
            InvitedName(
                invitation_id=7,
                role=OrganizationRole.VIEWER,
                email="kari@example.no",
                full_name="Kari Nordmann",
            )
        ],
    )
    assert found.access is RegisterAccess.LIKELY


def test_an_invitation_with_no_name_matches_nobody() -> None:
    """An address is not a name, and guessing one from the local part is worse."""
    found = match_person(
        "Kari Nordmann",
        invitations=[
            InvitedName(invitation_id=7, role=OrganizationRole.MEMBER, email="kari@example.no")
        ],
    )
    assert found.access is RegisterAccess.NONE


def test_a_stranger_on_the_board_is_reported_as_one() -> None:
    found = match_person("Ola Nordmann", members=[_member("Kari Nordmann")])
    assert found.access is RegisterAccess.NONE
    assert found.user_id is None


def test_a_revisor_is_a_company_and_the_question_does_not_apply() -> None:
    """Marking an auditing firm "ingen tilgang" answers something nobody asked."""
    found = match_person("Fjord Revisjon AS", orgnr="915501680", members=[_member("Kari Nordmann")])
    assert found.access is RegisterAccess.COMPANY
    assert found.role is None


def test_a_shielded_holder_with_no_name_matches_nothing() -> None:
    found = match_person("   ", members=[_member("Kari Nordmann")])
    assert found.access is RegisterAccess.NONE


def test_two_members_with_one_name_resolve_the_same_way_every_time() -> None:
    """A false positive is possible; a flickering one would be worse.

    Nothing here can tell a father and son apart, and the mark grants nothing,
    but it must not change between page loads, so the first match wins rather
    than whichever row the database happened to return last.
    """
    members = [_member("Ola Nordmann", user_id=4), _member("Ola Nordmann", user_id=5)]
    assert match_person("Ola Nordmann", members=members).user_id == 4
    assert match_person("Ola Nordmann", members=list(reversed(members))).user_id == 5

"""Password hashing and policy."""

import unicodedata

import pytest

from myntos.auth import passwords
from myntos.domain.identity.errors import WeakPasswordError


def test_hash_then_verify_roundtrips() -> None:
    stored = passwords.hash_password("korrekt-hest-batteri-stift")
    assert passwords.verify_password("korrekt-hest-batteri-stift", stored)
    assert not passwords.verify_password("korrekt-hest-batteri-stif", stored)


def test_hash_is_argon2id_and_salted() -> None:
    first = passwords.hash_password("korrekt-hest-batteri-stift")
    second = passwords.hash_password("korrekt-hest-batteri-stift")
    assert first.startswith("$argon2id$")
    # Different salts, so identical passwords do not produce identical hashes —
    # otherwise the table would reveal which users share a password.
    assert first != second


def test_short_password_is_rejected() -> None:
    with pytest.raises(WeakPasswordError):
        passwords.hash_password("kort")


def test_absurdly_long_password_is_rejected() -> None:
    # Argon2 hashes the whole input, so an unbounded password is a way to make
    # the server do expensive work on demand.
    with pytest.raises(WeakPasswordError):
        passwords.hash_password("x" * (passwords.MAX_PASSWORD_LENGTH + 1))


def test_unicode_equivalent_passwords_verify() -> None:
    """The same characters typed two ways must be the same password.

    Norwegian vowels exist both as a single code point and as a base letter plus
    a combining mark, and operating systems disagree about which one a keyboard
    produces. Without NFKC normalisation a user's password would depend on which
    machine they set it from.
    """
    composed = unicodedata.normalize("NFC", "blåbærsyltetøy-2026")
    decomposed = unicodedata.normalize("NFD", composed)
    assert composed != decomposed  # different bytes, same text
    stored = passwords.hash_password(composed)
    assert passwords.verify_password(decomposed, stored)


def test_account_without_password_never_verifies() -> None:
    """A provider-only account cannot be reached with a password.

    Any password, including the empty string, must fail against a NULL hash —
    the pitfall being an implementation where "no hash stored" reads as
    "nothing to check".
    """
    assert not passwords.verify_password("", None)
    assert not passwords.verify_password("korrekt-hest-batteri-stift", None)


def test_garbage_hash_does_not_raise() -> None:
    # A corrupted or truncated hash column must fail the login, not 500 it.
    assert not passwords.verify_password("korrekt-hest-batteri-stift", "not-a-hash")

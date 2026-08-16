"""Password hashing and password policy.

Argon2id, via ``argon2-cffi``, at the library's default parameters (t=3,
m=64 MiB, p=4). Those exceed the OWASP floor of 19 MiB and are chosen here
rather than tuned because the honest tuning input — how much memory the
production container has — is not known yet; the cost is one 64 MiB allocation
per login attempt, and the parameters are recorded inside each hash, so raising
them later re-hashes users transparently on their next sign-in
(:func:`needs_rehash`).

Two properties matter as much as the algorithm:

- A user with no password (provider-only account) must not be authenticable,
  and asking must not be *faster* than asking about a user who has one. Both
  are handled in :func:`verify_password`.
- Passwords are Unicode-normalised before hashing. Without it, the same
  keystrokes typed on a different keyboard layout or operating system can
  produce a different byte sequence and fail to verify.
"""

import unicodedata
from contextlib import suppress
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from myntos.core.config import get_settings
from myntos.domain.identity.errors import WeakPasswordError

_HASHER = PasswordHasher()

# Argon2 hashes its input in full, so a multi-megabyte "password" is a cheap way
# to make the server do expensive work. bcrypt's 72-byte truncation does not
# apply here, so the cap only exists to bound that.
MAX_PASSWORD_LENGTH = 256


def normalise(password: str) -> str:
    """NFKC-normalise a password so equivalent input verifies equally."""
    return unicodedata.normalize("NFKC", password)


def validate_password(password: str) -> None:
    """Raise :class:`WeakPasswordError` if the password is unacceptable.

    Length only. Composition rules ("one digit, one symbol") push users towards
    predictable substitutions without adding entropy — NIST SP 800-63B advises
    against them and recommends length instead.
    """
    normalised = normalise(password)
    minimum = get_settings().password_min_length
    if len(normalised) < minimum:
        raise WeakPasswordError(f"passordet må være minst {minimum} tegn")
    if len(normalised) > MAX_PASSWORD_LENGTH:
        raise WeakPasswordError(f"passordet kan ikke være lengre enn {MAX_PASSWORD_LENGTH} tegn")


def hash_password(password: str) -> str:
    """Validate and hash a new password."""
    validate_password(password)
    return _HASHER.hash(normalise(password))


@lru_cache(maxsize=1)
def _decoy_hash() -> str:
    """A hash nobody knows the password to, used to spend time on purpose.

    Built lazily so importing this module does not cost a 64 MiB Argon2 run.
    """
    return _HASHER.hash("no account holds this password")


def verify_password(password: str, password_hash: str | None) -> bool:
    """Check ``password`` against a stored hash.

    ``password_hash`` is ``None`` for accounts that only sign in through a
    provider, and for the "user does not exist" case at the call site. Both
    still perform a full Argon2 verification against a decoy, so response time
    does not reveal whether an address has an account or has a password — then
    return ``False``.
    """
    if password_hash is None:
        # The mismatch is the expected outcome; the decoy exists only to spend
        # the same amount of time a real verification would have.
        with suppress(VerificationError):
            _HASHER.verify(_decoy_hash(), "wrong")
        return False
    try:
        return _HASHER.verify(password_hash, normalise(password))
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """True when the hash was made with weaker parameters than we now use."""
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False

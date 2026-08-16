"""Opaque bearer tokens: session cookies, invitation links, OAuth state.

All of them follow the same shape — 256 bits from
:func:`secrets.token_urlsafe`, handed to the client once, stored only as a
SHA-256 digest.

SHA-256 rather than Argon2 is deliberate, and the reasoning is the opposite of
:mod:`myntos.auth.passwords`: a password is low-entropy and guessable, so
verification must be slow. A 256-bit random token is not guessable, so there is
nothing for a slow hash to protect against — hashing exists only so that a
leaked database dump contains no usable credentials, and it has to stay cheap
because it runs on every authenticated request.
"""

import hashlib
import hmac
import secrets

# 32 bytes → 256 bits of entropy, rendered as 43 URL-safe characters.
TOKEN_BYTES = 32


def generate_token() -> str:
    """A fresh URL-safe token."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """The stored form of ``token`` — a SHA-256 hex digest."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    """Constant-time comparison, for the rare case of comparing two digests."""
    return hmac.compare_digest(left, right)

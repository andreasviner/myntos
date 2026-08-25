"""Encrypting a government access token for the minutes it has to be kept.

A submission is not one request. Skatteetaten requires a person to review the
melding before it is filed, so the token that authorizes the filing has to
survive between the ID-porten callback and the moment the user presses submit.
The backend runs as stateless containers that sleep between requests, so that
means a database row rather than process memory - the same reasoning
:class:`~myntos.domain.identity.models.OAuthLoginAttempt` records for its own
short-lived state.

Which leaves the question the token's own docstring leaves open: how to keep it.
A government access token authorizes filing a tax return in a named person's
name, so it is kept **encrypted**, for **minutes**, and **once**:

- encrypted with Fernet under ``GOVERNMENT_TOKEN_KEY``, so a database dump is
  not a pile of usable government credentials;
- expiring no later than ID-porten's own ``exp``, because a token kept past it
  is only a liability - it cannot do anything useful;
- single use, deleted when the submission finishes.

With no key configured the feature is **unavailable** rather than degraded to
plaintext. That is deliberate: a missing key is a deployment mistake, and the
failure mode of guessing what the operator meant is a readable column full of
credentials.

The key is not rotated here. Rotation means re-encrypting nothing - every row
is minutes old - so changing the key simply invalidates in-flight logins, and
those users log in again.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from myntos.auth.idporten.client import IdPortenError
from myntos.core.config import Settings, get_settings


class TokenVaultError(IdPortenError):
    """A government token could not be sealed or opened."""


class VaultNotConfiguredError(TokenVaultError):
    """No ``GOVERNMENT_TOKEN_KEY`` is set, so nothing may be stored."""


def is_configured(settings: Settings | None = None) -> bool:
    """Whether a government token may be stored at all.

    Checked before a login is offered, so the flow is not advertised and then
    refused at the point where a person has already authenticated.
    """
    settings = settings or get_settings()
    return bool(settings.government_token_key.strip())


def _cipher(settings: Settings | None = None) -> Fernet:
    settings = settings or get_settings()
    key = settings.government_token_key.strip()
    if not key:
        raise VaultNotConfiguredError(
            "GOVERNMENT_TOKEN_KEY is not set, so a government token cannot be "
            'stored. Generate one with: python -c "from cryptography.fernet '
            'import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as error:
        raise VaultNotConfiguredError(
            "GOVERNMENT_TOKEN_KEY is not a valid Fernet key: it must be 32 "
            "urlsafe-base64 bytes, exactly as Fernet.generate_key() prints it"
        ) from error


def seal(token: str, *, settings: Settings | None = None) -> bytes:
    """Encrypt ``token`` for storage."""
    return _cipher(settings).encrypt(token.encode())


def open_sealed(blob: bytes, *, settings: Settings | None = None) -> str:
    """Decrypt a stored token.

    A blob that will not open is an error rather than a ``None``: it means the
    key changed or the row was tampered with, and quietly treating that as "no
    login" would hide both.
    """
    try:
        return _cipher(settings).decrypt(blob).decode()
    except InvalidToken as error:
        raise TokenVaultError(
            "a stored government token could not be decrypted - the key has "
            "probably changed since it was stored, in which case the person "
            "needs to log in again"
        ) from error

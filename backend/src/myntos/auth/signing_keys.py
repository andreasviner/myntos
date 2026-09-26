"""The private keys that authenticate this installation to Digdir.

Maskinporten and ID-porten both authenticate a *client* with a signed JWT
rather than a shared secret: the JWT grant (RFC 7523) in Maskinporten's case,
``private_key_jwt`` client authentication in ID-porten's. Both need the same
three things — a private key, the ``kid`` it was registered under, and the
signing algorithm — so both read it through here.

**Two file encodings, one setting.** ``MASKINPORTEN_JWK_PATH`` is documented as
a JWK, and Digdir's self-service hands you one. But a key generated with
``openssl genrsa`` is a PEM, and one is as valid as the other once it has been
registered — so both are read, decided by looking at the file rather than by a
second configuration flag someone would have to keep in step with reality.

**The ``kid`` cannot be guessed.** Digdir looks the signing key up by the
``kid`` in the JWT header; a value that does not match a registered key is an
``invalid_client`` rejection, not a signature failure, and the difference
matters when reading the error. A JWK carries its own ``kid`` and that one wins.
A PEM carries nothing, so the ``kid`` has to be configured — and if the key is
registered with the thumbprint this module computes, ``python -m
myntos.auth.digdir_key`` prints both the ``kid`` and the public JWK to paste
into Samarbeidsportalen.

Nothing here is ever logged. The loaded key stays in memory in a single
:class:`SigningKey`, and :func:`public_jwk` deliberately builds the *public*
half only, so the value a person is asked to copy out of a terminal cannot
contain the private exponent even by accident.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

# RS256 across the board. Maskinporten accepts RS256/384/512 and ID-porten the
# same; one algorithm keeps the registration and the code from disagreeing, and
# there is no security argument for the longer digests here.
ALGORITHM = "RS256"

# Digdir requires at least 2048 bits. Refusing anything smaller here turns a
# rejected token request into a configuration error with a readable reason.
_MINIMUM_KEY_SIZE = 2048


class SigningKeyError(Exception):
    """The configured client key could not be read, or is not usable."""


@dataclass(frozen=True)
class SigningKey:
    """A private key and the ``kid`` Digdir knows it by."""

    key: RSAPrivateKey
    kid: str

    @property
    def algorithm(self) -> str:
        return ALGORITHM


def load_signing_key(path: Path, *, kid: str | None = None) -> SigningKey:
    """Read the client key at ``path``, as either a JWK or a PEM.

    ``kid`` is used when the file does not carry one of its own. A JWK's own
    ``kid`` takes precedence, because that is the value the key was registered
    with — silently overriding it from configuration would produce a token
    request that fails for a reason nothing in the configuration explains.
    """
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise SigningKeyError(f"could not read the client key at {path}: {error}") from error
    return load_signing_key_material(raw, kid=kid, source=str(path))


def load_signing_key_material(
    raw: bytes, *, kid: str | None = None, source: str = "the configured client key"
) -> SigningKey:
    """The same key, read from memory rather than from a file.

    A secret binding is a string, not a path, so a container that has its
    credentials injected holds the key material with nowhere on disk to put it.
    ``source`` only names the origin in error messages.
    """
    document = _as_jwk_document(raw, source=source)
    if document is not None:
        return _from_jwk(document, fallback_kid=kid, source=source)
    return _from_pem(raw, kid=kid, source=source)


def _as_jwk_document(raw: bytes, *, source: str) -> dict[str, Any] | None:
    """The file parsed as a single JWK, or ``None`` if it is not JSON at all.

    A JWK *set* is accepted with exactly one key in it: that is what several
    tools export, and picking one out of several would be a guess about which
    key was registered.
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        raise SigningKeyError(f"{source} is JSON, but not a JWK")

    keys = parsed.get("keys")
    if isinstance(keys, list):
        if len(keys) != 1:
            raise SigningKeyError(
                f"{source} is a JWK set with {len(keys)} keys - point the setting "
                "at a single key, so which one signs is not a guess"
            )
        only = keys[0]
        if not isinstance(only, dict):
            raise SigningKeyError(f"{source} is a JWK set with an unreadable key in it")
        return only
    return parsed


def _from_jwk(document: dict[str, Any], *, fallback_kid: str | None, source: str) -> SigningKey:
    if "d" not in document:
        raise SigningKeyError(
            f"{source} holds a public key - the JWK that signs the grant is the "
            "private one; the public half is what gets registered with Digdir"
        )
    try:
        key = jwt.PyJWK(document, algorithm=ALGORITHM).key
    except Exception as error:  # PyJWT raises several unrelated types here
        raise SigningKeyError(f"{source} is not a usable RSA JWK: {error}") from error

    kid = document.get("kid") or fallback_kid
    if not isinstance(kid, str) or not kid.strip():
        raise SigningKeyError(
            f"{source} carries no 'kid' - set the key id this client was "
            "registered with in Samarbeidsportalen"
        )
    return _checked(key, kid.strip(), source)


def _from_pem(raw: bytes, *, kid: str | None, source: str) -> SigningKey:
    try:
        key = load_pem_private_key(raw, password=None)
    except (ValueError, TypeError) as error:
        raise SigningKeyError(
            f"{source} is neither a JWK document nor an unencrypted PEM private key"
        ) from error

    if kid is None or not kid.strip():
        raise SigningKeyError(
            f"{source} is a PEM, which carries no key id. Set the matching *_KID "
            "variable to the key id this client was registered with - run "
            "'python -m myntos.auth.digdir_key' to see this key's thumbprint "
            "and the public JWK to register in Samarbeidsportalen"
        )
    return _checked(key, kid.strip(), source)


def _checked(key: object, kid: str, source: str) -> SigningKey:
    """Refuse anything that is not an RSA private key of a usable size."""
    if not isinstance(key, RSAPrivateKey):
        raise SigningKeyError(
            f"{source} is not an RSA private key - Digdir's JWT grant is signed "
            f"with RSA ({ALGORITHM})"
        )
    if key.key_size < _MINIMUM_KEY_SIZE:
        raise SigningKeyError(
            f"{source} is a {key.key_size}-bit key; Digdir requires at least "
            f"{_MINIMUM_KEY_SIZE} bits"
        )
    return SigningKey(key=key, kid=kid)


def public_jwk(key: RSAPrivateKey, *, kid: str | None = None) -> dict[str, str]:
    """The public JWK to register with Digdir, with a thumbprint ``kid``.

    The ``kid`` defaults to the RFC 7638 thumbprint of the public key: it is
    stable, derived from the key itself, and therefore impossible to get out of
    step with the key file the way a hand-chosen name can.

    Digdir does not necessarily keep it. Samarbeidsportalen assigns a UUID of
    its own to a registered key, and *that* is the value the JWT header has to
    carry — so the thumbprint here is a proposal and a default, never an
    assumption. Read the kid back from the portal after registering.
    """
    numbers = key.public_key().public_numbers()
    members = {
        "kty": "RSA",
        "n": _b64url(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
        "e": _b64url(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
    }
    return {
        **members,
        "kid": kid or thumbprint(members),
        "alg": ALGORITHM,
        "use": "sig",
    }


def thumbprint(jwk: dict[str, str]) -> str:
    """The RFC 7638 thumbprint: SHA-256 over the required members, in order."""
    canonical = json.dumps(
        {"e": jwk["e"], "kty": jwk["kty"], "n": jwk["n"]},
        separators=(",", ":"),
        sort_keys=True,
    )
    return _b64url(hashlib.sha256(canonical.encode("ascii")).digest())


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

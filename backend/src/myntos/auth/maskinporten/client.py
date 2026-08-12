"""Maskinporten machine-to-machine authentication.

Every Skatteetaten API this project talks to (skattemelding, MVA-melding)
requires an access token from Maskinporten, obtained with a signed JWT grant
(RFC 7523):

1. Build a JWT assertion (``iss`` = client id, ``aud`` = the Maskinporten
   issuer from the well-known document, ``scope`` = requested scopes,
   short lifetime).
2. Sign it with the client's private key — registered in Digdir's
   Samarbeidsportalen, or backed by a virksomhetssertifikat.
3. POST the assertion to the token endpoint and cache the access token in
   memory until shortly before expiry.

The concrete client is deliberately not implemented here yet: Maskinporten
client registration happens outside this repo, and the project owner wires up
the registered client. Implementations must satisfy :class:`TokenProvider` —
the rest of the codebase depends only on that protocol, never on a concrete
client. See docs/integrations/maskinporten.md.

Security invariants for any implementation:

- The private key is loaded from the path in ``Settings.maskinporten_jwk_path``
  (outside the repo or in the gitignored ``secrets/``), never from source code.
- Tokens are cached in memory only — never written to disk or logs.
- Request the narrowest scope set that the current operation needs.
"""

from collections.abc import Sequence
from typing import Protocol


class MaskinportenError(Exception):
    """Base error for Maskinporten failures."""


class MaskinportenNotConfiguredError(MaskinportenError):
    """Raised when no real token provider has been configured."""


class TokenProvider(Protocol):
    """Anything that can produce a Maskinporten access token."""

    async def get_access_token(self, scopes: Sequence[str]) -> str:
        """Return a valid access token covering ``scopes``."""
        ...


class NotConfiguredTokenProvider:
    """Placeholder that fails loudly until Maskinporten is wired up."""

    async def get_access_token(self, scopes: Sequence[str]) -> str:
        raise MaskinportenNotConfiguredError(
            "Maskinporten is not configured. Set the MASKINPORTEN_* variables in "
            ".env and provide a TokenProvider implementation — see "
            "docs/integrations/maskinporten.md."
        )

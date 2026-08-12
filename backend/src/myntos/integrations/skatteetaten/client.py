"""Client for Skatteetaten's skattemelding APIs (stub).

Planned submission lifecycle (details in docs/integrations/skatteetaten.md):

1. Fetch the current skattemelding / utkast for the taxpayer.
2. Build the næringsspesifikasjon from the ledger.
3. Validate the draft against Skatteetaten's validation endpoint.
4. Submit through the Altinn instance API.
5. Confirm the receipt (kvittering).

All calls authenticate with a Maskinporten token via
:class:`myntos.auth.maskinporten.client.TokenProvider`.
"""

from myntos.auth.maskinporten.client import TokenProvider


class SkattemeldingClient:
    """Stub — implemented in the skattemelding milestone (docs/ROADMAP.md)."""

    def __init__(self, token_provider: TokenProvider, base_url: str) -> None:
        self._token_provider = token_provider
        self._base_url = base_url

    async def fetch_current(self, inntektsaar: int, identifier: str) -> bytes:
        """Fetch the current skattemelding for a taxpayer and income year."""
        raise NotImplementedError

    async def validate(self, payload: bytes) -> bytes:
        """Validate a draft submission and return the validation result."""
        raise NotImplementedError

    async def submit(self, payload: bytes) -> str:
        """Submit and return a reference for the receipt (kvittering)."""
        raise NotImplementedError

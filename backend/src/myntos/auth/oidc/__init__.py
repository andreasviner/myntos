"""Federated sign-in over OpenID Connect (Google, Microsoft, BankID through a broker).

:mod:`.client` holds the protocol, :mod:`.providers` holds what differs between
providers. Nothing here knows about Myntos accounts — turning a verified
profile into a session is :mod:`myntos.domain.identity.service`'s job.
"""

from myntos.auth.oidc.client import (
    OidcClient,
    OidcError,
    OidcProfile,
    PkcePair,
    ProviderConfig,
    ProviderNotConfiguredError,
    new_pkce_pair,
)
from myntos.auth.oidc.providers import configured_providers, get_oidc_client, reset_clients

__all__ = [
    "OidcClient",
    "OidcError",
    "OidcProfile",
    "PkcePair",
    "ProviderConfig",
    "ProviderNotConfiguredError",
    "configured_providers",
    "get_oidc_client",
    "new_pkce_pair",
    "reset_clients",
]

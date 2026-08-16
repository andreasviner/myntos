"""What differs between Google, Microsoft and Vipps.

Each provider gets a :class:`~myntos.auth.oidc.client.ProviderConfig`; the
protocol itself lives in :mod:`.client`. Three things genuinely differ, and all
three have bitten real systems:

**How the issuer is checked.** Google signs tokens with two spellings of its
issuer. Microsoft's multi-tenant discovery document returns a *template*
(``…/{tenantid}/v2.0``) which has to be resolved against the token's own ``tid``
claim — comparing against the literal template accepts nothing, and skipping
the check accepts everything.

**How "the provider verified this email" is expressed.** Google and Vipps send
``email_verified``. Microsoft does **not** send it at all; the equivalent is the
``xms_edov`` optional claim, which is Microsoft's own remediation for the
nOAuth vulnerability — apps that trusted Entra's ``email`` claim could be taken
over by anyone who set that address on a tenant they controlled. Absence is
never read as verified, by any provider.

**Which claim carries the email.** Microsoft work accounts may put it in
``preferred_username`` when the ``email`` claim is absent.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import SecretStr

from myntos.auth.oidc.client import (
    OidcClient,
    OidcError,
    OidcProfile,
    ProviderConfig,
    ProviderNotConfiguredError,
    default_profile,
    exact_issuer,
)
from myntos.core.config import Settings, get_settings
from myntos.domain.identity.models import IdentityProvider

# Google's issuer historically appears both with and without the scheme. Both
# are legitimate values in tokens it signs.
GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})

# Microsoft's discovery document for the shared endpoints returns this literal
# placeholder in the issuer, to be replaced with the signing tenant's id.
_TENANT_PLACEHOLDER = "{tenantid}"
_GUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")


# --- Google ---------------------------------------------------------------------


def _google_issuer(metadata_issuer: str, claims: dict[str, Any]) -> bool:
    del metadata_issuer
    return claims.get("iss") in GOOGLE_ISSUERS


# --- Microsoft (Entra ID) ---------------------------------------------------------


def microsoft_issuer(metadata_issuer: str, claims: dict[str, Any]) -> bool:
    """Resolve Microsoft's tenant-templated issuer against the token's ``tid``.

    For a single-tenant registration the discovery document names one concrete
    issuer and this is plain equality. For the shared endpoints (``common``,
    ``organizations``, ``consumers``) it is a template, and the tenant id has
    to come from the token — checked to be a GUID first, so a crafted ``tid``
    cannot rewrite the rest of the issuer URL.
    """
    if _TENANT_PLACEHOLDER not in metadata_issuer:
        return exact_issuer(metadata_issuer, claims)
    tenant_id = claims.get("tid")
    if not isinstance(tenant_id, str) or not _GUID.match(tenant_id):
        return False
    return claims.get("iss") == metadata_issuer.replace(_TENANT_PLACEHOLDER, tenant_id)


def microsoft_profile(claims: dict[str, Any]) -> OidcProfile:
    """Read a Microsoft profile, deriving verification from ``xms_edov``.

    Entra ID never sends ``email_verified``. What it can send is ``xms_edov``
    ("email domain owner verified"), an optional claim that has to be switched
    on in the app registration. Without it we have no evidence the address
    belongs to the person signing in, so the address counts as unverified —
    which does not block them from getting an account, only from being attached
    to an account that already exists.
    """
    subject = claims.get("sub")
    if not isinstance(subject, str):
        raise OidcError("id_token is missing 'sub'")

    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        # Work and school accounts often carry the address here instead.
        candidate = claims.get("preferred_username")
        email = candidate if isinstance(candidate, str) and "@" in candidate else None
    if email is None:
        raise OidcError(
            "id_token carries no email address — add the 'email' optional claim "
            "to the app registration"
        )

    name = claims.get("name")
    return OidcProfile(
        subject=subject,
        email=email,
        email_verified=claims.get("xms_edov") is True,
        full_name=name if isinstance(name, str) and name.strip() else None,
    )


def microsoft_well_known(tenant: str) -> str:
    return f"https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"


# --- The registry -----------------------------------------------------------------


def _secret(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    return value.get_secret_value().strip() or None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    return value.strip() or None


def _build(provider: IdentityProvider, settings: Settings) -> ProviderConfig | None:
    """The provider's configuration, or ``None`` when it is not set up.

    A blank environment variable counts as unset: ``.env.example`` ships empty
    values for all of these, and a deployment that half-filled them should see
    the provider absent rather than a button that fails on click.
    """
    if provider is IdentityProvider.GOOGLE:
        client_id = _clean(settings.google_client_id)
        secret = _secret(settings.google_client_secret)
        redirect_uri = _clean(settings.google_redirect_uri)
        if not (client_id and secret and redirect_uri):
            return None
        return ProviderConfig(
            code=provider.value,
            label="Google",
            # Identity only: a name and an email, never mail or files.
            scope="openid email profile",
            client_id=client_id,
            client_secret=secret,
            redirect_uri=redirect_uri,
            well_known_url=settings.google_well_known_url,
            # Show the account chooser rather than silently reusing whichever
            # account the browser is already signed into — people keep a
            # personal and a work account and need to pick.
            authorization_params={"prompt": "select_account"},
            issuer_matches=_google_issuer,
        )

    if provider is IdentityProvider.MICROSOFT:
        client_id = _clean(settings.microsoft_client_id)
        secret = _secret(settings.microsoft_client_secret)
        redirect_uri = _clean(settings.microsoft_redirect_uri)
        if not (client_id and secret and redirect_uri):
            return None
        return ProviderConfig(
            code=provider.value,
            label="Microsoft",
            scope="openid email profile",
            client_id=client_id,
            client_secret=secret,
            redirect_uri=redirect_uri,
            well_known_url=_clean(settings.microsoft_well_known_url)
            or microsoft_well_known(settings.microsoft_tenant),
            authorization_params={"prompt": "select_account"},
            issuer_matches=microsoft_issuer,
            profile_from=microsoft_profile,
        )

    if provider is IdentityProvider.VIPPS:
        client_id = _clean(settings.vipps_client_id)
        secret = _secret(settings.vipps_client_secret)
        redirect_uri = _clean(settings.vipps_redirect_uri)
        if not (client_id and secret and redirect_uri):
            return None
        return ProviderConfig(
            code=provider.value,
            label="Vipps",
            # Vipps carries the name under its own scope rather than "profile".
            scope="openid email name",
            client_id=client_id,
            client_secret=secret,
            redirect_uri=redirect_uri,
            well_known_url=settings.vipps_well_known_url,
            issuer_matches=exact_issuer,
            profile_from=default_profile,
        )

    return None  # pragma: no cover - every member is handled above


# Built once per provider and kept: the client caches the discovery document
# and the key set, and throwing that away on every request would mean two extra
# round trips per login.
_CLIENTS: dict[IdentityProvider, OidcClient] = {}


def reset_clients() -> None:
    """Drop the cached clients. For tests that change the configuration."""
    _CLIENTS.clear()


def get_oidc_client(provider: IdentityProvider) -> OidcClient:
    """The configured client for ``provider``, or raise."""
    cached = _CLIENTS.get(provider)
    if cached is not None:
        return cached
    config = _build(provider, get_settings())
    if config is None:
        raise ProviderNotConfiguredError(
            f"innlogging med {provider.value} er ikke satt opp i denne installasjonen"
        )
    client = OidcClient(config)
    _CLIENTS[provider] = client
    return client


def configured_providers() -> list[tuple[IdentityProvider, str]]:
    """Every provider this deployment can actually sign a user in with.

    The frontend renders exactly this list, so a provider that is half
    configured never appears as a button that leads nowhere.
    """
    settings = get_settings()
    found: list[tuple[IdentityProvider, str]] = []
    for provider in IdentityProvider:
        config = _build(provider, settings)
        if config is not None:
            found.append((provider, config.label))
    return found

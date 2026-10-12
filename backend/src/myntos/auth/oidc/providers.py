"""What differs between Google, Microsoft and BankID.

Each provider gets a :class:`~myntos.auth.oidc.client.ProviderConfig`; the
protocol itself lives in :mod:`.client`. BankID is the odd one out and has a
section of its own below (:func:`bankid_profile`): it comes through a broker,
and it says who a person is without saying how to reach them.

Between the two that do carry an address, three things genuinely differ, and
all three have bitten real systems:

**How the issuer is checked.** Google signs tokens with two spellings of its
issuer. Microsoft's multi-tenant discovery document returns a *template*
(``…/{tenantid}/v2.0``) which has to be resolved against the token's own ``tid``
claim — comparing against the literal template accepts nothing, and skipping
the check accepts everything.

**How "the provider verified this email" is expressed.** Google sends
``email_verified``. Microsoft does **not** send it at all; the equivalent is the
``xms_edov`` optional claim, which is Microsoft's own remediation for the
nOAuth vulnerability — apps that trusted Entra's ``email`` claim could be taken
over by anyone who set that address on a tenant they controlled. Absence is
never read as verified, by any provider.

**Which claim carries the email.** Microsoft work accounts may put it in
``preferred_username`` when the ``email`` claim is absent.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from pydantic import SecretStr

from myntos.auth.oidc.client import (
    OidcClient,
    OidcError,
    OidcProfile,
    ProviderConfig,
    ProviderNotConfiguredError,
    exact_issuer,
)
from myntos.auth.signing_keys import (
    SigningKey,
    SigningKeyError,
    load_signing_key,
    load_signing_key_material,
)
from myntos.core.config import Settings, get_settings
from myntos.domain.identity.models import IdentityProvider

logger = logging.getLogger(__name__)

# What a person is shown for each provider: on the button, and in the list of
# ways into their account on /konto/. Kept here rather than on the config so a
# linked identity still has a name after its provider is switched off.
LABELS: dict[IdentityProvider, str] = {
    IdentityProvider.GOOGLE: "Google",
    IdentityProvider.MICROSOFT: "Microsoft",
    IdentityProvider.BANKID: "BankID",
}

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


# --- BankID, through a broker -------------------------------------------------------
#
# Every value below was read from Idura's documentation on 2026-10-10, and the
# discovery document of Idura's own public sample domain
# (https://docs-samples.test.idura.broker/.well-known/openid-configuration) was
# read the same day to confirm the endpoints, the client authentication methods
# and the signing algorithm. docs/ARCHITECTURE.md, "Federated providers", lists
# the pages.

# Idura's ``identityscheme`` for Norwegian BankID, in both of its documented
# token examples (https://docs.idura.app/verify/e-ids/norwegian-bankid/).
IDURA_NORWEGIAN_BANKID = "nobankid-oidc"


def bankid_issuer(configured: str) -> str:
    """The issuer URL for a configured value, which may be a bare domain.

    Idura's dashboard shows a domain (``myntos.test.idura.broker``), and its
    issuer is ``https://`` plus that domain with no trailing slash; reading a
    bare domain that way saves a configuration error nobody would learn from.
    """
    issuer = configured.strip().rstrip("/")
    return issuer if "://" in issuer else f"https://{issuer}"


def bankid_profile(claims: dict[str, Any]) -> OidcProfile:
    """Read a BankID login: the broker's ``sub`` and the person's name, and nothing else.

    **No email.** BankID does not know one, so the profile carries ``None``
    and none of the rules that find an account by address can ever apply.

    **No fødselsnummer.** Idura releases it only for the ``ssn`` scope, which
    is never asked for (``BANKID_SCOPE`` is ``openid``). The product does not
    need it to sign anybody in: the broker's ``sub`` is a stable pseudonym for
    the same person. Asking for it would mean holding a national identity
    number without the need personopplysningsloven § 12 demands for one, and
    Idura would also require a consent step of our own before every such login.
    If one arrives anyway (an application set to Idura's static scope strategy
    with SSN switched on would do it), it is dropped here and the deployment is
    told, without the number, so the setting can be corrected.

    **Not another eID.** Idura brokers many, and ``acr_values`` travels in the
    browser's address bar where a person can edit it. When the token says which
    scheme it came from, it has to be Norwegian BankID; a broker that does not
    say passes, because then there is nothing to compare.

    The birth date, ``uniqueuserid`` and the certificate fields Idura also
    sends are not read at all.
    """
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise OidcError("id_token is missing 'sub'")

    scheme = claims.get("identityscheme")
    if scheme is not None and scheme != IDURA_NORWEGIAN_BANKID:
        raise OidcError(f"the broker authenticated with '{scheme}', not Norwegian BankID")

    if "socialno" in claims:
        logger.warning(
            "The BankID broker sent a fødselsnummer that was not asked for. It was "
            "discarded; check that the application does not request SSN."
        )

    name = claims.get("name")
    if not isinstance(name, str) or not name.strip():
        parts = [claims.get("given_name"), claims.get("family_name")]
        name = " ".join(part.strip() for part in parts if isinstance(part, str) and part.strip())
    if not name:
        raise OidcError(
            "id_token carries no name — Idura always releases it; another broker "
            "may need 'profile' in BANKID_SCOPE"
        )

    return OidcProfile(
        subject=subject.strip(),
        email=None,
        email_verified=False,
        full_name=name.strip(),
    )


def _bankid_key(settings: Settings) -> SigningKey | None:
    """The configured BankID client key, or ``None`` when there is none.

    An unreadable key is a configuration that cannot work, and reads as not
    configured (with the reason) rather than as a button that sends somebody
    through BankID only to fail at the token request.
    """
    try:
        if settings.bankid_key_material is not None:
            return load_signing_key_material(
                settings.bankid_key_material, kid=settings.bankid_kid, source="BANKID_JWK"
            )
        if settings.bankid_key_file is not None:
            return load_signing_key(settings.bankid_key_file, kid=settings.bankid_kid)
    except SigningKeyError as error:
        raise ProviderNotConfiguredError(f"the BankID client key is unusable: {error}") from error
    return None


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
            label=LABELS[provider],
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
            label=LABELS[provider],
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

    if provider is IdentityProvider.BANKID:
        configured_issuer = _clean(settings.bankid_issuer)
        client_id = _clean(settings.bankid_client_id)
        redirect_uri = _clean(settings.bankid_redirect_uri)
        secret = _secret(settings.bankid_client_secret)
        key_is_set = settings.bankid_key_material is not None or settings.bankid_key_file
        if not (configured_issuer and client_id and redirect_uri and (key_is_set or secret)):
            return None
        issuer = bankid_issuer(configured_issuer)
        return ProviderConfig(
            code=provider.value,
            label=LABELS[provider],
            scope=_clean(settings.bankid_scope) or "openid",
            client_id=client_id,
            redirect_uri=redirect_uri,
            # OIDC Discovery 1.0 § 4: the issuer plus this path. Idura serves
            # it exactly there.
            well_known_url=f"{issuer}/.well-known/openid-configuration",
            client_secret=secret,
            signing_key=_bankid_key(settings),
            authorization_params={
                "acr_values": settings.bankid_acr_values.strip(),
                # A real BankID authentication every time, never a broker
                # session reused silently. Deleting an account without a
                # password trusts a login from the last ten minutes as proof
                # that its owner is at the keyboard, and that is only true if
                # the login actually asked them.
                "prompt": "login",
                # Idura supports nb and en for Norwegian BankID and otherwise
                # follows Accept-Language.
                "ui_locales": "nb",
            },
            issuer_matches=exact_issuer,
            profile_from=bankid_profile,
            asserts_email=False,
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
            f"innlogging med {LABELS[provider]} er ikke satt opp i denne installasjonen"
        )
    client = OidcClient(config)
    _CLIENTS[provider] = client
    return client


def configured_providers() -> list[tuple[IdentityProvider, str]]:
    """Every provider this deployment can actually sign a user in with.

    The frontend renders exactly this list, so a provider that is half
    configured never appears as a button that leads nowhere. Nor does one
    whose key cannot be read: that is logged, because otherwise a deployment
    would simply have no BankID button and no reason why.
    """
    settings = get_settings()
    found: list[tuple[IdentityProvider, str]] = []
    for provider in IdentityProvider:
        try:
            config = _build(provider, settings)
        except ProviderNotConfiguredError as error:
            logger.error("%s sign-in is configured but unusable: %s", provider.value, error)
            continue
        if config is not None:
            found.append((provider, config.label))
    return found

"""Application configuration.

All runtime configuration comes from environment variables (12-factor). In
development the repo-root ``.env`` file is read (see ``.env.example`` for the
documented list); in production, inject real environment variables and never
ship a ``.env`` file.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Later entries win, so a backend-local .env can override the repo root one.
        env_file=(REPO_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- General ---
    environment: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # --- Database ---
    database_url: str = "postgresql+asyncpg://myntos:myntos@localhost:5432/myntos"

    # --- CORS (JSON array in the env var) ---
    # Doubles as the trusted-origin allow-list for cookie-authenticated writes
    # (myntos.api.deps), so this is not only a browser-fetch concern.
    cors_origins: list[str] = ["http://localhost:4321"]

    # --- Application accounts (Myntos' own login) ---
    # Absolute URL of the frontend: post-login redirects and invitation links
    # are built from it, so it must be the origin a browser actually reaches.
    frontend_base_url: str = "http://localhost:4321"

    session_cookie_name: str = "myntos_session"
    # Set when the API and the frontend live on different subdomains of one site
    # (e.g. ".myntos.no"). Left unset the cookie is host-only, which is the
    # safer default.
    session_cookie_domain: str | None = None
    # Absolute cap: a session dies at this age however active it has been.
    session_lifetime_days: int = 14
    # Idle cap. A signed-in session can post journal entries and read salary
    # figures, so an unattended browser must not stay usable indefinitely.
    session_idle_timeout_hours: int = 24

    # Length only — see myntos.auth.passwords for why composition rules are not
    # used. 10 is above the NIST SP 800-63B minimum of 8, and short enough that
    # people reach for a passphrase rather than a saved-somewhere-else password.
    # Reported by GET /auth/providers so the sign-up form states the same rule.
    password_min_length: int = 10
    # Per-account throttle. Broad, source-based rate limiting belongs at the
    # edge (Cloudflare), not here.
    login_max_failed_attempts: int = 10
    login_lockout_minutes: int = 15
    invitation_lifetime_days: int = 7

    # --- Federated sign-in (all optional; password login works without any) ---
    # A provider appears in the interface only when its three values are set,
    # so a half-configured one is absent rather than a button that fails.

    # Google Cloud Console → Credentials → OAuth client ID → "Web application".
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str | None = None
    google_well_known_url: str = "https://accounts.google.com/.well-known/openid-configuration"

    # Microsoft Entra ID (Azure AD). Register an application, add a Web
    # redirect URI, and create a client secret.
    microsoft_client_id: str | None = None
    microsoft_client_secret: SecretStr | None = None
    microsoft_redirect_uri: str | None = None
    # Which accounts may sign in: "common" (work, school and personal),
    # "organizations", "consumers", or a tenant GUID to allow only your own.
    # This is an authorization boundary, not a formality — with "common", any
    # Microsoft tenant in the world can start a sign-in.
    microsoft_tenant: str = "common"
    # Derived from the tenant unless set explicitly.
    microsoft_well_known_url: str | None = None

    # Vipps MobilePay Login. Needs a merchant agreement, not just an app
    # registration; client id and secret come from portal.vippsmobilepay.com.
    vipps_client_id: str | None = None
    vipps_client_secret: SecretStr | None = None
    vipps_redirect_uri: str | None = None
    # Test: https://apitest.vipps.no/access-management-1.0/access/.well-known/openid-configuration
    vipps_well_known_url: str = (
        "https://api.vipps.no/access-management-1.0/access/.well-known/openid-configuration"
    )

    # --- Maskinporten (docs/integrations/maskinporten.md) ---
    maskinporten_client_id: str | None = None
    maskinporten_well_known_url: str = (
        "https://test.maskinporten.no/.well-known/oauth-authorization-server"
    )
    maskinporten_scopes: str = ""
    # The private key that signs the JWT grant. Named ``jwk`` for the variable
    # that was already documented, but either encoding is read: a JWK document
    # (``{"kty":"RSA",...}``, possibly wrapped in a ``{"keys":[...]}`` set) or a
    # PEM private key. See myntos.auth.maskinporten.keys.
    maskinporten_jwk_path: Path | None = None
    # The key id registered in Samarbeidsportalen. Only needed when the key file
    # is a PEM, which carries no ``kid`` of its own — a JWK's own ``kid`` wins.
    # Maskinporten looks the signing key up by this value, so a mismatch is an
    # ``invalid_client``, not a signature error.
    maskinporten_kid: str | None = None
    # Acting on a client's behalf (regnskapsfører): the orgnr of the company
    # whose delegation is being used, added to the grant as ``consumer_org``.
    # Normally left unset and passed per request instead — a deployment that
    # keeps books for several companies cannot have one global value.
    maskinporten_consumer_org: str | None = None

    # --- ID-porten (required for validating/submitting MVA-melding & skattemelding) ---
    idporten_client_id: str | None = None
    idporten_well_known_url: str = "https://test.idporten.no/.well-known/openid-configuration"
    idporten_redirect_uri: str | None = None
    # Scopes beyond ``openid``. These are ordered from Skatteetaten and are
    # inert until granted, so they are configuration rather than constants:
    # requesting one the client does not have fails the whole authorization.
    idporten_scopes: str = ""
    # ID-porten clients authenticate with ``private_key_jwt`` (Digdir's default
    # and recommendation) — the same key handling as Maskinporten, a separate
    # key because it is a separate client. A ``client_secret`` client works too;
    # set the secret instead and leave these unset.
    idporten_jwk_path: Path | None = None
    idporten_kid: str | None = None
    idporten_client_secret: SecretStr | None = None
    # Level of assurance demanded of the login. ``idporten-loa-high`` means
    # BankID/Buypass/Commfides rather than MinID — which is what Skatteetaten
    # requires for submissions, and what a signature legally attached to a
    # personnummer should demand anyway.
    idporten_acr_values: str = "idporten-loa-high"

    # Fernet key (urlsafe base64, 32 bytes) that a government access token is
    # encrypted with at rest. Unset, the government login is unavailable rather
    # than stored in the clear: the token authorizes filing a tax return on a
    # named person's behalf, and that is not something to keep in a readable
    # column because a key was inconvenient. Generate one with:
    #   python -c "from cryptography.fernet import Fernet; \
    #              print(Fernet.generate_key().decode())"
    government_token_key: str = ""

    # --- Altinn (token exchange + instance APIs) ---
    altinn_platform_base_url: str = "https://platform.tt02.altinn.no"
    # Altinn 3 apps, one absolute URL each: the app id differs between
    # environments (``…-etm2`` in test, ``…-v1`` in production) and so does the
    # host, which is why these are configuration and not constants. The org
    # prefix is part of the path — ``skd`` for Skatteetaten, ``brg`` for
    # Brønnøysundregistrene.
    # Test: https://skd.apps.tt02.altinn.no/skd/formueinntekt-skattemelding-v2
    # Prod: https://skd.apps.altinn.no/skd/formueinntekt-skattemelding-v2
    altinn_skattemelding_app_url: str | None = None
    # Test: https://skd.apps.tt02.altinn.no/skd/mva-melding-innsending-etm2
    # Prod: https://skd.apps.altinn.no/skd/mva-melding-innsending-v1
    altinn_mva_app_url: str | None = None

    # --- Altinn systembruker (system vendor registration) ---
    # Our own organisasjonsnummer, as the *vendor* of this system. Not the same
    # as maskinporten_consumer_org, which is a customer we act for: this is who
    # offers the system. It forms the system id, so it cannot be derived from a
    # token at registration time.
    altinn_vendor_orgnr: str = ""
    # The vendor-chosen half of the system id, which Altinn requires to be
    # "{vendor orgnr}_{name}". Permanent in practice: once a customer has
    # approved a system user against an id, changing it means every customer
    # approving again.
    altinn_system_name: str = "myntos"
    # Where a customer's browser may return after approving a system user.
    # Altinn requires https, so a loopback development URL is not valid here
    # even in test.
    altinn_system_redirect_url: str = "https://myntos.no/skatt/systembruker/godkjent"

    # --- Skatteetaten ---
    # Three separate hosts, because Skatteetaten splits them by auth plane and
    # the test names do not follow one pattern (docs/integrations/).
    # Maskinporten plane — test: https://api-test.sits.no
    #                      prod: https://api.skatteetaten.no
    skatteetaten_api_base_url: str | None = None
    # ID-porten plane (skattemelding) — test: https://idporten-api-test.sits.no
    #                                   prod: https://idporten.api.skatteetaten.no
    skatteetaten_idporten_api_base_url: str | None = None
    # MVA validation — test: https://idporten-api-sbstest.sits.no
    #                  prod: https://idporten.api.skatteetaten.no
    skatteetaten_mva_api_base_url: str | None = None

    # --- Løynd (the model that reads an uploaded bilag) ---
    # Optional in the same sense as the sign-in providers: without a key the
    # model is simply not asked, and the register falls back to its own reading
    # of the document. Norwegian-hosted inference is the reason this particular
    # service is the one allowed to see a user's bilag — see
    # myntos.integrations.loynd.
    loynd_key: SecretStr | None = None
    loynd_base_url: str = "https://api.loynd.no"
    loynd_model: str = "loynd-lite"

    # --- Enhetsregisteret (Brønnøysundregistrene) ---
    # Open data, no credentials. Configurable only so tests can point it at a
    # stub instead of the real register.
    brreg_api_base_url: str = "https://data.brreg.no/enhetsregisteret/api"

    # --- Document storage for bilag ---
    storage_backend: Literal["local", "r2"] = "local"
    local_storage_path: Path = REPO_ROOT / "data" / "bilag"
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: SecretStr | None = None
    r2_bucket: str | None = None
    # Part of the endpoint hostname, not a preference: a bucket created with
    # jurisdiction "eu" lives in a separate namespace reachable only at
    # <account>.eu.r2.cloudflarestorage.com, and the jurisdiction cannot be
    # changed after creation. Defaulting to "eu" means keeping accounting
    # records inside the EEA is what happens unless someone opts out.
    r2_jurisdiction: Literal["eu", "fedramp", "default"] = "eu"
    # Full override of the S3 endpoint, for a self-hosted S3-compatible store or
    # a test double. Normally derived from the account id and jurisdiction.
    r2_endpoint_url: str | None = None

    @property
    def maskinporten_key_file(self) -> Path | None:
        """The Maskinporten key file as an absolute path, or ``None``."""
        return _repo_relative(self.maskinporten_jwk_path)

    @property
    def idporten_key_file(self) -> Path | None:
        """The ID-porten key file as an absolute path, or ``None``."""
        return _repo_relative(self.idporten_jwk_path)

    @property
    def session_cookie_secure(self) -> bool:
        """HTTPS-only cookies everywhere except local development.

        Derived rather than configurable: a deployment that has to *opt in* to
        secure cookies is a deployment where someone will forget.
        """
        return self.environment != "development"


def _repo_relative(path: Path | None) -> Path | None:
    """Resolve a configured path against the repository root, not the cwd.

    ``.env`` documents ``./secrets/maskinporten.jwk.json``, and the backend is
    normally started from ``backend/`` — so a relative path read literally would
    point at a file that is not there. Anchoring on the repo root makes the
    documented value mean what it looks like it means, and an absolute path
    (what production should use) passes through untouched.
    """
    if path is None:
        return None
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()

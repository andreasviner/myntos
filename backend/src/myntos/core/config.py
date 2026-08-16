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
    maskinporten_jwk_path: Path | None = None

    # --- ID-porten (required for validating/submitting MVA-melding & skattemelding) ---
    idporten_client_id: str | None = None
    idporten_well_known_url: str = "https://test.idporten.no/.well-known/openid-configuration"
    idporten_redirect_uri: str | None = None

    # --- Altinn (token exchange + instance APIs) ---
    altinn_platform_base_url: str = "https://platform.tt02.altinn.no"

    # --- Skatteetaten ---
    skatteetaten_api_base_url: str | None = None

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
    def session_cookie_secure(self) -> bool:
        """HTTPS-only cookies everywhere except local development.

        Derived rather than configurable: a deployment that has to *opt in* to
        secure cookies is a deployment where someone will forget.
        """
        return self.environment != "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()

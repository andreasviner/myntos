"""Application configuration.

All runtime configuration comes from environment variables (12-factor). In
development the repo-root ``.env`` file is read (see ``.env.example`` for the
documented list); in production, inject real environment variables and never
ship a ``.env`` file.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    cors_origins: list[str] = ["http://localhost:4321"]

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

    # --- Document storage for bilag ---
    storage_backend: Literal["local", "r2"] = "local"
    local_storage_path: Path = REPO_ROOT / "data" / "bilag"
    r2_account_id: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_bucket: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()

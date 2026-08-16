"""Document storage for bilag attachments (see base.py for the contract)."""

from functools import lru_cache

from pydantic import SecretStr

from myntos.core.config import Settings, get_settings
from myntos.storage.base import DocumentStorage, ObjectNotFoundError, StorageError
from myntos.storage.local import LocalDocumentStorage
from myntos.storage.r2 import R2DocumentStorage, endpoint_for

__all__ = [
    "DocumentStorage",
    "LocalDocumentStorage",
    "ObjectNotFoundError",
    "R2DocumentStorage",
    "StorageError",
    "StorageNotConfiguredError",
    "get_storage",
]


class StorageNotConfiguredError(RuntimeError):
    """The configured storage backend is missing something it needs."""


def _set(value: str | SecretStr | None) -> str | None:
    """The value if it was actually provided, else ``None``.

    A blank string counts as absent. ``.env.example`` ships ``R2_BUCKET=`` and
    every secret manager has a way of producing an empty variable, so treating
    unset and empty differently would mean a deployment could pass the
    configuration check with no credentials at all and only discover it when a
    user tried to file a receipt.
    """
    if value is None:
        return None
    raw = value.get_secret_value() if isinstance(value, SecretStr) else value
    return raw.strip() or None


def _r2_storage(settings: Settings) -> R2DocumentStorage:
    """Build the R2 backend, refusing to start half-configured.

    Everything is checked up front rather than at the first upload: a
    deployment missing a secret should fail while someone is watching it.
    """
    access_key_id = _set(settings.r2_access_key_id)
    secret_access_key = _set(settings.r2_secret_access_key)
    bucket = _set(settings.r2_bucket)
    account_id = _set(settings.r2_account_id)
    endpoint = _set(settings.r2_endpoint_url)

    missing = [
        name
        for name, value in (
            ("R2_ACCESS_KEY_ID", access_key_id),
            ("R2_SECRET_ACCESS_KEY", secret_access_key),
            ("R2_BUCKET", bucket),
        )
        if value is None
    ]
    if endpoint is None:
        if account_id is None:
            missing.append("R2_ACCOUNT_ID (eller R2_ENDPOINT_URL)")
        else:
            endpoint = endpoint_for(account_id, settings.r2_jurisdiction)

    if missing or endpoint is None or access_key_id is None or secret_access_key is None:
        raise StorageNotConfiguredError(
            f"STORAGE_BACKEND=r2 mangler konfigurasjon: {', '.join(missing)}"
        )
    # mypy: bucket is in the same list as the two checked above.
    assert bucket is not None  # noqa: S101

    return R2DocumentStorage(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket=bucket,
        endpoint_url=endpoint,
    )


@lru_cache
def get_storage() -> DocumentStorage:
    """The storage backend for this deployment.

    Cached: a backend holds configuration and a connection pool, not
    per-request state.
    """
    settings = get_settings()
    if settings.storage_backend == "local":
        return LocalDocumentStorage(settings.local_storage_path)
    return _r2_storage(settings)

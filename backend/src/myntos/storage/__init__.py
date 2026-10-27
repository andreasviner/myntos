"""Document storage for bilag attachments (see base.py for the contract), and working copies.

The working copies (:mod:`myntos.storage.working`) are the other kind of file:
uploads a job reads and then deletes, in a bucket of their own.
"""

from dataclasses import dataclass
from functools import lru_cache

from pydantic import SecretStr

from myntos.core.config import Settings, get_settings
from myntos.storage.base import DocumentStorage, ObjectNotFoundError, StorageError
from myntos.storage.local import LocalDocumentStorage
from myntos.storage.r2 import R2DocumentStorage, endpoint_for
from myntos.storage.working import LocalWorkingStore, R2WorkingStore, WorkingStore

__all__ = [
    "DocumentStorage",
    "LocalDocumentStorage",
    "LocalWorkingStore",
    "ObjectNotFoundError",
    "R2DocumentStorage",
    "R2WorkingStore",
    "StorageError",
    "StorageNotConfiguredError",
    "WorkingStore",
    "get_storage",
    "get_working_store",
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


@dataclass(frozen=True)
class _R2Access:
    access_key_id: str
    secret_access_key: str
    bucket: str
    endpoint_url: str


def _r2_access(settings: Settings, *, bucket_setting: str, bucket: str | None) -> _R2Access:
    """The credentials and endpoint for one bucket, refusing them half-configured.

    Everything is checked up front rather than at the first upload: a
    deployment missing a secret should fail while someone is watching it.
    """
    access_key_id = _set(settings.r2_access_key_id)
    secret_access_key = _set(settings.r2_secret_access_key)
    bucket = _set(bucket)
    account_id = _set(settings.r2_account_id)
    endpoint = _set(settings.r2_endpoint_url)

    missing = [
        name
        for name, value in (
            ("R2_ACCESS_KEY_ID", access_key_id),
            ("R2_SECRET_ACCESS_KEY", secret_access_key),
            (bucket_setting, bucket),
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
    return _R2Access(
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        bucket=bucket,
        endpoint_url=endpoint,
    )


def _r2_storage(settings: Settings) -> R2DocumentStorage:
    """Build the R2 backend for the bilag archive."""
    access = _r2_access(settings, bucket_setting="R2_BUCKET", bucket=settings.r2_bucket)
    return R2DocumentStorage(
        access_key_id=access.access_key_id,
        secret_access_key=access.secret_access_key,
        bucket=access.bucket,
        endpoint_url=access.endpoint_url,
    )


def _r2_working(settings: Settings) -> R2WorkingStore:
    """Build the R2 store for working copies: the archive's account, its own bucket."""
    access = _r2_access(
        settings, bucket_setting="R2_WORKING_BUCKET", bucket=settings.r2_working_bucket
    )
    if access.bucket == _set(settings.r2_bucket):
        # The sweep deletes whatever no import row points at, under work/ only,
        # but the archive's whole promise is that nothing in its bucket is
        # ever deleted, so the two are never one bucket.
        raise StorageNotConfiguredError(
            "R2_WORKING_BUCKET kan ikke være samme bøtte som R2_BUCKET, der ingenting slettes"
        )
    return R2WorkingStore(
        access_key_id=access.access_key_id,
        secret_access_key=access.secret_access_key,
        bucket=access.bucket,
        endpoint_url=access.endpoint_url,
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


@lru_cache
def get_working_store() -> WorkingStore:
    """Where uploads a job reads and then deletes are kept (:mod:`myntos.storage.working`).

    Built at first use, like :func:`get_storage`, and on the same switch: R2 in
    production, a directory in development.
    """
    settings = get_settings()
    if settings.storage_backend == "local":
        return LocalWorkingStore(settings.local_working_path)
    return _r2_working(settings)

"""Test fixtures for the HTTP API.

The API tests run the real application against a real (SQLite) database, so a
signup exercises the endpoint, the service layer, the ORM and the schema
together — the seams where an auth bug actually hides.

Two details are deliberate:

- **The schema is created with a synchronous engine on the same file.** An
  ``AsyncEngine`` binds to whichever event loop first connects on it, and
  ``TestClient`` runs the application in a loop of its own. Creating tables from
  a pytest-owned loop and then serving requests from the client's loop is the
  standard way to get "attached to a different loop" failures; doing the setup
  synchronously sidesteps the question entirely.

- **The client speaks ``https://testserver``.** Outside development the session
  cookie is marked ``Secure``, and an HTTP test client would silently refuse to
  send it back — the tests would then be exercising a code path no browser uses.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from myntos.api.deps import get_document_storage
from myntos.core.config import get_settings
from myntos.db.base import Base
from myntos.db.session import get_session
from myntos.domain.bilag import models as bilag_models  # noqa: F401  (registers tables)
from myntos.domain.faktura import models as faktura_models  # noqa: F401  (registers tables)
from myntos.domain.identity import models as identity_models  # noqa: F401  (registers tables)
from myntos.domain.ledger import models as ledger_models  # noqa: F401  (registers tables)
from myntos.main import create_app
from myntos.storage.local import LocalDocumentStorage

# The frontend origin the tests pretend to be. Cookie-authenticated writes are
# checked against the CORS allow-list, so the two have to agree.
TEST_ORIGIN = "https://testserver"

# Every password used in the tests. Long enough for the policy; the point of the
# constant is that a policy change breaks one line rather than twenty.
PASSWORD = "korrekt-hest-batteri-stift"


@pytest.fixture(scope="session", autouse=True)
def test_settings() -> Iterator[None]:
    """Pin the settings the API tests assume.

    Environment variables outrank the repo-root ``.env``, so this holds whether
    or not the developer running the suite has one.
    """
    patcher = pytest.MonkeyPatch()
    patcher.setenv("ENVIRONMENT", "test")
    patcher.setenv("CORS_ORIGINS", f'["{TEST_ORIGIN}"]')
    patcher.setenv("FRONTEND_BASE_URL", "https://app.example")
    # Low enough to lock out inside a test without twenty requests.
    patcher.setenv("LOGIN_MAX_FAILED_ATTEMPTS", "3")
    # No model, ever. Environment variables outrank the repo-root .env, so this
    # holds even for a developer whose own key is sitting in one — a suite that
    # reaches the network is a suite that fails for reasons unrelated to the
    # code. The tests that want a model stub it themselves.
    patcher.setenv("LOYND_KEY", "")
    # And no federated provider, for the same reason. Several tests assert on
    # what an *unconfigured* deployment offers, so a developer with real client
    # credentials in their .env would see them fail while CI — which has none —
    # passes. The OIDC tests do not need these set: they replace the client
    # itself (`auth_endpoints._client_for`), not the configuration.
    for provider in ("GOOGLE", "MICROSOFT", "VIPPS"):
        patcher.setenv(f"{provider}_CLIENT_ID", "")
        patcher.setenv(f"{provider}_CLIENT_SECRET", "")
        patcher.setenv(f"{provider}_REDIRECT_URI", "")
    get_settings.cache_clear()
    yield
    patcher.undo()
    get_settings.cache_clear()


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """A fresh SQLite file per test, with the schema already created."""
    path = tmp_path / "myntos-test.db"
    sync_engine = sa.create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    return f"sqlite+aiosqlite:///{path}"


@pytest.fixture
def client(database_url: str, tmp_path: Path) -> Iterator[TestClient]:
    engine = create_async_engine(database_url)

    # SQLite ignores foreign keys unless asked, and half of what the identity
    # schema relies on is ON DELETE behaviour.
    @sa.event.listens_for(engine.sync_engine, "connect")
    def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        # Mirrors myntos.db.session.get_session: one transaction per request.
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    # Bilag uploads must not land in the developer's repository. A per-test
    # directory also means one test's PDFs cannot be read by another's.
    storage = LocalDocumentStorage(tmp_path / "bilag")
    app.dependency_overrides[get_document_storage] = lambda: storage
    with TestClient(app, base_url=TEST_ORIGIN) as test_client:
        yield test_client
    engine.sync_engine.dispose()


def register(
    client: TestClient,
    email: str = "kari@example.no",
    *,
    full_name: str = "Kari Nordmann",
    organization_name: str | None = None,
    password: str = PASSWORD,
) -> dict[str, Any]:
    """Register an account and return the session response body."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": password,
            "full_name": full_name,
            "organization_name": organization_name,
        },
        headers={"Origin": TEST_ORIGIN},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def write(client: TestClient, method: str, url: str, **kwargs: Any) -> Any:
    """A state-changing request carrying the ``Origin`` a browser would send.

    Cookie-authenticated writes are origin-checked, so every test write goes
    through here rather than remembering the header at each call site.
    """
    headers = {"Origin": TEST_ORIGIN, **kwargs.pop("headers", {})}
    return client.request(method, url, headers=headers, **kwargs)

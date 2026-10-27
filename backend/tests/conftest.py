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

import contextlib
import functools
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from myntos.api.deps import (
    KEY_SCOPE_EXTENSION,
    KEY_SEND_EXTENSION,
    get_document_storage,
    get_mailer,
    get_working_files,
)
from myntos.api.v1.saft_innlesing import get_import_sessions
from myntos.auth import agent_keys
from myntos.core.config import Settings, get_settings
from myntos.core.mail import Delivery, Mail
from myntos.db.base import Base
from myntos.db.session import get_session
from myntos.domain.arsoppgjor import models as arsoppgjor_models  # noqa: F401  (registers tables)
from myntos.domain.bank import models as bank_models  # noqa: F401  (registers tables)
from myntos.domain.bilag import models as bilag_models  # noqa: F401  (registers tables)
from myntos.domain.bilag import reading_cache, readings
from myntos.domain.faktura import models as faktura_models  # noqa: F401  (registers tables)
from myntos.domain.feedback import models as feedback_models  # noqa: F401  (registers tables)
from myntos.domain.identity import models as identity_models  # noqa: F401  (registers tables)
from myntos.domain.ledger import models as ledger_models  # noqa: F401  (registers tables)
from myntos.domain.mva import models as mva_models  # noqa: F401  (registers tables)
from myntos.integrations import brreg, norgesbank
from myntos.main import create_app
from myntos.saft import models as saft_models  # noqa: F401  (registers tables)
from myntos.skattemelding import models as skattemelding_models  # noqa: F401  (registers tables)
from myntos.storage.local import LocalDocumentStorage
from myntos.storage.working import LocalWorkingStore

# The frontend origin the tests pretend to be. Cookie-authenticated writes are
# checked against the CORS allow-list, so the two have to agree.
TEST_ORIGIN = "https://testserver"

# Every password used in the tests. Long enough for the policy; the point of the
# constant is that a policy change breaks one line rather than twenty.
PASSWORD = "korrekt-hest-batteri-stift"


@pytest.fixture(scope="session", autouse=True)
def test_settings() -> Iterator[None]:
    """Pin the settings the API tests assume.

    No ``.env`` is read at all, so the suite gives the same answer on a
    developer's machine, on the production box and in CI.
    """
    patcher = pytest.MonkeyPatch()
    # The root of it. On the production box the repo-root .env is the live
    # configuration, and reading it made a couple of hundred tests assert on a
    # configured deployment where CI has an unconfigured one. Blanking settings
    # one at a time (below) kept missing the newest, so the standing advice was
    # to move .env aside before every run, and a run interrupted halfway left
    # production without its configuration at the next restart.
    patcher.setitem(Settings.model_config, "env_file", None)
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
    for provider in ("GOOGLE", "MICROSOFT", "BANKID"):
        patcher.setenv(f"{provider}_CLIENT_ID", "")
        patcher.setenv(f"{provider}_CLIENT_SECRET", "")
        patcher.setenv(f"{provider}_REDIRECT_URI", "")
    # BankID's issuer and key, which the BankID tests set for themselves.
    for name in ("BANKID_ISSUER", "BANKID_JWK_PATH", "BANKID_JWK", "BANKID_KID"):
        patcher.setenv(name, "")
    # And the government login, which is the same trap with higher stakes: this
    # repository's own .env holds a working ID-porten test client, so a suite
    # that read it would assert on a configured deployment here and an
    # unconfigured one in CI. Tests that want it configured set these
    # themselves.
    for name in (
        "IDPORTEN_CLIENT_ID",
        "IDPORTEN_CLIENT_SECRET",
        "IDPORTEN_JWK_PATH",
        "IDPORTEN_KID",
        "IDPORTEN_REDIRECT_URI",
        "IDPORTEN_SCOPES",
        "GOVERNMENT_TOKEN_KEY",
    ):
        patcher.setenv(name, "")
    get_settings.cache_clear()
    yield
    patcher.undo()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def a_cold_reading_cache() -> Iterator[None]:
    """Every test reads its documents for itself.

    ``reading_cache.CACHE`` is process-wide and keyed by the bytes of the file,
    which is exactly what makes it useful in production and exactly what makes
    it leak here: two tests uploading the same fixture PDF are the same key, and
    the second would be handed the first one's text no matter what it had
    stubbed Løynd's extractor to say. Emptied on the way in and on the way out,
    so neither the order tests run in nor a failure half-way through one can
    decide what the next one reads. The readings kept for answering
    (``readings.STORE``) go with it, for the same reason.
    """
    reading_cache.CACHE.clear()
    readings.STORE.clear()
    yield
    reading_cache.CACHE.clear()
    readings.STORE.clear()


@pytest.fixture(autouse=True)
def fresh_key_counters() -> Iterator[None]:
    """Every test counts its keys' requests and refusals from zero.

    The counters live in the process (myntos.auth.agent_keys, rule 10) and are
    keyed by key id, and every test's database hands out id 1 again. Without
    this, the boundary tests' refusals would add up across tests until some
    unrelated test's key was stopped for probing.
    """
    agent_keys.forget_counts()
    yield
    agent_keys.forget_counts()


@pytest.fixture(autouse=True)
def no_exchange_rates() -> Iterator[None]:
    """No calls to Norges Bank, for the same reason there are no calls to Løynd.

    The rate lookup is best-effort and silent about failing, so a suite that
    reached the network would pass either way while quietly depending on a
    public API being up and on today's midkurs. Refusing by default makes the
    absence of a rate the tested default; the tests that want a conversion
    supply the rate themselves.
    """
    patcher = pytest.MonkeyPatch()

    async def refuse(currency_code: str, *, on: date) -> norgesbank.Rate:
        raise norgesbank.RateUnavailableError(f"no network in tests ({currency_code} on {on})")

    patcher.setattr(norgesbank, "rate_for", refuse)
    yield
    patcher.undo()


@pytest.fixture(autouse=True)
def no_register_lookups(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """No calls to Brønnøysundregistrene, for the reason above.

    Creating a company now reads the register to fill in the næringskode and
    contact details, and confirming one reads it again. Both are best effort and
    swallow a failure, so a suite that reached the network would pass either way
    while quietly depending on Brønnøysund being up — and would spend a real
    round trip in every test that sets up a set of books.

    Refusing by default makes "the register said nothing" the tested default.
    The tests that want an answer install their own transport
    (``brreg_answers`` in test_enhetsregisteret_api.py), which replaces the same
    seam and therefore overrides this.
    """
    patcher = pytest.MonkeyPatch()

    def refuse() -> httpx.AsyncClient:
        def unreachable(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no network in tests", request=request)

        return httpx.AsyncClient(transport=httpx.MockTransport(unreachable))

    patcher.setattr(brreg, "_client", refuse)
    yield
    patcher.undo()


class RecordingMailer:
    """The mailer every test gets: keeps the messages, sends nothing.

    Installed by default rather than opted into, for the reason
    ``no_register_lookups`` is: this repository's own ``.env`` may hold working
    SMTP credentials, and a suite that used them would mail real people from a
    test run. Nothing here opens a socket.

    ``configured`` and ``fails_with`` are what the tests set to describe a
    deployment that never set up mail, and one whose relay refused the message.
    """

    def __init__(self) -> None:
        self.sent: list[Mail] = []
        self.configured = True
        self.fails_with: str | None = None

    async def send(self, mail: Mail) -> Delivery:
        if not self.configured:
            return Delivery(sent=False)
        if self.fails_with is not None:
            return Delivery(sent=False, error=self.fails_with)
        self.sent.append(mail)
        return Delivery(sent=True)

    @property
    def only(self) -> Mail:
        """The single message sent, asserting that there was exactly one."""
        assert len(self.sent) == 1, f"expected one message, got {len(self.sent)}"
        return self.sent[0]


@pytest.fixture
def outbox() -> RecordingMailer:
    """What the application sent, for the test to read afterwards."""
    return RecordingMailer()


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    """A fresh SQLite file per test, with the schema already created."""
    path = tmp_path / "myntos-test.db"
    sync_engine = sa.create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    return f"sqlite+aiosqlite:///{path}"


@pytest.fixture
def client(database_url: str, tmp_path: Path, outbox: RecordingMailer) -> Iterator[TestClient]:
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
    # Uploaded SAF-T files and printouts the same, in a directory of their own.
    working = LocalWorkingStore(tmp_path / "working")
    app.dependency_overrides[get_working_files] = lambda: working
    app.dependency_overrides[get_mailer] = lambda: outbox
    # The SAF-T import works after its request has answered, in sessions of its
    # own: they must open on this database too.
    app.dependency_overrides[get_import_sessions] = lambda: session_factory
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


def invite_to_company(
    client: TestClient,
    company_id: int,
    *,
    email: str,
    role: str = "member",
    full_name: str | None = None,
) -> str:
    """Invite an address to one company's books and return the acceptance token.

    Called as whoever is signed in, who therefore needs ``member:manage`` on
    that company. Access is per company, so there is no way to invite somebody
    to "the account": that is the whole point of
    :class:`~myntos.domain.identity.models.CompanyMembership`.
    """
    payload: dict[str, Any] = {"email": email, "role": role}
    if full_name is not None:
        payload["full_name"] = full_name
    response = write(client, "POST", f"/api/v1/companies/{company_id}/invitations", json=payload)
    assert response.status_code == 201, response.text
    url: str = response.json()["invite_url"]
    return url.split("token=")[1]


def join_company(
    client: TestClient,
    company_id: int,
    *,
    email: str,
    role: str = "member",
    full_name: str = "Ola Nordmann",
) -> dict[str, Any]:
    """Invite somebody to one company, then become them.

    The whole round trip the product actually asks of a colleague: the person
    signed in invites an address, signs out, that address registers an account
    of its own, and accepts. Returns the invitee's session body, and leaves the
    client signed in **as the invitee** — which is what every test using this
    goes on to assert about.

    Their new account has a set of books of its own (signup makes none, so it is
    empty) and exactly one company they can open: the one they were invited to.
    """
    token = invite_to_company(client, company_id, email=email, role=role)
    write(client, "POST", "/api/v1/auth/logout")
    session = register(client, email, full_name=full_name)
    accepted = write(client, "POST", "/api/v1/auth/invitations/accept", json={"token": token})
    assert accepted.status_code == 200, accepted.text
    body: dict[str, Any] = accepted.json()
    return body or session


ALL_SCOPES = ("read", "write", "post")


@functools.cache
def _declared_scopes() -> dict[tuple[str, str], str | None]:
    schema = create_app().openapi()
    return {
        (method.upper(), path): operation.get(KEY_SCOPE_EXTENSION)
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
    }


@functools.cache
def _declared_sends() -> frozenset[tuple[str, str]]:
    schema = create_app().openapi()
    return frozenset(
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method, operation in operations.items()
        if operation.get(KEY_SEND_EXTENSION) is True
    )


def is_send(method: str, path: str) -> bool:
    """Whether a key's request to the route always waits for its person (``key_send``)."""
    return (method, path) in _declared_sends()


def assert_key_refused(response: Any, method: str, path: str) -> None:
    """What a key is answered on a route it must not reach (rule 9).

    A route open to keys answers 404, exactly as it does a session: the id is
    not this key's to know about. A route closed to keys answers 403
    ``not_for_agent_keys`` before any id is looked at, which says nothing about
    whether the id exists. Never a 2xx, and never anything else.
    """
    if _declared_scopes()[(method, path)] in (None, "none"):
        assert response.status_code == 403, (
            f"{method} {path} answered {response.status_code} to a key: {response.text[:200]}"
        )
        assert response.json()["code"] == "not_for_agent_keys", response.text[:200]
    else:
        assert response.status_code == 404, (
            f"{method} {path} answered {response.status_code} to a key: {response.text[:200]}"
        )


def connection_key(
    client: TestClient,
    company_id: int,
    *,
    scopes: tuple[str, ...] = ALL_SCOPES,
    approval_scopes: tuple[str, ...] = (),
    name: str = "Testagent",
) -> str:
    """Switch keys on for a company, as whoever is signed in, and make one.

    Returns the connection key the Tilgang page would show. The person signed
    in needs ``member:manage`` for the switch, which every company's creator
    has; tests about who may switch it do so themselves.
    """
    switched = write(
        client, "PUT", f"/api/v1/companies/{company_id}/agent-access", json={"enabled": True}
    )
    assert switched.status_code == 200, switched.text
    made = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/agent-keys",
        json={"name": name, "scopes": list(scopes), "approval_scopes": list(approval_scopes)},
    )
    assert made.status_code == 201, made.text
    key: str = made.json()["connection_key"]
    return key


def agent_tokens(client: TestClient, key: str) -> dict[str, Any]:
    """Exchange a connection key, as an agent would, and return the answer."""
    response = client.post(
        "/api/v1/agent/token", json={"grant_type": "connection_key", "connection_key": key}
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def agent_header(
    client: TestClient,
    company_id: int,
    *,
    scopes: tuple[str, ...] = ALL_SCOPES,
    approval_scopes: tuple[str, ...] = (),
) -> dict[str, str]:
    """An ``Authorization`` header for a fresh key on ``company_id``.

    The whole round trip a person and their agent make: switch on, make the
    key, exchange it. The client stays signed in as the person, so a test can
    go on acting as either; a request carrying this header is the agent, since
    a bearer token is read before the cookie.
    """
    tokens = agent_tokens(
        client,
        connection_key(client, company_id, scopes=scopes, approval_scopes=approval_scopes),
    )
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def write(client: TestClient, method: str, url: str, **kwargs: Any) -> Any:
    """A state-changing request carrying the ``Origin`` a browser would send.

    Cookie-authenticated writes are origin-checked, so every test write goes
    through here rather than remembering the header at each call site.
    """
    headers = {"Origin": TEST_ORIGIN, **kwargs.pop("headers", {})}
    return client.request(method, url, headers=headers, **kwargs)


def insert_mva_filing(
    client: TestClient,
    company_id: int,
    *,
    year: int,
    index: int,
    status: str = "validert",
    fastsatt: str = "0.00",
    period_type: str = "toMaaneder",
    content_hash: str = "0" * 64,
    validation_xml: str = (
        '<valideringsresultat xmlns="no:skatteetaten:fastsetting:avgift:mva:'
        'valideringsresultat:v1"><avvikVedMeldingslevering>ingen avvik'
        "</avvikVedMeldingslevering></valideringsresultat>"
    ),
    altinn_instance_id: str | None = None,
) -> int:
    """A filing row written straight into the app's database, and its id.

    Through the app's own session factory and on the app's own event loop
    (``client.portal``): the async engine's connections belong to that loop, and
    a second one would deadlock on SQLite. Driving the dependency generator to
    its end is what commits, exactly as a request would.

    For tests that need a filing to exist without standing up ID-porten and
    Skatteetaten's validation to produce one, such as the company wall.
    """
    from myntos.domain.mva.models import MvaFiling

    override = client.app.dependency_overrides[get_session]  # type: ignore[attr-defined]

    async def insert() -> int:
        sessions = override()
        session = await sessions.__anext__()
        filing = MvaFiling(
            company_id=company_id,
            year=year,
            period_type=period_type,
            period_index=index,
            category="alminnelig",
            status=status,
            reference=str(uuid.uuid4()),
            melding_xml="<mvaMeldingDto/>",
            content_hash=content_hash,
            fastsatt_merverdiavgift=Decimal(fastsatt),
            lines=[],
            validation_status="ingen avvik",
            validation_xml=validation_xml,
            altinn_instance_id=altinn_instance_id,
            documents=[],
        )
        session.add(filing)
        await session.flush()
        filing_id = int(filing.id)
        with contextlib.suppress(StopAsyncIteration):
            await sessions.__anext__()
        return filing_id

    portal = client.portal
    assert portal is not None, "insert_mva_filing needs the client inside its with-block"
    return portal.call(insert)


def insert_skattemelding_filing(
    client: TestClient,
    company_id: int,
    *,
    year: int,
    status: str = "validert",
    skattemessig_resultat: str = "0.00",
    altinn_instance_id: str | None = None,
) -> int:
    """A skattemelding filing row written straight into the app's database, and its id.

    The MVA helper's reasoning and session handling. The validation answer is
    Skatteetaten's real one from TT02, because reading a filing parses it.
    """
    from myntos.skattemelding.models import SkattemeldingFiling

    validation_xml = (
        Path(__file__).parent / "fixtures" / "skattemelding" / "tt02-validering-314067630-2025.xml"
    ).read_text(encoding="utf-8")
    override = client.app.dependency_overrides[get_session]  # type: ignore[attr-defined]

    async def insert() -> int:
        sessions = override()
        session = await sessions.__anext__()
        filing = SkattemeldingFiling(
            company_id=company_id,
            year=year,
            status=status,
            melding_id="SKI:755:32858",
            partsnummer=3001570199,
            request_xml="<skattemeldingOgNaeringsspesifikasjonRequest/>",
            content_hash="0" * 64,
            answers={},
            has_auditor=False,
            aarsresultat=Decimal(skattemessig_resultat),
            skattemessig_resultat=Decimal(skattemessig_resultat),
            findings=[],
            validation_status="validertOK",
            validation_xml=validation_xml,
            altinn_instance_id=altinn_instance_id,
            documents=[],
        )
        session.add(filing)
        await session.flush()
        filing_id = int(filing.id)
        with contextlib.suppress(StopAsyncIteration):
            await sessions.__anext__()
        return filing_id

    portal = client.portal
    assert portal is not None, "insert_skattemelding_filing needs the client inside its with-block"
    return portal.call(insert)


def insert_imported_document(
    client: TestClient, company_id: int, *, content: bytes = b"%PDF-1.7\n%%EOF\n"
) -> int:
    """A document kept from an old program's export, stored and recorded, and its id.

    The bytes go to the app's own document store and the row through the app's
    own session, as in ``insert_mva_filing``. For tests that need one to exist
    without a SAF-T export posted to produce it, such as the company wall.
    """
    import hashlib

    from myntos.saft.models import ImportedDocument

    storage = client.app.dependency_overrides[get_document_storage]()  # type: ignore[attr-defined]
    override = client.app.dependency_overrides[get_session]  # type: ignore[attr-defined]

    async def insert() -> int:
        key = await storage.save(
            content=content, filename="kvittering.pdf", content_type="application/pdf"
        )
        sessions = override()
        session = await sessions.__anext__()
        document = ImportedDocument(
            company_id=company_id,
            software="Fiken AS",
            export_name="fiken-komplett.zip",
            path="Bilag/kvittering.pdf",
            filename="kvittering.pdf",
            content_type="application/pdf",
            byte_size=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            storage_key=key,
        )
        session.add(document)
        await session.flush()
        document_id = int(document.id)
        with contextlib.suppress(StopAsyncIteration):
            await sessions.__anext__()
        return document_id

    portal = client.portal
    assert portal is not None, "insert_imported_document needs the client inside its with-block"
    return portal.call(insert)

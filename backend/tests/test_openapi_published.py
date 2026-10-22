"""The OpenAPI document is published, in production too, and the docs page is not.

docs/agents-and-api.md rule 11: ``/api/v1/openapi.json`` is the reference
developers and agents build against, so production serves it to anybody who
asks, without a credential. It used to be served only outside production, with
the interactive page, on the reasoning that API docs are a development tool.
That holds for the page, which loads its script from a CDN and sends requests
from a form, and not for the document, which says nothing the public source
does not, and which every route already uses to say what a key may do.

What every operation in the document must carry is held by
``test_agent_surface.py``, which reads the schema from an application built
with the test settings. So the last test here checks that this is the same
document production publishes, rather than repeating those checks.
"""

from collections.abc import Callable, Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from myntos.api.deps import KEY_SCOPE_EXTENSION
from myntos.core.config import get_settings
from myntos.main import PUBLIC_API_URL, create_app
from tests.conftest import TEST_ORIGIN

pytestmark = pytest.mark.usefixtures("test_settings")

SCHEMA = "/api/v1/openapi.json"


@pytest.fixture
def app_in(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], FastAPI]]:
    """Build the application as a given environment would.

    Settings are cached for the life of the process, so the cache is emptied
    on the way in and again on the way out; otherwise the next test would be
    handed production's settings.

    The client is used without ``with``, so the lifespan never runs: it opens
    a database connection, and nothing asked here needs one.
    """

    def build(environment: str) -> FastAPI:
        monkeypatch.setenv("ENVIRONMENT", environment)
        get_settings.cache_clear()
        return create_app()

    yield build
    get_settings.cache_clear()


def test_production_publishes_the_schema_to_anybody(app_in: Callable[[str], FastAPI]) -> None:
    client = TestClient(app_in("production"), base_url="https://testserver")
    # No cookie, no Authorization header: the reference is for somebody who
    # has no account yet.
    response = client.get(SCHEMA, headers={"Origin": TEST_ORIGIN})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    document = response.json()
    assert "/api/v1/agent/token" in document["paths"]
    assert len(document["paths"]) > 100
    assert document["servers"] == [{"url": PUBLIC_API_URL}]
    # The description is where a reader learns what the extensions mean and
    # where an agent starts; without these it explains nothing that matters.
    assert "https://myntos.no/llms.txt" in document["info"]["description"]
    assert KEY_SCOPE_EXTENSION in document["info"]["description"]
    # Through the same middleware as every other answer.
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN
    assert response.headers["x-content-type-options"] == "nosniff"


def test_production_serves_no_interactive_docs(app_in: Callable[[str], FastAPI]) -> None:
    client = TestClient(app_in("production"), base_url="https://testserver")
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404, path


def test_the_docs_page_reads_the_published_schema(app_in: Callable[[str], FastAPI]) -> None:
    """Outside production the page is there, and finds the document where it now lives.

    And the document names no server, so «Try it out» on a development
    machine is sent to that machine and not to production.
    """
    client = TestClient(app_in("development"))
    page = client.get("/docs")
    assert page.status_code == 200
    assert SCHEMA in page.text
    assert "servers" not in client.get(SCHEMA).json()


def test_production_publishes_the_document_the_surface_test_checks(
    app_in: Callable[[str], FastAPI],
) -> None:
    """Every operation's key scope and description are held by test_agent_surface.py.

    That test builds the application with the test settings. If a route ever
    depended on the environment, it would be checking a document nobody is
    served, and passing.
    """
    published = TestClient(app_in("production"), base_url="https://testserver").get(SCHEMA)
    checked = app_in("test").openapi()
    assert published.json()["paths"] == checked["paths"]

"""CORS headers must survive every response, including the ones we regret.

A browser applies the CORS check before it lets the page see a response at all,
so an error answered without ``Access-Control-Allow-Origin`` does not reach the
frontend as a 500 — it reaches it as "blocked by CORS policy", which points the
next person at the allow-list rather than at the actual fault. The failure paths
matter here more than the happy one; the happy one was never in doubt.
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import TEST_ORIGIN

pytestmark = pytest.mark.usefixtures("test_settings")


@pytest.fixture
def exploding_client(client: TestClient) -> TestClient:
    """The real application, plus one route that fails in an unforeseen way.

    Not a domain error and not an ``HTTPException``: those are registered with
    handlers and answered from inside ``CORSMiddleware`` already. The gap is the
    exception nobody anticipated, which is by definition the one no endpoint can
    be relied on to raise on request.
    """
    app: FastAPI = client.app  # type: ignore[assignment]

    @app.get("/api/v1/_test_boom")
    async def boom() -> None:
        raise RuntimeError("database connection lost")

    return client


def test_successful_response_is_readable_cross_origin(client: TestClient) -> None:
    response = client.get("/api/v1/health", headers={"Origin": TEST_ORIGIN})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


def test_rejected_response_is_readable_cross_origin(client: TestClient) -> None:
    """A 401 the frontend cannot read is a login loop it cannot explain."""
    response = client.get("/api/v1/companies", headers={"Origin": TEST_ORIGIN})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN


def test_unhandled_error_is_readable_cross_origin(exploding_client: TestClient) -> None:
    response = exploding_client.get("/api/v1/_test_boom", headers={"Origin": TEST_ORIGIN})
    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == TEST_ORIGIN
    # Same {detail, code} shape as every other error, so the frontend needs no
    # special case for the one response it is least able to anticipate.
    assert response.json()["code"] == "internal_error"


def test_unhandled_error_does_not_leak_the_exception(exploding_client: TestClient) -> None:
    """The traceback goes to the logs; the caller is told only that it was ours."""
    response = exploding_client.get("/api/v1/_test_boom", headers={"Origin": TEST_ORIGIN})
    assert "database connection lost" not in response.text
    assert "RuntimeError" not in response.text


def test_unknown_origin_is_still_refused(exploding_client: TestClient) -> None:
    """Answering errors cross-origin must not have widened the allow-list."""
    for path in ("/api/v1/health", "/api/v1/_test_boom"):
        response = exploding_client.get(path, headers={"Origin": "https://angriper.example"})
        assert "access-control-allow-origin" not in response.headers, path

"""The ceiling on request bodies, which has to hold before anyone is known.

FastAPI parses a body before it resolves who sent it, so without this anybody
could grow the one process that serves every company by posting to any route.
"""

from collections.abc import Iterator

from fastapi.testclient import TestClient

from tests.conftest import TEST_ORIGIN

MIB = 1024 * 1024


def _chunks(size: int) -> Iterator[bytes]:
    sent = 0
    while sent < size:
        piece = min(MIB, size - sent)
        sent += piece
        yield b" " * piece


def test_a_json_body_over_the_limit_is_refused_before_it_is_read(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        content=b"[" + b"{}," * MIB + b"{}]",
        headers={"Content-Type": "application/json", "Origin": TEST_ORIGIN},
    )
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"


def test_a_body_without_a_length_is_cut_off_at_the_limit(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/login",
        content=_chunks(3 * MIB),
        headers={"Content-Type": "application/json", "Origin": TEST_ORIGIN},
    )
    assert response.status_code == 413


def test_an_upload_has_room_for_a_few_bilag_but_not_more(client: TestClient) -> None:
    boundary = "grense"
    head = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="a.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Origin": TEST_ORIGIN}

    # Unauthenticated, so a body that is let through is answered 401, not 413.
    within = client.post(
        "/api/v1/companies/1/cashflow/forslag",
        content=head + b"%PDF-" + b"0" * (30 * MIB) + tail,
        headers=headers,
    )
    over = client.post(
        "/api/v1/companies/1/cashflow/forslag",
        content=head + b"%PDF-" + b"0" * (65 * MIB) + tail,
        headers=headers,
    )
    saft = client.post(
        "/api/v1/companies/1/saft/innlesing",
        content=head + b"0" * (65 * MIB) + tail,
        headers=headers,
    )
    # A year of Tripletex's printout is as large as a year's SAF-T file.
    printout = client.post(
        "/api/v1/companies/1/saft/vedlegg",
        content=head + b"%PDF-" + b"0" * (65 * MIB) + tail,
        headers=headers,
    )

    assert within.status_code == 401
    assert over.status_code == 413
    assert saft.status_code == 401
    assert printout.status_code == 401


def test_an_ordinary_request_is_untouched(client: TestClient) -> None:
    assert client.get("/api/v1/health").status_code == 200

"""Photographed bilag: what is accepted, what is kept, and what is derived.

Three promises, tested from the outside in:

- a format is recognised by its bytes, never by its name or declared type;
- the archived original is the bytes that were uploaded, unchanged, location
  and all;
- a thumbnail is a copy made from pixels only, it carries nothing a phone
  wrote into the original, and it is exactly as private as the receipt it shows.

The images are built here with Pillow rather than kept as fixtures, so each one
is as small as the assertion needs. HEIC is the exception: Pillow cannot write
it, and the encoder that can (x265) is GPL and is deliberately not a dependency.
So it is a 668-byte file inlined below, 64x48 red, made once with pillow-heif,
carrying an EXIF block with a GPS position in Oslo.
"""

from __future__ import annotations

import base64
import hashlib
import io
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from myntos.api.v1 import cashflow
from myntos.domain.bilag import derived
from myntos.domain.bilag import service as bilag_service
from myntos.domain.bilag.errors import UnsupportedDocumentError
from myntos.domain.bilag.models import ACCEPTED_CONTENT_TYPES, PDF_CONTENT_TYPE
from myntos.storage.keys import (
    UnknownStorageKeyError,
    new_key,
    thumbnail_key,
    validate_derived_key,
    validate_key,
)
from myntos.storage.local import LocalDocumentStorage
from myntos.storage.r2 import R2DocumentStorage
from tests.conftest import register, write
from tests.test_cashflow_api import OTHER_ORGNR, create_company, record, signed_in_company

HEIC_WITH_GPS = base64.b64decode(
    "AAAAHGZ0eXBoZWljAAAAAG1pZjFoZWljbWlhZgAAAchtZXRhAAAAAAAAACFoZGxyAAAAAAAAAABwaWN0AAAAAAAA"
    "AAAAAAAAAAAAADRpbG9jAAAAAERAAAIAAQAAAAAB7AABAAAAAAAAABoAAgAAAAACBgABAAAAAAAAAJYAAAA4aWlu"
    "ZgAAAAAAAgAAABVpbmZlAgAAAAABAABodmMxAAAAABVpbmZlAgAAAQACAABFeGlmAAAAAA5waXRtAAAAAAABAAAB"
    "B2lwcnAAAADmaXBjbwAAAHZodmNDAQNwAAAAAAAAAAAAHvAA/P34+AAADwNgAAEAGEABDAH//wNwAAADAJAAAAMA"
    "AAMAHroCQGEAAQAqQgEBA3AAAAMAkAAAAwAAAwAeoCCBBZbq5Ka5uAhoMCAAAAMDIAAAAwAhYgABAAZEAcFzwIkA"
    "AAATY29scm5jbHgAAQANAAaAAAAAFGlzcGUAAAAAAAAAQAAAAEAAAAAoY2xhcAAAAEAAAAABAAAAMAAAAAEAAAAA"
    "AAAAAv////AAAAACAAAAEHBpeGkAAAAAAwgICAAAAAlpcm90AwAAABlpcG1hAAAAAAAAAAEAAQaBAgMFhIYAAAAa"
    "aXJlZgAAAAAAAAAOY2RzYwACAAEAAQAAALhtZGF0AAAAFigBrxmA70mlv//f4x/19q/4prWmPm4AAAAGRXhpZgAA"
    "TU0AKgAAAAgAAgESAAMAAAABAAYAAIglAAQAAAABAAAAJgAAAAAABAABAAIAAAACTgAAAAACAAUAAAADAAAAXAAD"
    "AAIAAAACRQAAAAAEAAUAAAADAAAAdAAAAAAAAAA7AAAAAQAAADYAAAABAAAAKQAAAAEAAAAKAAAAAQAAAC0AAAAB"
    "AAAAAAAAAAE="
)

GPS_IFD = 0x8825
ORIENTATION = 0x0112
# Oslo, to the second of arc: about thirty metres. What a phone writes.
OSLO = {1: "N", 2: (59.0, 54.0, 41.0), 3: "E", 4: (10.0, 45.0, 0.0)}


def picture(format: str, *, size: tuple[int, int] = (40, 20), **save: Any) -> bytes:
    mode = "RGBA" if format in {"PNG", "WEBP"} else "RGB"
    image = Image.new(mode, size, (30, 120, 200, 255) if mode == "RGBA" else (30, 120, 200))
    out = io.BytesIO()
    image.save(out, format=format, **save)
    return out.getvalue()


def phone_photo() -> bytes:
    """A JPEG as a phone writes it: landscape pixels, told to stand upright, and located.

    40 wide and 20 tall in the file, with Orientation 6 ("rotate 90° clockwise to
    display"), so the picture a person sees is 20 wide and 40 tall. A copy that
    drops the tag without turning the pixels comes out lying on its side.
    """
    exif = Image.Exif()
    exif[ORIENTATION] = 6
    exif[GPS_IFD] = OSLO
    return picture("JPEG", exif=exif.tobytes())


def ftyp(major: bytes, *compatible: bytes) -> bytes:
    """An ISO media header and nothing else: enough to sniff, not to decode."""
    body = b"ftyp" + major + b"\x00\x00\x00\x00" + b"".join(compatible)
    return (len(body) + 4).to_bytes(4, "big") + body + b"\x00" * 16


def has_no_metadata(jpeg: bytes) -> None:
    image = Image.open(io.BytesIO(jpeg))
    assert image.format == "JPEG"
    assert len(image.getexif()) == 0
    assert image.getexif().get_ifd(GPS_IFD) == {}
    # Belt and braces: no EXIF or XMP segment anywhere in the file, whatever
    # Pillow chooses to parse.
    assert b"Exif\x00\x00" not in jpeg
    assert b"http://ns.adobe.com/xap" not in jpeg
    assert "icc_profile" not in image.info


# --- Accepted on the bytes --------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"%PDF-1.7\n%%EOF\n", "application/pdf"),
        (picture("PNG"), "image/png"),
        (picture("JPEG"), "image/jpeg"),
        (picture("GIF"), "image/gif"),
        (picture("WEBP"), "image/webp"),
        (HEIC_WITH_GPS, "image/heic"),
        # Major brand "mif1" says only "an image of some kind"; a compatible
        # "heic" says which.
        (ftyp(b"mif1", b"mif1", b"heic"), "image/heic"),
        (ftyp(b"heix", b"mif1"), "image/heic"),
    ],
)
def test_each_format_is_recognised_by_its_bytes(content: bytes, expected: str) -> None:
    assert bilag_service.validate_document(content) == expected


@pytest.mark.parametrize(
    "content",
    [
        # AVIF is the same container with a different codec, and Løynd reads it
        # no better than HEIC. Not taken until somebody needs it.
        ftyp(b"avif", b"mif1", b"miaf"),
        ftyp(b"mif1", b"avif", b"miaf"),
        # An MP4 video is an ISO media file too.
        ftyp(b"isom", b"iso2", b"mp41"),
        # A brand list that claims to run on past the header is not read past it.
        b"\xff\xff\xff\xffftypmif1" + b"\x00" * 200 + b"heic",
        b"ftypheic",
    ],
)
def test_a_container_that_is_not_a_photograph_is_refused(content: bytes) -> None:
    with pytest.raises(UnsupportedDocumentError):
        bilag_service.validate_document(content)


def test_a_photograph_may_not_be_passed_off_as_a_pdf() -> None:
    """The narrowing check still holds with the new format in the list."""
    with pytest.raises(UnsupportedDocumentError):
        bilag_service.validate_pdf(HEIC_WITH_GPS)


def test_every_image_the_archive_takes_can_have_a_thumbnail() -> None:
    """``derived`` spells its list out to stay cheap to import; this keeps it honest."""
    assert ACCEPTED_CONTENT_TYPES - {PDF_CONTENT_TYPE} == derived.THUMBNAIL_SOURCES


# --- Derived copies ---------------------------------------------------------------


async def test_a_thumbnail_is_small_upright_and_says_nothing_about_where() -> None:
    thumb = await derived.thumbnail(phone_photo(), "image/jpeg")

    has_no_metadata(thumb)
    width, height = Image.open(io.BytesIO(thumb)).size
    # The orientation was applied to the pixels before the tag was dropped.
    assert (width, height) == (20, 40)


async def test_a_large_photo_is_scaled_to_the_thumbnail_edge() -> None:
    thumb = await derived.thumbnail(picture("PNG", size=(1200, 300)), "image/png")
    assert Image.open(io.BytesIO(thumb)).size == (derived.THUMBNAIL_EDGE, 48)


async def test_a_heic_thumbnail_carries_no_gps() -> None:
    # The fixture really does carry a position, or the assertion below is empty.
    import pi_heif  # type: ignore[import-untyped]

    pi_heif.register_heif_opener()
    assert Image.open(io.BytesIO(HEIC_WITH_GPS)).getexif().get_ifd(GPS_IFD) == OSLO

    thumb = await derived.thumbnail(HEIC_WITH_GPS, "image/heic")
    has_no_metadata(thumb)
    red = Image.open(io.BytesIO(thumb)).convert("RGB").getpixel((5, 5))
    assert isinstance(red, tuple)
    assert red[0] > 150  # still the picture


async def test_a_pdf_gets_no_thumbnail() -> None:
    """No native PDF renderer: see the module docstring of ``derived``."""
    with pytest.raises(derived.DerivationError):
        await derived.thumbnail(b"%PDF-1.7\n%%EOF\n", "application/pdf")


async def test_bytes_that_only_look_like_an_image_give_no_thumbnail() -> None:
    """A header that sniffs right and decodes to nothing is a missing badge, not a 500."""
    with pytest.raises(derived.DerivationError):
        await derived.thumbnail(b"\xff\xd8\xff\xe0" + b"\x00" * 64, "image/jpeg")


async def test_a_decompression_bomb_is_refused_before_it_is_decoded() -> None:
    """A PNG that claims 10 000 by 10 000 pixels is refused from its header."""
    bomb = picture("PNG", size=(10_000, 10_000), compress_level=9)
    assert len(bomb) < 1_000_000  # small on the wire, which is the trick
    with pytest.raises(derived.DerivationError):
        await derived.thumbnail(bomb, "image/png")


async def test_heic_is_read_through_a_stripped_jpeg_copy() -> None:
    """Løynd does not open HEIC, so it gets a JPEG, renamed so its extension agrees."""
    readable = await cashflow._as_loynd_reads(
        [(HEIC_WITH_GPS, "IMG_0042.HEIC", "image/heic"), (b"%PDF-1.7", "a.pdf", "application/pdf")]
    )

    copy, filename, content_type = readable[0]
    assert (filename, content_type) == ("IMG_0042.jpg", "image/jpeg")
    has_no_metadata(copy)
    # Everything Løynd already reads goes through untouched.
    assert readable[1] == (b"%PDF-1.7", "a.pdf", "application/pdf")


# --- Storage keys -----------------------------------------------------------------


def test_a_thumbnail_key_follows_from_the_original_and_cannot_be_one() -> None:
    original = new_key("image/heic")
    assert original.endswith(".heic")
    derived_key = thumbnail_key(original)

    assert derived_key == "derived/thumbnail/" + original.removesuffix(".heic") + ".jpg"
    assert validate_key(derived_key) == derived_key
    assert validate_derived_key(derived_key) == derived_key
    with pytest.raises(UnknownStorageKeyError):
        validate_derived_key(original)


async def test_the_overwriting_write_cannot_reach_an_original(tmp_path: Path) -> None:
    storage = LocalDocumentStorage(tmp_path)
    original = await storage.save(
        content=b"%PDF-1.7", filename="a.pdf", content_type="application/pdf"
    )
    with pytest.raises(UnknownStorageKeyError):
        await storage.save_derived(original, content=b"x", content_type="image/jpeg")
    assert await storage.load(original) == b"%PDF-1.7"

    key = thumbnail_key(original)
    await storage.save_derived(key, content=b"first", content_type="image/jpeg")
    await storage.save_derived(key, content=b"again", content_type="image/jpeg")
    assert await storage.load(key) == b"again"


async def test_r2_refuses_to_overwrite_an_original_without_asking_r2() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should have been made")

    storage = R2DocumentStorage(
        access_key_id="AKID",
        secret_access_key="secret",
        bucket="myntos-bilag",
        endpoint_url="https://acct.eu.r2.cloudflarestorage.com",
        client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)),
    )
    with pytest.raises(UnknownStorageKeyError):
        await storage.save_derived(
            "2026/" + "0" * 32 + ".jpg", content=b"x", content_type="image/jpeg"
        )


# --- Through the API --------------------------------------------------------------


def attach(client: TestClient, company_id: int, content: bytes, name: str) -> dict[str, Any]:
    entry = record(client, company_id, type_code="varekjop", gross_amount="100.00").json()
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company_id}/cashflow/{entry['id']}/dokument",
        # Declared as a PDF on purpose. The declaration is not evidence.
        files={"file": (name, content, "application/pdf")},
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def test_a_photo_is_archived_byte_for_byte_location_included(client: TestClient) -> None:
    """The original is the primary documentation. Nothing is stripped from it."""
    company = signed_in_company(client)
    photo = phone_photo()
    attachment = attach(client, company["id"], photo, "kvittering.jpg")

    assert attachment["content_type"] == "image/jpeg"
    assert attachment["sha256"] == hashlib.sha256(photo).hexdigest()

    original = client.get(f"/api/v1/companies/{company['id']}/dokument/{attachment['id']}")
    assert original.status_code == 200
    assert original.content == photo
    assert hashlib.sha256(original.content).hexdigest() == attachment["sha256"]
    # Still located: what the phone wrote is part of what was uploaded.
    assert Image.open(io.BytesIO(original.content)).getexif().get_ifd(GPS_IFD) != {}


def test_a_heic_is_archived_as_heic(client: TestClient) -> None:
    company = signed_in_company(client)
    attachment = attach(client, company["id"], HEIC_WITH_GPS, "IMG_0042.HEIC")

    assert attachment["content_type"] == "image/heic"
    assert attachment["filename"] == "IMG_0042.HEIC"
    original = client.get(f"/api/v1/companies/{company['id']}/dokument/{attachment['id']}")
    assert original.content == HEIC_WITH_GPS


def test_the_register_gets_a_thumbnail_and_keeps_it(client: TestClient, tmp_path: Path) -> None:
    company = signed_in_company(client)
    attachment = attach(client, company["id"], phone_photo(), "kvittering.jpg")
    url = f"/api/v1/companies/{company['id']}/dokument/{attachment['id']}/miniatyr"

    first = client.get(url)
    assert first.status_code == 200, first.text
    assert first.headers["content-type"] == "image/jpeg"
    assert first.headers["cache-control"].startswith("private")
    has_no_metadata(first.content)

    # Stored under the derived prefix, apart from the archive, and served from
    # there the second time.
    stored = list((tmp_path / "bilag" / "derived" / "thumbnail").rglob("*.jpg"))
    assert len(stored) == 1
    assert client.get(url).content == first.content


def test_a_pdf_has_no_thumbnail_and_says_so_with_a_404(client: TestClient) -> None:
    company = signed_in_company(client)
    attachment = attach(client, company["id"], b"%PDF-1.7\n%%EOF\n", "faktura.pdf")

    response = client.get(f"/api/v1/companies/{company['id']}/dokument/{attachment['id']}/miniatyr")
    assert response.status_code == 404


def test_a_thumbnail_is_not_reachable_from_a_sibling_company(client: TestClient) -> None:
    """Two companies on one account both pass the membership check. The row must not."""
    session = register(client)
    organization_id = session["memberships"][0]["organization_id"]
    first = create_company(client, organization_id)
    second = create_company(client, organization_id, orgnr=OTHER_ORGNR, name="Andre AS")
    attachment = attach(client, first["id"], phone_photo(), "kvittering.jpg")

    response = client.get(f"/api/v1/companies/{second['id']}/dokument/{attachment['id']}/miniatyr")
    assert response.status_code == 404
    own = client.get(f"/api/v1/companies/{first['id']}/dokument/{attachment['id']}/miniatyr")
    assert own.status_code == 200


def test_a_thumbnail_is_not_reachable_from_another_account(client: TestClient) -> None:
    owner = register(client, "kari@example.no")
    company = create_company(client, owner["memberships"][0]["organization_id"])
    attachment = attach(client, company["id"], phone_photo(), "kvittering.jpg")
    url = f"/api/v1/companies/{company['id']}/dokument/{attachment['id']}/miniatyr"

    write(client, "POST", "/api/v1/auth/logout")
    assert client.get(url).status_code == 401
    register(client, "ola@example.no", full_name="Ola Nordmann")
    assert client.get(url).status_code == 404

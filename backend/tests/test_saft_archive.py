"""What in an export is kept as a document, and what is named instead of kept.

The ZIPs are made here. Fiken's own layout is unpublished, so what is tested is
what any ZIP may hold: names in the wrong encoding, a ``..``, what a Mac or
Windows leaves behind, a file locked with a password, one too large.
"""

from __future__ import annotations

import zipfile
from io import BytesIO

import pytest

from myntos.saft import archive


def _written(*members: tuple[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as made:
        for name, content in members:
            made.writestr(name, content)
    return buffer.getvalue()


def _zip(*members: tuple[str, bytes]) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(_written(*members)))


def _paths(found: list[archive.Entry]) -> dict[str, str | None]:
    return {entry.path: entry.skip.value if entry.skip else None for entry in found}


def test_a_name_written_as_utf8_without_saying_so_is_read_as_utf8() -> None:
    # What a program that writes UTF-8 without the flag leaves. Python sets the
    # flag on any name it writes, so the name goes in as ASCII of the same
    # length and its bytes are swapped afterwards; reading it, Python takes
    # them as code page 437, the ZIP format's default.
    written = _written(("Bilag/Kvittering BXXndernes.pdf", b"%PDF-1.7"))
    written = written.replace(b"BXXndernes", "Bøndernes".encode())
    made = zipfile.ZipFile(BytesIO(written))
    assert made.infolist()[0].filename == "Bilag/Kvittering B├╕ndernes.pdf"
    assert archive.path_of(made.infolist()[0]) == "Bilag/Kvittering Bøndernes.pdf"
    flagged = _zip(("Bilag/Æ og Å.pdf", b"%PDF-1.7"))
    assert archive.path_of(flagged.infolist()[0]) == "Bilag/Æ og Å.pdf"


def test_a_path_is_text_to_show_never_a_way_out_of_a_folder() -> None:
    info = zipfile.ZipInfo("../../etc/./passwd")
    assert archive.path_of(info) == "etc/passwd"
    assert archive.path_of(zipfile.ZipInfo("Bilag\\2025\\k.pdf")) == "Bilag/2025/k.pdf"
    assert archive.path_of(zipfile.ZipInfo("Bilag/linje\nskift.pdf")) == "Bilag/linjeskift.pdf"


def test_what_the_operating_system_leaves_is_not_a_document() -> None:
    made = _zip(
        ("Bilag/", b""),
        ("Bilag/k.pdf", b"%PDF-1.7"),
        ("__MACOSX/Bilag/._k.pdf", b"\x00"),
        ("Bilag/._k.pdf", b"\x00"),
        ("Bilag/.DS_Store", b"\x00"),
        ("Bilag/Thumbs.db", b"\x00"),
        ("desktop.ini", b"\x00"),
    )
    assert _paths(archive.entries(made)) == {"Bilag/k.pdf": None}


def test_a_locked_file_and_one_too_large_are_named_not_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(archive, "MAX_DOCUMENT_BYTES", 10)
    made = _zip(
        ("Bilag/låst.pdf", b"%PDF"),
        ("Bilag/stor.pdf", b"%PDF-1.7 " + b"x" * 20),
        ("Bilag/liten.pdf", b"%PDF-1.7"),
    )
    # Python writes no encrypted ZIP; the flag is what is read, so it is set
    # on what was read.
    made.getinfo("Bilag/låst.pdf").flag_bits |= 0x1
    found = archive.entries(made)
    assert _paths(found) == {
        "Bilag/låst.pdf": "encrypted",
        "Bilag/stor.pdf": "too_large",
        "Bilag/liten.pdf": None,
    }
    review = archive.review("eksport.zip", found)
    assert review["documents"] == 1
    assert review["skipped_count"] == 2
    assert {item["path"] for item in review["skipped"]} == {"Bilag/låst.pdf", "Bilag/stor.pdf"}


def test_past_the_most_an_export_holds_the_rest_is_named(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive, "MAX_DOCUMENTS", 2)
    made = _zip(*((f"Bilag/{number}.pdf", b"%PDF-1.7") for number in range(4)))
    assert list(_paths(archive.entries(made)).values()) == [None, None, "too_many", "too_many"]


def test_folders_are_counted_inside_the_folder_that_wraps_them_all() -> None:
    wrapped = _zip(
        ("gard/Bilag/1.pdf", b"%PDF"),
        ("gard/Bilag/2.pdf", b"%PDF"),
        ("gard/Fakturaer/3.pdf", b"%PDF"),
    )
    review = archive.review("gard.zip", archive.entries(wrapped))
    assert review["folders"] == [
        {"name": "Bilag", "documents": 2},
        {"name": "Fakturaer", "documents": 1},
    ]
    loose = _zip(("Bilag/1.pdf", b"%PDF"), ("les-meg.txt", b"hei"))
    review = archive.review("gard.zip", archive.entries(loose))
    assert review["folders"] == [
        {"name": "Bilag", "documents": 1},
        {"name": "", "documents": 1},
    ]


def test_the_type_is_what_the_bytes_are_never_what_the_name_says() -> None:
    assert archive.content_type_of(b"%PDF-1.7") == "application/pdf"
    assert archive.content_type_of(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert archive.content_type_of(b"\xef\xbb\xbf<?xml ?><Invoice/>") == "application/xml"
    assert archive.content_type_of(b"PK\x03\x04") == "application/octet-stream"
    # An HTML page named kvittering.pdf is never served as a page: at most as
    # XML, which the download route hands over as an attachment.
    assert archive.content_type_of(b"<html><script>") == "application/xml"


def test_a_zip_with_only_its_saft_files_is_not_an_export() -> None:
    only = _zip(("2025.xml", b"<AuditFile/>"), ("__MACOSX/._2025.xml", b"\x00"))
    assert archive.has_documents(only, only.infolist()[:1]) is False
    more = _zip(("2025.xml", b"<AuditFile/>"), ("Bilag/1.pdf", b"%PDF"))
    assert archive.has_documents(more, more.infolist()[:1]) is True

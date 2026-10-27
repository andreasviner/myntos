"""What an upload becomes before an import reads it: files in the working store, and nothing else.

The routes that bring the old program's books over (``api/v1/saft_innlesing.py``)
and its printouts (``api/v1/saft_vedlegg.py``) hand their uploads here, and get
back the files as kept in :mod:`myntos.storage.working`, one per row of
``saft_import_file``.

**A SAF-T file is kept as its XML, gzip-compressed, whatever was uploaded.** A
plain XML is compressed as it is copied; a ZIP has its SAF-T files taken out
(:func:`myntos.saft.reader.saft_members`), each kept as a file of its own. That
is how Fiken's complete export comes over: one ZIP of 350 to 600 MB with a SAF-T
file per year beside every document in the books, which is what the first
person to try uploaded (2026-10-27) and what was refused for its size and would
have been refused for its contents.

**A ZIP with more than SAF-T files in it is also kept whole** (``FileKind.EXPORT``),
for the documents beside them: when the person says yes to the import, every
file in it is copied into the document store (:mod:`myntos.saft.archive`), and
the person no longer has to keep the export for five years themselves. Until
then it is a working copy like the rest, gone with the import if it is thrown
away or left for two weeks. Not for a comparison, which keeps nothing.

Gzip at level 1: the copy keeps up with the upload, SAF-T XML still shrinks
around tenfold at that level, and the reader streams it back without seeking
(:func:`myntos.saft.reader.read_entries_xml`).

**Where the upload is while this runs.** In Starlette's spool: memory up to a
megabyte, then a temporary file in ``/tmp``, which on the production host is
tmpfs under the unit's ``PrivateTmp``, so the bytes never reach a disk. It is
read a few megabytes at a time in a worker thread, and each piece goes to the
store as it is ready.

**A failure keeps nothing.** What was already stored for the request is deleted
before the refusal goes back. A client that leaves halfway cancels the request
instead, and the sweep deletes what it left (:func:`myntos.saft.jobs.sweep`).
"""

from __future__ import annotations

import asyncio
import logging
import zipfile
import zlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import IO

from starlette.datastructures import UploadFile

from myntos.saft import archive as export_archive
from myntos.saft import move, printout, reader
from myntos.saft.errors import InvalidPrintoutError, InvalidSaftFileError, SaftNotPrintoutError
from myntos.saft.models import FileKind, ImportPurpose
from myntos.storage.working import Stored, WorkingStore

logger = logging.getLogger(__name__)

#: The largest upload the import of several years takes, and so the largest
#: file. Fiken's complete export was 350 MB for one company and nearly 600 MB
#: for another; this is room for a company some years older. What bounds the
#: work is not this but the reader's ceilings on the XML (1 GB) and the voucher
#: lines (300 000) per file, which the documents in such a ZIP do not count
#: towards. The route checks who is asking before it reads a byte of it.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024

#: Read, compressed and handed to the store this much at a time.
_STEP = 4 * 1024 * 1024

_ZIP = b"PK\x03\x04"


@dataclass(frozen=True)
class KeptFile:
    #: The name the person knows it by: the upload's, or the SAF-T file's inside a ZIP.
    filename: str
    key: str
    #: The file's size as the person knows it: the upload's, or the XML's inside a ZIP.
    size: int
    #: A file the import reads, or an export kept whole for its documents.
    kind: FileKind = FileKind.READ


async def keep_saft_files(
    store: WorkingStore, purpose: ImportPurpose, uploads: Sequence[UploadFile]
) -> list[KeptFile]:
    """Every SAF-T file in ``uploads``, stored as gzip-compressed XML."""
    if not uploads:
        raise InvalidSaftFileError("velg SAF-T-filene fra det gamle programmet først")
    for upload in uploads:
        _check_size(upload, MAX_UPLOAD_BYTES, InvalidSaftFileError)
    kept: list[KeptFile] = []
    try:
        for upload in uploads:
            await _keep_saft(store, purpose, upload, kept)
        _check_count(purpose, _read(kept))
    except Exception:
        await forget(store, [file.key for file in kept])
        raise
    return kept


async def keep_printouts(store: WorkingStore, uploads: Sequence[UploadFile]) -> list[KeptFile]:
    """The printouts as they came, PDFs for pypdf to read whole later."""
    if not uploads:
        raise InvalidPrintoutError("velg utskriften av bilagene først")
    if len(uploads) > printout.MAX_FILES:
        raise InvalidPrintoutError(
            f"Myntos leser høyst {printout.MAX_FILES} utskrifter i samme opplasting. Last opp "
            "resten etterpå"
        )
    for upload in uploads:
        _check_size(upload, printout.MAX_UPLOAD_BYTES, InvalidPrintoutError)
        await asyncio.to_thread(_refuse_unless_pdf, upload.file, upload.filename or "Filen")
    kept: list[KeptFile] = []
    try:
        for index, upload in enumerate(uploads):
            stored = await store.save(_pieces(upload.file))
            kept.append(
                KeptFile(
                    filename=upload.filename or f"utskrift-{index + 1}.pdf",
                    key=stored.key,
                    size=stored.size,
                )
            )
    except Exception:
        await forget(store, [file.key for file in kept])
        raise
    return kept


async def forget(store: WorkingStore, keys: Sequence[str]) -> None:
    """Delete ``keys`` from the store, as far as it goes: the sweep takes what is left."""
    for key in keys:
        try:
            await store.delete(key)
        except Exception:
            logger.warning("Could not delete working copy %s; the sweep will", key, exc_info=True)


def _check_size(
    upload: UploadFile, limit: int, error: type[InvalidSaftFileError | InvalidPrintoutError]
) -> None:
    name = upload.filename or "filen"
    if upload.size is not None and upload.size > limit:
        if error is InvalidPrintoutError:
            raise error(
                f"«{name}» er større enn {_size(limit)}. Skriv ut en kortere periode om gangen"
            )
        raise error(f"«{name}» er større enn {_size(limit)}, det største Myntos tar imot")
    if upload.size == 0:
        raise error(f"«{name}» er tom")


def _size(limit: int) -> str:
    gigabytes, rest = divmod(limit, 1024**3)
    return f"{gigabytes} GB" if gigabytes and not rest else f"{limit // 1024**2} MB"


def _read(kept: Sequence[KeptFile]) -> int:
    """How many of ``kept`` the import reads: the SAF-T files, not an export kept whole."""
    return sum(1 for file in kept if file.kind is FileKind.READ)


def _check_count(purpose: ImportPurpose, count: int) -> None:
    if purpose is ImportPurpose.SAMMENLIGNING and count != 1:
        raise InvalidSaftFileError(
            "sammenligningen leser bare ett år: SAF-T-filen for året det gamle programmet "
            f"fører nå. Her er det {count}"
        )
    if count > move.MAX_FILES:
        raise InvalidSaftFileError(
            f"Myntos leser høyst {move.MAX_FILES} SAF-T-filer, en for hvert år"
        )


async def _keep_saft(
    store: WorkingStore, purpose: ImportPurpose, upload: UploadFile, kept: list[KeptFile]
) -> None:
    """Store the SAF-T files of one upload, appending each to ``kept`` as it is stored."""
    name = upload.filename or "saft.xml"
    if await asyncio.to_thread(_head, upload.file) != _ZIP:
        # Before anything is stored: a printout or a bank statement sent here
        # is told what it is (reader.refuse_unless_saft).
        await asyncio.to_thread(reader.refuse_unless_saft, upload.file, f"«{name}»")
        stored, size = await _gzipped_into(store, upload.file, name)
        kept.append(KeptFile(filename=name, key=stored.key, size=size))
        return
    try:
        archive = await asyncio.to_thread(zipfile.ZipFile, upload.file)
    except zipfile.BadZipFile as error:
        raise InvalidSaftFileError(
            f"«{name}» kan ikke åpnes som ZIP. Last den ned på nytt"
        ) from error
    members = await asyncio.to_thread(reader.saft_members, archive)
    if not members:
        reason = reader.without_saft(archive)
        if name.lower().startswith("fiken-"):
            # Fiken's own names: fiken-bilag-…, fiken-dokumenter-…, fiken-lønn-…
            # sit beside fiken-komplett-… on the page the person was on.
            reason += (
                ". Fra Fiken er det «Komplett eksport» og SAF-T-filen for hvert år som har "
                "regnskapet i seg"
            )
        raise InvalidSaftFileError(f"«{name}» {reason}")
    # Counted before anything is unpacked: a ZIP of a thousand SAF-T files is
    # refused for being a thousand, not after the first ten are stored.
    _check_count(purpose, _read(kept) + len(members))
    for info in members:
        member_name = info.filename.rsplit("/", 1)[-1]
        try:
            member = await asyncio.to_thread(archive.open, info)
            with member:
                stored, size = await _gzipped_into(store, member, member_name)
        except (zipfile.BadZipFile, EOFError, zlib.error) as error:
            raise InvalidSaftFileError(
                f"«{member_name}» i «{name}» kan ikke pakkes ut. Last ZIP-filen ned på nytt"
            ) from error
        kept.append(KeptFile(filename=member_name, key=stored.key, size=size))
    if purpose is ImportPurpose.IMPORT and await asyncio.to_thread(
        export_archive.has_documents, archive, members
    ):
        # After the SAF-T files, so a ZIP refused for them is never copied.
        await asyncio.to_thread(upload.file.seek, 0)
        stored = await store.save(_pieces(upload.file))
        kept.append(KeptFile(filename=name, key=stored.key, size=stored.size, kind=FileKind.EXPORT))


def _head(source: IO[bytes]) -> bytes:
    head = source.read(4)
    source.seek(0)
    return head


def _refuse_unless_pdf(source: IO[bytes], name: str) -> None:
    """A printout is a PDF. A SAF-T file sent here is told where it goes, with a code of its own."""
    head = _head(source)
    if head == b"%PDF":
        return
    element = None if head == _ZIP else reader.document_element(source)
    source.seek(0)
    if head == _ZIP or (element is not None and element[1] == "AuditFile"):
        raise SaftNotPrintoutError(
            f"«{name}» er en SAF-T-fil eller en ZIP, ikke en utskrift av bilag. SAF-T-filene "
            "lastes opp under «Last opp SAF-T-filene»"
        )
    raise InvalidPrintoutError(f"«{name}» er ikke en PDF. Last opp utskriften av bilagene som PDF")


async def _gzipped_into(store: WorkingStore, source: IO[bytes], name: str) -> tuple[Stored, int]:
    """Copy ``source`` into the store gzip-compressed: what was stored, and the XML's size."""
    read = 0

    async def pieces() -> AsyncIterator[bytes]:
        nonlocal read
        compressor = zlib.compressobj(1, zlib.DEFLATED, 31)
        while raw := await asyncio.to_thread(source.read, _STEP):
            read += len(raw)
            if read > reader.MAX_DOCUMENT_BYTES:
                # The reader would stop here too, after the copy; a ZIP's
                # member can unpack to far more than its upload.
                raise InvalidSaftFileError(
                    f"«{name}» er mer enn 1 GB XML, og så mye leser ikke Myntos fra en "
                    "SAF-T-fil. Ta ut SAF-T-filen for en kortere periode"
                )
            if piece := await asyncio.to_thread(compressor.compress, raw):
                yield piece
        yield compressor.flush()

    stored = await store.save(pieces())
    return stored, read


async def _pieces(source: IO[bytes]) -> AsyncIterator[bytes]:
    while piece := await asyncio.to_thread(source.read, _STEP):
        yield piece

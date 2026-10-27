"""The documents in the old program's export, kept in Myntos with the books.

Fiken's complete export («Komplett eksport», ``fiken-komplett-<firma>.zip``,
350 to 600 MB for the first two companies to try it) holds a SAF-T file per year
and, beside them, every receipt and invoice behind the vouchers, the invoices
Fiken made, and a spreadsheet per year. Until 2026-10-27 only the SAF-T files
were taken out and the person was told to keep the rest themselves, for the
five years bokføringsloven § 13 asks. Now the whole export is kept as a working
copy with the import (:mod:`myntos.saft.intake`, ``FileKind.EXPORT``), and when
the person says yes to the import, every file in it is copied into the
document store (:class:`~myntos.saft.models.ImportedDocument`).

**On no voucher, by design.** Fiken publishes neither the folders of its export
nor the names of the files in them, and the bilag archive is append-only: a
receipt put on the wrong voucher is there for good, under a voucher an auditor
then believes it documents. So each file is kept under the path it had in the
export, findable and downloadable, which is what the person had before and no
less. Matching them to the vouchers brought over can come later, from the rows
here, once a real export has shown how Fiken names them; nothing needs
uploading again for that.

**What is not kept** is said, never dropped quietly: a file locked with a
password, one larger than :data:`MAX_DOCUMENT_BYTES`, those past
:data:`MAX_DOCUMENTS` or :data:`MAX_EXPORT_BYTES` (a ZIP that unpacks to more is
not an accounting export), and one that cannot be unpacked. What the operating
system leaves in a ZIP (``__MACOSX/``, ``.DS_Store``, ``Thumbs.db``) is not a
document and is left out without a word.

**Kept once.** A file already kept for the company at the same path with the
same contents is not kept again, so uploading the same export twice, or
posting again after a stop, adds nothing. A file at another path is kept again:
the path is what says which voucher it was behind.

Everything a ZIP says about itself (names, paths, sizes) came from whoever made
it: data to show, never a path on a disk, and never instructions to an agent
reading it back (docs/agents-and-api.md rule 7).
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import hashlib
import zipfile
import zlib
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, BinaryIO

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myntos.domain.bilag.models import sniff_content_type
from myntos.domain.bilag.service import sanitise_filename
from myntos.saft.errors import InvalidSaftFileError
from myntos.saft.models import ImportedDocument
from myntos.storage.base import DocumentStorage, StorageError
from myntos.storage.working import WorkingStore

#: The largest single file kept. A photographed receipt is a few megabytes and
#: a scanned invoice rarely twenty; each is held in memory once while it is
#: copied. A SAF-T file larger than this is still read for the books, and the
#: person is told to keep that one themselves.
MAX_DOCUMENT_BYTES = 100 * 1024 * 1024

#: The most files kept from one export. Ten years of a busy shop's receipts
#: is some tens of thousands.
MAX_DOCUMENTS = 50_000

#: The most kept from one export in all, unpacked. Receipts and PDFs do not
#: compress, so an export unpacks to about its own size, and the largest upload
#: is 2 GB: a ZIP that claims four times that is not an accounting export.
MAX_EXPORT_BYTES = 8 * 1024 * 1024 * 1024

#: How many of the files not kept the review names. The count is always there.
_LISTED = 50

#: Documents copied between commits: a commit per receipt is a round trip to
#: Frankfurt each. A restart in the middle loses at most these rows; their
#: files stay in the store unreferenced (the harmless direction, as
#: ``bilag_service.attach_document`` explains), and the next post keeps them
#: again. A store that stops answering loses none: the rows before it are
#: committed first.
_BATCH = 20

_STEP = 4 * 1024 * 1024

_JUNK_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})

#: Set when a ZIP's names are UTF-8 (APPNOTE 4.4.4, bit 11).
_UTF8_FLAG = 0x800


class Skip(enum.StrEnum):
    #: Locked with a password.
    ENCRYPTED = "encrypted"
    #: Larger than MAX_DOCUMENT_BYTES.
    TOO_LARGE = "too_large"
    #: Past MAX_DOCUMENTS or MAX_EXPORT_BYTES.
    TOO_MANY = "too_many"
    #: Could not be unpacked when it was copied.
    BROKEN = "broken"


@dataclass(frozen=True)
class Entry:
    info: zipfile.ZipInfo
    #: Where it is in the export, as the person sees it when they unpack it.
    path: str
    skip: Skip | None = None


def path_of(info: zipfile.ZipInfo) -> str:
    """The member's path as a person reads it: UTF-8, forward slashes, no ``..``.

    Without the UTF-8 flag Python reads a name as code page 437, which is what
    the ZIP format says; many programs write UTF-8 without setting it, and
    «Kvittering_Bøndernes.pdf» then reads as «Kvittering_B├╕ndernes.pdf».
    """
    name = info.filename
    if not info.flag_bits & _UTF8_FLAG:
        with contextlib.suppress(UnicodeError):
            name = name.encode("cp437").decode("utf-8")
    parts = [part for part in name.replace("\\", "/").split("/") if part not in ("", ".", "..")]
    return "".join(char for char in "/".join(parts) if char.isprintable())[:1024]


def _junk(info: zipfile.ZipInfo, path: str) -> bool:
    if info.is_dir() or not path:
        return True
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    return lower.startswith("__macosx/") or name in _JUNK_NAMES or name.startswith("._")


def entries(archive: zipfile.ZipFile) -> list[Entry]:
    """Every file in the export worth keeping, in its order, each saying whether it will be."""
    found: list[Entry] = []
    count = 0
    total = 0
    for info in archive.infolist():
        path = path_of(info)
        if _junk(info, path):
            continue
        skip: Skip | None = None
        if info.flag_bits & 0x1:
            skip = Skip.ENCRYPTED
        elif info.file_size > MAX_DOCUMENT_BYTES:
            skip = Skip.TOO_LARGE
        elif count >= MAX_DOCUMENTS or total + info.file_size > MAX_EXPORT_BYTES:
            skip = Skip.TOO_MANY
        else:
            count += 1
            total += info.file_size
        found.append(Entry(info=info, path=path, skip=skip))
    return found


def has_documents(archive: zipfile.ZipFile, saft: list[zipfile.ZipInfo]) -> bool:
    """Whether a ZIP holds anything beside its SAF-T files: then it is an export, kept whole."""
    taken = {info.filename for info in saft}
    return any(entry.info.filename not in taken for entry in entries(archive))


def _folders(kept: list[Entry]) -> list[dict[str, Any]]:
    """How many files each folder holds, for the review: «Bilag 1 204, Fakturaer 312».

    By the first folder in the path, or the second when every file is in the
    same first one (a ZIP that wraps everything in a folder named after the
    export says nothing by it). Only for showing; nothing is decided by it.
    """
    split = [entry.path.split("/") for entry in kept]
    depth = (
        1
        if len({parts[0] for parts in split if len(parts) > 1}) == 1
        and all(len(parts) > 1 for parts in split)
        else 0
    )
    counted = Counter(parts[depth] if len(parts) > depth + 1 else "" for parts in split)
    return [{"name": name, "documents": documents} for name, documents in counted.most_common(12)]


def review(file_name: str, found: list[Entry]) -> dict[str, Any]:
    """What keeping the export's documents will do, as stored with the import and answered."""
    kept = [entry for entry in found if entry.skip is None]
    skipped = [entry for entry in found if entry.skip is not None]
    return {
        "file_name": file_name,
        "documents": len(kept),
        "bytes": sum(entry.info.file_size for entry in kept),
        "folders": _folders(kept),
        "skipped": [
            {"path": entry.path, "reason": entry.skip.value}
            for entry in skipped[:_LISTED]
            if entry.skip is not None
        ],
        "skipped_count": len(skipped),
        # Filled in once the documents are kept (keep).
        "kept": None,
        "already_kept": None,
    }


def read_review(working: WorkingStore, key: str, file_name: str) -> dict[str, Any]:
    """The review of a stored export, from its ZIP directory alone. Blocking: a worker thread's."""
    with working.open_seekable(key) as raw:
        try:
            with zipfile.ZipFile(raw) as archive:
                return review(file_name, entries(archive))
        except zipfile.BadZipFile as error:
            raise InvalidSaftFileError(_unreadable(file_name)) from error


def _unreadable(file_name: str) -> str:
    return f"kopien av «{file_name}» hos Myntos kan ikke leses. Last den opp på nytt"


class _BrokenMemberError(Exception):
    pass


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """One member's bytes, never more than :data:`MAX_DOCUMENT_BYTES` whatever its header says."""
    pieces: list[bytes] = []
    read = 0
    try:
        with archive.open(info) as member:
            while piece := member.read(_STEP):
                read += len(piece)
                if read > MAX_DOCUMENT_BYTES:
                    raise _BrokenMemberError
                pieces.append(piece)
    except (zipfile.BadZipFile, EOFError, zlib.error, NotImplementedError, RuntimeError) as error:
        # NotImplementedError: a compression Python cannot unpack (Deflate64,
        # which Windows writes past 2 GB). RuntimeError: a password after all.
        raise _BrokenMemberError from error
    return b"".join(pieces)


def content_type_of(content: bytes) -> str:
    """The type the bytes are: a bilag's types by their header, XML by its start, else unknown.

    Never by the file's name: it is served back with this type, and the name
    came from the export.
    """
    sniffed = sniff_content_type(content)
    if sniffed is not None:
        return sniffed
    if content.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return "application/xml"
    return "application/octet-stream"


Progress = Callable[[int, int], Awaitable[None]]


async def keep(
    sessions: async_sessionmaker[AsyncSession],
    storage: DocumentStorage,
    working: WorkingStore,
    *,
    key: str,
    file_name: str,
    company_id: int,
    import_pk: int,
    software: str | None,
    user_id: int | None,
    progress: Progress,
) -> dict[str, Any]:
    """Copy every file of a stored export into the document store. Returns its review, filled in.

    A failure of the store stops it (``StorageError``) with what was kept so
    far committed; calling it again carries on, since what is there is not kept
    twice.
    """
    source: BinaryIO = await asyncio.to_thread(working.open_seekable, key)
    try:
        try:
            archive = await asyncio.to_thread(zipfile.ZipFile, source)
        except zipfile.BadZipFile as error:
            raise InvalidSaftFileError(_unreadable(file_name)) from error
        found = await asyncio.to_thread(entries, archive)
        result = review(file_name, found)
        to_keep = [entry for entry in found if entry.skip is None]
        broken: list[Entry] = []
        kept = already = 0
        async with sessions() as db:
            there = {
                (path, digest)
                for path, digest in (
                    await db.execute(
                        select(ImportedDocument.path, ImportedDocument.sha256).where(
                            ImportedDocument.company_id == company_id
                        )
                    )
                ).tuples()
            }
            for index, entry in enumerate(to_keep):
                if index % _BATCH == 0:
                    await db.commit()
                    await progress(index, len(to_keep))
                try:
                    content = await asyncio.to_thread(_read_member, archive, entry.info)
                except _BrokenMemberError:
                    broken.append(Entry(info=entry.info, path=entry.path, skip=Skip.BROKEN))
                    continue
                digest = hashlib.sha256(content).hexdigest()
                if (entry.path, digest) in there:
                    already += 1
                    continue
                content_type = content_type_of(content)
                filename = sanitise_filename(entry.path, content_type=content_type)
                try:
                    stored = await storage.save(
                        content=content, filename=filename, content_type=content_type
                    )
                except StorageError:
                    # What the store took before it stopped is recorded, so
                    # the next post does not store it a second time.
                    await db.commit()
                    raise
                db.add(
                    ImportedDocument(
                        company_id=company_id,
                        import_id=import_pk,
                        software=software,
                        export_name=file_name[:255],
                        path=entry.path,
                        filename=filename,
                        content_type=content_type,
                        byte_size=len(content),
                        sha256=digest,
                        storage_key=stored,
                        kept_by_user_id=user_id,
                    )
                )
                there.add((entry.path, digest))
                kept += 1
                del content
            await db.commit()
        await progress(len(to_keep), len(to_keep))
    finally:
        await asyncio.to_thread(source.close)
    if broken:
        listed = result["skipped"]
        room = max(0, _LISTED - len(listed))
        listed.extend({"path": entry.path, "reason": Skip.BROKEN.value} for entry in broken[:room])
        result["skipped_count"] += len(broken)
        result["documents"] -= len(broken)
        result["bytes"] -= sum(entry.info.file_size for entry in broken)
    result["kept"] = kept
    result["already_kept"] = already
    return result


__all__ = [
    "MAX_DOCUMENTS",
    "MAX_DOCUMENT_BYTES",
    "MAX_EXPORT_BYTES",
    "Entry",
    "Skip",
    "content_type_of",
    "entries",
    "has_documents",
    "keep",
    "path_of",
    "read_review",
    "review",
]

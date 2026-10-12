"""Copies made from a bilag: thumbnails, and the JPEG a HEIC photo is read as.

**A derived copy is not part of the record.** The archived original is the
primary documentation bokføringsloven § 13 asks us to keep, and it is stored
byte for byte as it arrived: nothing is stripped from it, nothing re-encoded,
and the SHA-256 on its row is of exactly those bytes. Everything this module
makes is a convenience derived from that original. It can be thrown away and
made again at any time, so it lives under its own key prefix
(:func:`myntos.storage.keys.thumbnail_key`) and has no row, no digest and no
retention promise of its own.

**A phone photograph says where it was taken.** A receipt photographed at the
kitchen table carries the GPS position of the kitchen table in its EXIF block,
to a few metres, together with the phone model and the time to the second. In
the original that is part of the document as it was produced, and changing it
would make the archived file something other than what was uploaded, so it
stays. A derived copy has no such claim on it: a thumbnail is painted in a
register that a regnskapsfører, a colleague or an auditor may be looking at,
and the photo read by Løynd goes to a model. Neither needs to know where
somebody lives. So every copy made here is built from pixels alone: the
orientation the EXIF asked for is applied to the pixels first (or a photo taken
in portrait comes out lying on its side), and then the image is redrawn onto a
fresh canvas that carries no EXIF, no XMP and no ICC block at all. Stripping a
list of known-bad tags would miss the one nobody thought of; copying nothing
cannot.

**The decoding happens in a child process.** Until now nothing in Myntos
decoded an image: the archive sniffs a header (:mod:`myntos.domain.bilag.models`)
and Løynd does the reading behind its own guards. A thumbnail cannot be made
without decoding, and image decoders are native code handed bytes from a
stranger, with a long history of exactly the bugs that implies. So each job runs
in a fresh interpreter (``python -I -m myntos.domain.bilag.derived``) with an
address-space ceiling and a timeout. A decoder that crashes takes the child down
and not the API; one that is made to allocate without end hits the ceiling; one
that loops is killed. Any of those is "no thumbnail", which the register shows as
the plain badge it showed before. Starting a process costs a few hundred
milliseconds, and a thumbnail is made once and then stored, so that is cheap.

**No PDF thumbnails.** Drawing a PDF's first page means a PDF renderer, and the
only practical ones are large native libraries (PDFium, MuPDF, Poppler).
``pyproject.toml`` already records the decision against that: the text layer is
read with pure-Python pypdf because "a native PDF parser is a much larger thing
to trust" with a user's bytes. A thumbnail is not worth reversing it for, so a
PDF keeps its badge.
"""

from __future__ import annotations

import asyncio
import io
import logging
import sys
import weakref
from typing import Final

# Nothing from myntos is imported at module level. The child process imports
# this module by name, and every import here is startup time paid on every
# thumbnail: the bilag models alone bring SQLAlchemy and the whole ledger.

logger = logging.getLogger(__name__)

#: The longest side of a thumbnail, in pixels. Twice what the register paints,
#: so it stays sharp on a high-density phone screen.
THUMBNAIL_EDGE: Final = 192
THUMBNAIL_CONTENT_TYPE: Final = "image/jpeg"

#: The longest side of the copy Løynd reads. Løynd scales anything larger down
#: to fit 2048 by 2048 itself (``LOYND_MAX_IMAGE_PIXELS``), so sending more is
#: bytes on the wire for nothing; sending less was measured on 2026-09-18 and
#: cost a counterparty name. Same ceiling, then, decided once on that side.
READING_EDGE: Final = 2048

#: Formats a thumbnail can be made from: everything the archive accepts except
#: PDF, for the reason in the module docstring. Spelled out rather than imported
#: (see the note on imports above); the test suite holds it to
#: ``ACCEPTED_CONTENT_TYPES``, so a format added there cannot be forgotten here.
THUMBNAIL_SOURCES: Final = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif", "image/heic"}
)

# A 48-megapixel phone sensor is the largest honest photograph of a receipt we
# expect. The limit is checked from the header, before a pixel is decoded,
# because a 25 MB PNG of one colour can claim to be 40 000 pixels square and
# would decode to gigabytes.
_MAX_PIXELS: Final = 64_000_000
# Room for the interpreter, Pillow, libheif and one 64-megapixel RGB frame with
# a working copy, and not much more. Past it the child fails to allocate and
# exits, which is the point.
_CHILD_ADDRESS_SPACE: Final = 2 * 1024 * 1024 * 1024
_CHILD_TIMEOUT_SECONDS: Final = 30.0
# A register page lists a hundred entries and asks for every thumbnail that has
# not been made yet at once. Without a ceiling that is a hundred interpreters.
_CHILD_CONCURRENCY: Final = 2
# One semaphore per event loop. A module-level one binds to the first loop that
# has to wait on it, and a second loop (a test, a worker restarted in-process)
# then gets a RuntimeError instead of a thumbnail.
_children: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore] = (
    weakref.WeakKeyDictionary()
)


def _limiter() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    limiter = _children.get(loop)
    if limiter is None:
        limiter = _children[loop] = asyncio.Semaphore(_CHILD_CONCURRENCY)
    return limiter


_KINDS: Final = ("thumbnail", "reading")


class DerivationError(RuntimeError):
    """No copy could be made. Never the user's problem: the original is intact."""


# --- In the child ------------------------------------------------------------


def _render(content: bytes, *, edge: int, quality: int) -> bytes:
    """Decode ``content`` and return a JPEG of at most ``edge`` pixels a side.

    Runs only in the child process; importing Pillow and registering the HEIC
    opener happen here so the API process never loads either.
    """
    import pi_heif  # type: ignore[import-untyped]
    from PIL import Image, ImageOps

    pi_heif.register_heif_opener()
    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS

    with Image.open(io.BytesIO(content)) as source:
        width, height = source.size
        if width * height > _MAX_PIXELS:
            raise DerivationError("bildet er for stort")
        # A JPEG can be decoded at a fraction of its size directly, which turns
        # a twelve-megapixel decode into a quarter-megapixel one.
        source.draft("RGB", (edge, edge))
        source.thumbnail((edge, edge))
        # Orientation before the metadata goes, or a portrait photo is drawn
        # lying down: the tag that said "rotate" is about to stop existing.
        upright = ImageOps.exif_transpose(source)
        with_alpha = upright.convert("RGBA")
        # A fresh canvas, not a copy: Image.new carries no info dict, so there is
        # no EXIF, XMP or ICC profile for the encoder to write, whatever the
        # source had. A transparent PNG lands on white rather than on black.
        clean = Image.new("RGB", with_alpha.size, (255, 255, 255))
        clean.paste(with_alpha, mask=with_alpha.getchannel("A"))

    out = io.BytesIO()
    clean.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


def render(kind: str, content: bytes) -> bytes:
    """The copy of one kind. Exposed for the child's entry point and the tests."""
    if kind == "thumbnail":
        return _render(content, edge=THUMBNAIL_EDGE, quality=80)
    if kind == "reading":
        return _render(content, edge=READING_EDGE, quality=90)
    raise ValueError(f"unknown kind {kind!r}")


def _main() -> int:
    """Read the original on stdin, write the copy on stdout."""
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (_CHILD_ADDRESS_SPACE, _CHILD_ADDRESS_SPACE))
    if len(sys.argv) != 2 or sys.argv[1] not in _KINDS:
        return 2
    try:
        copy = render(sys.argv[1], sys.stdin.buffer.read())
    except Exception as error:
        # The type only. A message from a decoder can quote the input.
        print(type(error).__name__, file=sys.stderr)
        return 1
    sys.stdout.buffer.write(copy)
    return 0


# --- In the API --------------------------------------------------------------


async def _isolated(kind: str, content: bytes) -> bytes:
    """Run :func:`render` in a child process and return what it wrote."""
    async with _limiter():
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            # -I: no user site-packages, no PYTHON* environment, and not the
            # current directory on the path. The child imports what the API
            # imports and nothing that happens to be lying around.
            "-I",
            "-m",
            "myntos.domain.bilag.derived",
            kind,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(content), timeout=_CHILD_TIMEOUT_SECONDS
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise DerivationError("tok for lang tid") from error
    if process.returncode != 0 or not stdout:
        logger.info(
            "No %s could be made (exit %s, %s)",
            kind,
            process.returncode,
            stderr.decode(errors="replace").strip()[:80],
        )
        raise DerivationError("kunne ikke lage en kopi av bildet")
    return stdout


async def thumbnail(content: bytes, content_type: str) -> bytes:
    """A small JPEG of an archived image, with no metadata at all.

    :class:`DerivationError` for a PDF, and for an image no decoder would open.
    """
    if content_type not in THUMBNAIL_SOURCES:
        raise DerivationError("ingen miniatyr for dette formatet")
    return await _isolated("thumbnail", content)


async def reading_copy(content: bytes) -> bytes:
    """A JPEG of a photograph, for a reader that cannot open the original.

    Used for HEIC (:data:`myntos.domain.bilag.models.READ_AS`). Stripped like a
    thumbnail, because a model has no more need of the GPS position than a
    register does.
    """
    return await _isolated("reading", content)


if __name__ == "__main__":
    raise SystemExit(_main())

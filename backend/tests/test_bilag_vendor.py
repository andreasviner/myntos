"""Asking the vision model who a screenshot is from.

The case these cover is one AliExpress purchase documented by nine screenshots.
Its transcription names two shops and never AliExpress, because an app draws its
name as a logo, and a logo does not survive being turned into text.
"""

from __future__ import annotations

import pytest

from myntos.domain.bilag import vendor


def test_a_name_comes_back_as_itself() -> None:
    assert vendor.clean("AliExpress") == "AliExpress"
    assert vendor.clean("  Eite Technology Store  ") == "Eite Technology Store"
    assert vendor.clean('"Komplett.no"') == "Komplett.no"


def test_the_model_is_allowed_to_decline() -> None:
    """A bank transaction with the merchant collapsed behind "Show more".

    Declining is the right answer there, and a field left empty is better than
    one filled with the account holder, which is us.
    """
    for reply in ("UKJENT", "ukjent", "Unknown", "vet ikke", "N/A", ""):
        assert vendor.clean(reply) is None


def test_a_sentence_is_not_a_name() -> None:
    assert vendor.clean("Jeg kan ikke se hvilket firma dette skjermbildet er fra, men") is None
    assert vendor.clean("---") is None
    assert vendor.clean("2026") is None


@pytest.mark.asyncio
async def test_the_first_image_that_names_somebody_wins() -> None:
    """In upload order, and no further questions once one is answered."""
    asked: list[bytes] = []

    async def look(content: bytes, *, content_type: str, question: str, max_tokens: int) -> str:
        asked.append(content)
        return {b"bank": "UKJENT", b"shop": "AliExpress"}[content]

    images = [(b"bank", "image/png"), (b"shop", "image/png")]
    assert await vendor.named_by(images, look=look, at_a_time=1) == "AliExpress"
    assert asked == [b"bank", b"shop"]


@pytest.mark.asyncio
async def test_nobody_named_leaves_the_field_empty() -> None:
    async def look(content: bytes, *, content_type: str, question: str, max_tokens: int) -> str:
        return "UKJENT"

    assert await vendor.named_by([(b"a", "image/png")], look=look) is None


@pytest.mark.asyncio
async def test_a_model_that_cannot_be_reached_is_not_an_error() -> None:
    """The field stays empty, which is where it was."""

    async def look(content: bytes, *, content_type: str, question: str, max_tokens: int) -> str:
        raise RuntimeError("Løynd er nede")

    assert await vendor.named_by([(b"a", "image/png")], look=look) is None


@pytest.mark.asyncio
async def test_one_broken_image_does_not_stop_the_next() -> None:
    async def look(content: bytes, *, content_type: str, question: str, max_tokens: int) -> str:
        if content == b"broken":
            raise RuntimeError("nope")
        return "AliExpress"

    images = [(b"broken", "image/png"), (b"fine", "image/png")]
    assert await vendor.named_by(images, look=look, at_a_time=2) == "AliExpress"

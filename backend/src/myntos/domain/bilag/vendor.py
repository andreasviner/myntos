"""Asking a model who a screenshot is from.

Every other name on a bilag is read: printed beside an organisasjonsnummer,
written after "Selger", or matched against the party register. That works on
documents, because a document prints the sender's name at the top of page one.

An app does not. It draws its logo there and puts a search box and a row of tabs
where a letterhead would be, and the transcription of nine AliExpress
screenshots names two shops and never AliExpress. Reading a name out of a
*position* on such a page produced, in order, "Add receipt" from a bank app's
button, "Order ID, product or sto..." from a search field, "IMG 9362" from the
camera roll, and "Returns/re" from a tab. Each fix produced the next one,
because a list of words that are not company names cannot keep up with an
interface that is free to print anything anywhere.

So the question goes to the one reader that has what the text lacks: the vision
model, while it still has the pixels. It answers "AliExpress" for an order page
and "UKJENT" for a bank transaction with the merchant collapsed behind "Show
more", which is the right answer to both.

**Only when nothing else knew.** A registered party, an organisasjonsnummer, a
labelled seller and the model's own reading of the transcription all come first,
and each of them is evidence this is not. Asking costs a request per image and
about three seconds, so it is asked where the alternative is an empty field.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable
from typing import Protocol

logger = logging.getLogger(__name__)

#: The question. Deliberately about the *app*, not about the seller.
#:
#: "Hvem er selgeren" gets the shop inside the marketplace back, "Eite
#: Technology Store", and on a bank screenshot it gets the account holder, which
#: is us. Asking which app the screenshot is from gets "AliExpress" from the
#: order page and "UKJENT" from the bank page, because a bank app is not
#: something the payment was made *to*.
QUESTION = (
    "Hvilken app eller nettbutikk er dette skjermbildet fra? "
    "Svar med bare navnet på firmaet. Er du usikker, svar UKJENT."
)

#: Room for a name and not for a sentence.
MAX_TOKENS = 24
MAX_NAME_CHARS = 60

#: How the model declines, and the ways a small model dresses it up. Compared
#: after the same flattening the reply gets, so "N/A" and "n a" are one entry.
_UNKNOWN = frozenset({"ukjent", "unknown", "vet ikke", "usikker", "ingen", "none", "n a"})

#: A name is not a sentence. The same six that every other name on a bilag gets,
#: which holds "FEARLESS PC Accessories Store" with room to spare.
MAX_NAME_WORDS = 6

_QUOTES = "\"'«»“”"
_NOT_ALNUM = re.compile(r"[^0-9A-Za-zÆØÅæøå]+")
_LETTER = re.compile(r"[A-Za-zÆØÅæøå]")


class Look(Protocol):
    """Something that can put one question to a model about one image."""

    def __call__(
        self, content: bytes, *, content_type: str, question: str, max_tokens: int
    ) -> Awaitable[str]: ...


def clean(answer: str) -> str | None:
    """The name in a reply, or ``None`` when the model declined.

    The checks here are about the *shape* of a reply, not about which names are
    real. A model that answers with a sentence, an empty string or "UKJENT" has
    not named anybody, and that is all this can tell. Whether "AliExpress" is
    the right counterparty for this bilag is the user's to confirm, which is
    what the "Foreslått av KI" mark next to the field is for.
    """
    text = " ".join(answer.split()).strip(_QUOTES + " .:,;-")
    if not text or len(text) > MAX_NAME_CHARS:
        return None
    if " ".join(_NOT_ALNUM.sub(" ", text).casefold().split()) in _UNKNOWN:
        return None
    # A model that starts explaining itself has not answered the question, and
    # the explanation is usually a refusal written out longhand.
    if len(text.split()) > MAX_NAME_WORDS:
        return None
    # Two letters and mostly letters, the same floor every other name on a bilag
    # has to clear. It is here to catch a reply that is punctuation or a number,
    # not to have an opinion about the name.
    letters = len(_LETTER.findall(text))
    if letters < 2 or letters < len(text) / 2:
        return None
    return text


async def named_by(
    images: list[tuple[bytes, str]],
    *,
    look: Look,
    at_a_time: int = 3,
) -> str | None:
    """Ask about ``images`` in order until one of them names somebody.

    In batches rather than all at once, and in upload order, for two reasons
    that point the same way. The deployment has room for about three concurrent
    requests, so a tenth one queues rather than going faster. And the first
    screenshot a person attaches is usually the one that shows the shop: the
    batch that answers is normally the first, which makes the common case one
    round of about three seconds rather than nine.

    A model that cannot be reached is not an error here. The field stays empty,
    which is where it was.
    """
    for start in range(0, len(images), at_a_time):
        batch = images[start : start + at_a_time]
        replies = await asyncio.gather(
            *(
                look(content, content_type=content_type, question=QUESTION, max_tokens=MAX_TOKENS)
                for content, content_type in batch
            ),
            return_exceptions=True,
        )
        for reply in replies:
            if isinstance(reply, BaseException):
                logger.info("Løynd did not name a vendor: %s", type(reply).__name__)
                continue
            name = clean(reply)
            if name is not None:
                return name
    return None

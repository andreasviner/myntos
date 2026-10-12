"""Remembering what a document said, and the rules that keeps it honest.

The cache exists for one reason: the register now re-reads a bilag whenever the
user edits its description, and re-transcribing nine screenshots on every edit
would make the feature unusable. Everything below is about it staying a
convenience — bounded, expiring, keyed by the thing it describes, and never
something the register may depend on.
"""

from __future__ import annotations

from myntos.domain.bilag.reading_cache import Reading, ReadingCache, key

TEXT = Reading(text="Batteri 1 152,00", title=None, encrypted=False, unreadable=False)


def a_key(content: bytes = b"%PDF-1.7 elektra", *, filename: str = "faktura.pdf") -> str:
    return key(content, filename=filename, content_type="application/pdf", use_loynd=True)


def test_a_reading_comes_back_for_the_same_bytes() -> None:
    cache = ReadingCache()
    cache.put(a_key(), TEXT)

    assert cache.get(a_key()) == TEXT


def test_different_bytes_are_a_different_document() -> None:
    cache = ReadingCache()
    cache.put(a_key(b"one"), TEXT)

    assert cache.get(a_key(b"two")) is None


def test_the_same_bytes_under_another_name_are_read_again() -> None:
    """The extractor is told the filename, so the name is part of the reading.

    Two screenshots with identical bytes and different names went to a reader
    that picks a format from the extension. Keying on content alone handed the
    second one the first one's text, which is how one unreadable photograph
    came back looking readable.
    """
    cache = ReadingCache()
    cache.put(a_key(filename="kvittering.png"), TEXT)

    assert cache.get(a_key(filename="uskarp.png")) is None


def test_a_company_with_ki_off_never_gets_the_models_reading() -> None:
    """The switch has to hold here too, or it is not a switch.

    The same PDF read by the vision model and read by pypdf are two different
    texts, and handing a company that turned KI off the model's one would be the
    promise broken in the one place nobody would think to look.
    """
    cache = ReadingCache()
    content = b"%PDF-1.7 elektra"
    with_model = key(content, filename="f.pdf", content_type="application/pdf", use_loynd=True)
    without = key(content, filename="f.pdf", content_type="application/pdf", use_loynd=False)
    cache.put(with_model, TEXT)

    assert cache.get(without) is None


def test_an_entry_expires() -> None:
    cache = ReadingCache(ttl=60.0)
    cache.put(a_key(), TEXT, now=1000.0)

    assert cache.get(a_key(), now=1030.0) == TEXT
    assert cache.get(a_key(), now=1100.0) is None
    # And the expired entry is dropped rather than left to sit there.
    assert len(cache) == 0


def test_the_oldest_goes_first_when_it_is_full() -> None:
    cache = ReadingCache(max_entries=2)
    cache.put(a_key(b"a"), TEXT)
    cache.put(a_key(b"b"), TEXT)
    cache.put(a_key(b"c"), TEXT)

    assert len(cache) == 2
    assert cache.get(a_key(b"a")) is None
    assert cache.get(a_key(b"c")) == TEXT


def test_reading_one_keeps_it_from_being_evicted() -> None:
    """The file somebody is working on is the last thing to throw away."""
    cache = ReadingCache(max_entries=2)
    cache.put(a_key(b"a"), TEXT)
    cache.put(a_key(b"b"), TEXT)
    cache.get(a_key(b"a"))
    cache.put(a_key(b"c"), TEXT)

    assert cache.get(a_key(b"a")) == TEXT
    assert cache.get(a_key(b"b")) is None


def test_an_empty_reading_is_remembered_too() -> None:
    """ "This file yields no text" is an answer, and a repeatable one.

    Re-asking a vision model for it on every edit is the exact cost this exists
    to avoid, so a blank result is cached like any other.
    """
    cache = ReadingCache()
    blank = Reading(text="", title=None, encrypted=False, unreadable=True)
    cache.put(a_key(), blank)

    assert cache.get(a_key()) == blank

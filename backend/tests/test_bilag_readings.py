"""The readings kept while somebody answers questions about a bilag.

A cache in every respect but one: an entry belongs to a company and a person,
and is found by nobody else. The rest is the reading cache's bargain, bounded
and short-lived and never a source of truth.
"""

from __future__ import annotations

from myntos.domain.bilag.readings import TTL_SECONDS, Readings


def test_a_reading_is_found_by_its_own_company_and_person() -> None:
    store: Readings[str] = Readings()
    reading_id = store.open("bilaget", company_id=1, user_id=7, now=0.0)
    assert store.get(reading_id, company_id=1, user_id=7, now=1.0) == "bilaget"


def test_nobody_else_finds_it_and_cannot_tell_it_exists() -> None:
    """Another company, or a colleague in the same one: the answer an unknown id gets."""
    store: Readings[str] = Readings()
    reading_id = store.open("bilaget", company_id=1, user_id=7, now=0.0)
    assert store.get(reading_id, company_id=2, user_id=7, now=1.0) is None
    assert store.get(reading_id, company_id=1, user_id=8, now=1.0) is None
    assert store.get("finnes-ikke", company_id=1, user_id=7, now=1.0) is None
    # Asking under the wrong owner does not spend it for the right one.
    assert store.get(reading_id, company_id=1, user_id=7, now=2.0) == "bilaget"


def test_a_reading_nobody_answers_is_forgotten() -> None:
    store: Readings[str] = Readings()
    reading_id = store.open("bilaget", company_id=1, user_id=7, now=0.0)
    assert store.get(reading_id, company_id=1, user_id=7, now=TTL_SECONDS + 1) is None
    assert len(store) == 0


def test_answering_keeps_it() -> None:
    """The clock restarts on every answer, so three questions in a row keep their reading."""
    store: Readings[str] = Readings()
    reading_id = store.open("bilaget", company_id=1, user_id=7, now=0.0)
    for minute in range(1, 4):
        moment = minute * (TTL_SECONDS - 10)
        assert store.get(reading_id, company_id=1, user_id=7, now=moment) == "bilaget"


def test_the_store_is_bounded() -> None:
    store: Readings[int] = Readings(max_entries=2)
    first = store.open(1, company_id=1, user_id=7, now=0.0)
    store.open(2, company_id=1, user_id=7, now=1.0)
    store.open(3, company_id=1, user_id=7, now=2.0)
    assert len(store) == 2
    assert store.get(first, company_id=1, user_id=7, now=3.0) is None


def test_ids_cannot_be_counted_towards() -> None:
    store: Readings[int] = Readings()
    ids = {store.open(n, company_id=1, user_id=7, now=0.0) for n in range(50)}
    assert len(ids) == 50
    assert all(len(reading_id) >= 20 for reading_id in ids)

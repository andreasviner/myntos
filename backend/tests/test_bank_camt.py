"""The camt.053 reader: what it takes out of a file, and what it refuses.

Run against the hand-built fixtures in ``tests/fixtures/bank``, one per version
Norwegian banks export, and against variants of them that each break exactly
one thing. A refusal test built from a file that is wrong in three ways would
pass for whichever of the three the parser happened to notice first.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from myntos.domain.bank import camt, matching
from myntos.domain.bank.errors import (
    InvalidBankStatementError,
    UnbalancedBankStatementError,
    UnsupportedStatementCurrencyError,
)
from myntos.domain.ledger.cashflow import CashFlowDirection

FIXTURES = Path(__file__).parent / "fixtures" / "bank"
MARCH = (FIXTURES / "camt053_v02_mars.xml").read_bytes()
APRIL = (FIXTURES / "camt053_v08_april.xml").read_bytes()


def test_version_02_is_read_whole() -> None:
    parsed = camt.parse(MARCH)
    assert parsed.version == "001.02"
    assert parsed.message_id == "TESTBANK-20260401-000001"
    (statement,) = parsed.statements
    # The IBAN is reduced to the eleven digits CompanyBankAccount stores.
    assert statement.account_number == "15062033158"
    assert (statement.from_date, statement.to_date) == (date(2026, 3, 1), date(2026, 3, 31))
    assert statement.opening_balance == Decimal("10000.00")
    assert statement.closing_balance == Decimal("21201.00")

    incoming, purchase, fee = statement.entries
    assert incoming.direction is CashFlowDirection.INN
    assert incoming.amount == Decimal("12500.00")
    assert incoming.counterparty_name == "Fjordkunde AS"
    assert incoming.kid == "000100000100234"
    assert incoming.end_to_end_id == "E2E-KUNDE-0001"
    assert incoming.bank_reference == "9926030200001"

    assert purchase.direction is CashFlowDirection.UT
    # Money out names the creditor, not the debtor (who is the account holder).
    assert purchase.counterparty_name == "KONTORLAND AS"
    assert purchase.remittance == "Ordre 4711 Kontorrekvisita"
    # NOTPROVIDED is the format's way of saying there is none.
    assert purchase.end_to_end_id is None

    assert fee.counterparty_name is None
    assert fee.additional_info == "Kontohold mars"
    assert fee.booking_date == date(2026, 3, 31)


def test_version_08_reads_the_2019_shape() -> None:
    parsed = camt.parse(APRIL)
    assert parsed.version == "001.08"
    (statement,) = parsed.statements
    # BBAN in Othr/Id rather than an IBAN.
    assert statement.account_number == "15062033158"
    # PRCD is the previous day's close; the period starts the day after it.
    assert statement.opening_balance == Decimal("22500.00")
    assert statement.from_date == date(2026, 3, 16)
    # Five booked, one pending left out and counted.
    assert len(statement.entries) == 5
    assert statement.skipped_unbooked == 1
    deposit = statement.entries[-1]
    # Dbtr/Pty/Nm, the wrapper the 2019 versions added.
    assert deposit.counterparty_name == "Ola Nordmann"
    assert deposit.direction is CashFlowDirection.INN


def test_a_statement_that_does_not_add_up_is_refused() -> None:
    broken = MARCH.replace(b'<Amt Ccy="NOK">21201.00</Amt>', b'<Amt Ccy="NOK">21200.00</Amt>')
    with pytest.raises(UnbalancedBankStatementError, match="går ikke opp"):
        camt.parse(broken)


def test_a_missing_entry_is_caught_by_the_banks_own_totals() -> None:
    """Remove an entry and fix the closing balance: only TxsSummry still knows."""
    start = MARCH.index(b"<Ntry>\n        <NtryRef>3</NtryRef>")
    end = MARCH.index(b"</Ntry>", start) + len(b"</Ntry>")
    truncated = MARCH[:start] + MARCH[end:]
    truncated = truncated.replace(b">21201.00<", b">21250.00<")
    with pytest.raises(UnbalancedBankStatementError, match="ufullstendig"):
        camt.parse(truncated)


def test_a_negative_opening_balance_is_read_with_its_sign() -> None:
    overdrawn = MARCH.replace(
        b'<Amt Ccy="NOK">10000.00</Amt>\n        <CdtDbtInd>CRDT</CdtDbtInd>',
        b'<Amt Ccy="NOK">10000.00</Amt>\n        <CdtDbtInd>DBIT</CdtDbtInd>',
    ).replace(b">21201.00<", b">1201.00<")
    (statement,) = camt.parse(overdrawn).statements
    assert statement.opening_balance == Decimal("-10000.00")


def test_a_foreign_currency_entry_is_refused() -> None:
    euro = MARCH.replace(b'<Amt Ccy="NOK">49.00</Amt>', b'<Amt Ccy="EUR">49.00</Amt>')
    with pytest.raises(UnsupportedStatementCurrencyError, match="EUR"):
        camt.parse(euro)


def test_a_foreign_currency_account_is_refused() -> None:
    euro = MARCH.replace(b"<Ccy>NOK</Ccy>", b"<Ccy>EUR</Ccy>")
    with pytest.raises(UnsupportedStatementCurrencyError):
        camt.parse(euro)


def test_a_dtd_is_refused_before_any_entity_is_expanded() -> None:
    bomb = (
        b'<?xml version="1.0"?>\n<!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>\n'
        + MARCH.split(b"?>", 1)[1].replace(b"Kontohold mars", b"&lol2;")
    )
    with pytest.raises(InvalidBankStatementError, match="DTD"):
        camt.parse(bomb)


def test_an_external_entity_is_refused() -> None:
    xxe = (
        b'<?xml version="1.0"?>\n<!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]>\n'
        b"<Document>&x;</Document>"
    )
    with pytest.raises(InvalidBankStatementError, match="DTD"):
        camt.parse(xxe)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"", "tom"),
        (b"%PDF-1.7 not xml", "gyldig XML"),
        (b"<Document xmlns='urn:something:else'/>", "camt.053"),
        (
            MARCH.replace(b"camt.053.001.02", b"camt.054.001.02"),
            "avisering",
        ),
        (
            MARCH.replace(b"<IBAN>NO6515062033158</IBAN>", b"<IBAN>DE89370400440532013000</IBAN>"),
            "norsk",
        ),
        (MARCH.replace(b"<Cd>OPBD</Cd>", b"<Cd>OPAV</Cd>"), "inngående saldo"),
        (MARCH.replace(b"<CdtDbtInd>DBIT</CdtDbtInd>", b"", 1), "CdtDbtInd"),
        (MARCH.replace(b">49.00<", b">49.001<"), "ugyldig beløp"),
    ],
)
def test_what_is_not_a_statement_is_refused_by_name(content: bytes, message: str) -> None:
    with pytest.raises(InvalidBankStatementError, match=message):
        camt.parse(content)


def test_the_bank_reference_is_the_dedup_key_in_both_versions() -> None:
    """The same booking in the March and the April file gets the same key."""
    march = camt.parse(MARCH).statements[0]
    april = camt.parse(APRIL).statements[0]
    march_keys = set(camt.dedup_keys(march.entries))
    april_keys = camt.dedup_keys(april.entries)
    assert march_keys & set(april_keys) == {"ref:9926031600002", "ref:9926033100003"}


def test_identical_entries_without_a_reference_stay_two() -> None:
    april = camt.parse(APRIL).statements[0]
    keys = camt.dedup_keys(april.entries)
    fees = [key for key in keys if key.startswith("fp:")]
    assert len(fees) == 2
    assert len(set(fees)) == 2
    # Same fingerprint, numbered: an overlapping file covering the same day
    # numbers them the same way and is recognised as holding the same two.
    assert fees[0].rsplit(":", 1)[0] == fees[1].rsplit(":", 1)[0]
    assert camt.dedup_keys(april.entries) == keys


# --- Matching ------------------------------------------------------------------------


def _line(**overrides: object) -> matching.LineFacts:
    values: dict[str, object] = {
        "booking_date": date(2026, 3, 16),
        "amount": Decimal("1250.00"),
        "direction": CashFlowDirection.UT,
        "counterparty_name": "KONTORLAND AS",
        "remittance": "Ordre 4711",
        "kid": None,
        "additional_info": None,
    }
    values.update(overrides)
    return matching.LineFacts(**values)  # type: ignore[arg-type]


def _candidate(voucher_id: int, **overrides: object) -> matching.Candidate:
    values: dict[str, object] = {
        "voucher_id": voucher_id,
        "voucher_no": f"2026-{voucher_id:04d}",
        "occurred_on": date(2026, 3, 14),
        "gross_amount": Decimal("1250.00"),
        "direction": CashFlowDirection.UT,
        "description": "Kontorrekvisita",
        "party_name": None,
        "type_code": "kontorrekvisita",
    }
    values.update(overrides)
    return matching.Candidate(**values)  # type: ignore[arg-type]


def test_a_different_amount_is_never_suggested() -> None:
    assert matching.score(_line(), _candidate(1, gross_amount=Decimal("1249.00"))) is None
    assert matching.score(_line(), _candidate(1, direction=CashFlowDirection.INN)) is None


def test_a_date_outside_the_window_is_never_suggested() -> None:
    assert matching.score(_line(), _candidate(1, occurred_on=date(2026, 2, 1))) is None


def test_the_name_in_the_bank_lifts_a_candidate() -> None:
    plain = _candidate(1, occurred_on=date(2026, 3, 16))
    named = _candidate(2, occurred_on=date(2026, 3, 14), party_name="Kontorland AS")
    ranked = matching.rank(_line(), [plain, named])
    assert [s.candidate.voucher_id for s in ranked] == [2, 1]
    assert ranked[0].strong
    assert "Navnet i banken stemmer med bilaget" in ranked[0].reasons


def test_a_company_form_is_not_a_name() -> None:
    """ "AS" in common is no evidence that two names are the same supplier."""
    other = _candidate(1, party_name="Helt Annet AS")
    suggestion = matching.score(_line(), other)
    assert suggestion is not None
    assert "Navnet i banken stemmer med bilaget" not in suggestion.reasons

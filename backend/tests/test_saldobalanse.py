"""Reading a saldobalanse exported from another system: pure, no database."""

from __future__ import annotations

from decimal import Decimal

import pytest

from myntos.domain.arsoppgjor.errors import InvalidSaldobalanseError
from myntos.domain.arsoppgjor.saldobalanse import parse, parse_amount


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 234,56", "1234.56"),
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("-1234,56", "-1234.56"),
        ("\u22121 234,56", "-1234.56"),
        ("(500,00)", "-500.00"),
        ("500,00-", "-500.00"),
        ("1\u00a0000\u202f000", "1000000"),
        ("1.234.567", "1234567"),
        ("42", "42"),
    ],
)
def test_norwegian_amounts_are_read_exactly(raw: str, expected: str) -> None:
    assert parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "abc", "1,2,3", "12a"])
def test_an_amount_that_cannot_be_read_is_refused(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_amount(raw)


def test_the_closing_column_is_read_and_sum_lines_are_skipped() -> None:
    content = (
        "Konto;Kontonavn;Inngående saldo;Bevegelse;Utgående saldo\n"
        "Eiendeler;;;;\n"
        "1920;Bankinnskudd;10 000,00;40 000,00;50 000,00\n"
        "1500;Kundefordringer;0,00;0,00;0,00\n"
        "2000;Aksjekapital;-30 000,00;0,00;-30 000,00\n"
        "2050;Annen egenkapital;;;-20 000,00\n"
        "Sum;;;;0,00\n"
    ).encode()
    rows = parse(content)
    assert [(r.account_number, r.debit, r.credit) for r in rows] == [
        ("1920", Decimal("50000.00"), Decimal("0.00")),
        ("2000", Decimal("0.00"), Decimal("30000.00")),
        ("2050", Decimal("0.00"), Decimal("20000.00")),
    ]
    assert rows[0].name == "Bankinnskudd"


def test_a_debit_and_credit_pair_is_read_and_windows_1252_decoded() -> None:
    content = "Kontonr;Navn;Debet;Kredit\n1920;Bank;500,00;\n2400;Leverandørgjeld;;500,00\n".encode(
        "cp1252"
    )
    rows = parse(content)
    assert [(r.account_number, r.debit, r.credit) for r in rows] == [
        ("1920", Decimal("500.00"), Decimal("0.00")),
        ("2400", Decimal("0.00"), Decimal("500.00")),
    ]
    assert rows[1].name == "Leverandørgjeld"


def test_a_file_without_an_account_column_is_refused() -> None:
    with pytest.raises(InvalidSaldobalanseError):
        parse(b"Navn;Saldo\nBank;100,00\n")


def test_a_file_without_a_balance_column_is_refused() -> None:
    with pytest.raises(InvalidSaldobalanseError):
        parse(b"Konto;Navn;Merknad\n1920;Bank;hei\n")


def test_fractions_of_an_ore_are_refused_with_the_line() -> None:
    with pytest.raises(InvalidSaldobalanseError, match="linje 2"):
        parse(b"Konto;Saldo\n1920;100,005\n")

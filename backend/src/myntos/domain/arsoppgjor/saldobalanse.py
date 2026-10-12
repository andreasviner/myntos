"""Read a saldobalanse exported from the previous accounting system.

Pure: bytes in, rows out, no database. Whether an account exists in this
company's kontoplan, and whether it belongs in an opening balance at all, is
decided by :mod:`.opening` on the rows this returns, so the same rules apply to
an upload and to a form somebody typed.

**What the file is expected to be.** The balance per account at the end of the
last year kept elsewhere, which is this year's opening. Every Norwegian system
exports one (Tripletex, Fiken, Visma, PowerOffice, Uni), and they agree on very
little else: the separator, the header wording, whether there is one signed
amount column or a debit and a credit pair, and whether the movement and the
opening balance come along beside the closing one. So the reader looks for the
*shape* rather than for one vendor's layout:

- an account column: the header says "konto" (or "kontonr"), and the values are
  digits;
- the amount: a pair named debet/kredit, or else the column that says it is the
  closing balance ("utgående", "UB", "saldo"), preferring the most explicit.

**Signs.** A single amount column is read as positive debit, negative credit,
which is how every one of those systems writes a saldobalanse. Read the other
way round, assets would land as debts; the review step shows each line on its
side before anything is posted, which is where a file that disagrees would be
caught.

**Numbers are Norwegian.** ``1 234,56``, ``1.234,56``, ``-1234,56`` and
``1234.56`` are all the same kind of thing, and a thousands separator is told
from a decimal point by position, never by guessing at magnitude. Amounts are
parsed straight into :class:`~decimal.Decimal`; a float never sees them.

What it refuses rather than guesses: a file with no recognisable account or
amount column, an amount it cannot read, and fractions of an øre. Each refusal
names the line.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from myntos.domain.arsoppgjor.errors import InvalidSaldobalanseError

ZERO = Decimal("0.00")

# Generous, because a saldobalanse is small: a few hundred accounts. A file
# far bigger than that is not one, and reading it all into memory to find out
# would be the wrong way round.
MAX_BYTES = 2 * 1024 * 1024

# Spaces that appear inside Norwegian numbers: ordinary, no-break and the two
# thin ones spreadsheet exports use as thousands separators.
_SPACES = re.compile("[ \\u00a0\\u2009\\u202f]")
_ACCOUNT = re.compile(r"^\d{3,10}$")


@dataclass(frozen=True)
class SaldoRow:
    """One account's balance, as a debit/credit pair like everything in the ledger."""

    line_no: int
    account_number: str
    name: str | None
    debit: Decimal
    credit: Decimal


def parse_amount(raw: str) -> Decimal:
    """``"1 234,56"`` and friends to a Decimal, or :class:`ValueError`."""
    text = _SPACES.sub("", raw.strip()).replace("\u2212", "-")
    negative = False
    if text.startswith("(") and text.endswith(")"):
        # Accounting notation for a negative figure.
        negative, text = True, text[1:-1]
    if text.endswith("-"):
        # Some exports put the sign last.
        negative, text = True, text[:-1]
    if text.startswith("-"):
        negative, text = not negative, text[1:]
    elif text.startswith("+"):
        text = text[1:]
    if not text:
        raise ValueError("tomt beløp")
    if "," in text and "." in text:
        # Whichever comes last is the decimal mark; the other groups thousands.
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        if text.count(",") > 1:
            raise ValueError(f"kan ikke lese {raw!r}")
        text = text.replace(",", ".")
    elif text.count(".") > 1:
        # 1.234.567 with no decimals: dots as thousands separators.
        text = text.replace(".", "")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        raise ValueError(f"kan ikke lese {raw!r}")
    try:
        value = Decimal(text)
    except InvalidOperation as error:  # pragma: no cover - the regex rules it out
        raise ValueError(f"kan ikke lese {raw!r}") from error
    return -value if negative else value


def _decode(content: bytes) -> str:
    if len(content) > MAX_BYTES:
        raise InvalidSaldobalanseError("filen er for stor til å være en saldobalanse")
    # UTF-8 (with or without the BOM Excel writes) first; then Windows-1252,
    # which is what older Norwegian systems still export, and which decodes any
    # byte sequence, so it has to be last.
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise InvalidSaldobalanseError("kan ikke lese tegnene i filen")  # pragma: no cover


def _dialect(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    # Semicolon first: with Norwegian decimal commas it is what every system
    # uses, and a comma-separated count would also count every amount.
    for delimiter in (";", "\t", ","):
        if delimiter in sample:
            return delimiter
    raise InvalidSaldobalanseError("fant ingen kolonner i filen; den må være CSV")


def _norm(header: str) -> str:
    return re.sub(r"[^a-zæøå]", "", header.strip().lower())


def _find_columns(header: list[str]) -> tuple[int, int | None, int | None, int | None, int | None]:
    """``(account, name, amount, debit, credit)`` column indices."""
    names = [_norm(h) for h in header]
    account = next(
        (i for i, n in enumerate(names) if n in {"konto", "kontonr", "kontonummer", "account"}),
        None,
    )
    if account is None:
        account = next(
            (i for i, n in enumerate(names) if n.startswith("konto") and "navn" not in n), None
        )
    if account is None:
        raise InvalidSaldobalanseError(
            "fant ingen kolonne for kontonummer. Første linje i filen må være overskrifter, "
            "og én av dem må hete «Konto» eller «Kontonr»"
        )
    name = next(
        (
            i
            for i, n in enumerate(names)
            if i != account and (n in {"navn", "kontonavn", "beskrivelse", "tekst"} or "navn" in n)
        ),
        None,
    )
    debit = next((i for i, n in enumerate(names) if n in {"debet", "debit", "utgåendedebet"}), None)
    credit = next(
        (i for i, n in enumerate(names) if n in {"kredit", "credit", "utgåendekredit"}), None
    )
    if debit is not None and credit is not None:
        return account, name, None, debit, credit
    # The closing balance, most explicit wording first. "Saldo" alone comes
    # last, because a file with both an opening and a closing column labels
    # them "Inngående saldo" and "Utgående saldo", and both contain it.
    preferences: tuple[Callable[[str], bool], ...] = (
        lambda n: n.startswith("utgående") or n in {"ub", "utgåendebalanse"},
        lambda n: n in {"saldo", "beløp", "belop", "balanse", "sum"},
        lambda n: "saldo" in n and not n.startswith("inngående"),
    )
    for wanted in preferences:
        found = next((i for i, n in enumerate(names) if i != account and wanted(n)), None)
        if found is not None:
            return account, name, found, None, None
    raise InvalidSaldobalanseError(
        "fant ingen kolonne for saldo. Myntos leser enten en kolonne som heter «Utgående saldo», "
        "«UB» eller «Saldo», eller et par som heter «Debet» og «Kredit»"
    )


def parse(content: bytes) -> list[SaldoRow]:
    """Every account with a balance, in the order the file lists them.

    Rows whose account cell is not digits are skipped rather than refused: sum
    lines, class headings ("Eiendeler") and blank separators are in every
    export, and none of them is an account. Accounts with a zero balance are
    left out, since an opening line of nothing is not a line.
    """
    text = _decode(content)
    reader = csv.reader(io.StringIO(text), delimiter=_dialect(text))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise InvalidSaldobalanseError("filen er tom")
    account_col, name_col, amount_col, debit_col, credit_col = _find_columns(rows[0])

    def cell(row: list[str], index: int | None) -> str:
        return row[index].strip() if index is not None and index < len(row) else ""

    parsed: list[SaldoRow] = []
    for line_no, row in enumerate(rows[1:], start=2):
        account = cell(row, account_col)
        # "1920 Bankinnskudd" in one cell is common enough to accept.
        number, _, rest = account.partition(" ")
        if not _ACCOUNT.match(number):
            continue
        try:
            if amount_col is not None:
                raw = cell(row, amount_col)
                signed = parse_amount(raw) if raw else ZERO
            else:
                raw_debit, raw_credit = cell(row, debit_col), cell(row, credit_col)
                signed = (parse_amount(raw_debit) if raw_debit else ZERO) - (
                    parse_amount(raw_credit) if raw_credit else ZERO
                )
        except ValueError as error:
            raise InvalidSaldobalanseError(
                f"linje {line_no} (konto {number}): beløpet kan ikke leses ({error})"
            ) from error
        exponent = signed.as_tuple().exponent
        if (
            isinstance(exponent, int)
            and exponent < -2
            and signed != signed.quantize(Decimal("0.01"))
        ):
            raise InvalidSaldobalanseError(
                f"linje {line_no} (konto {number}): beløpet har brøkdeler av et øre"
            )
        signed = signed.quantize(Decimal("0.01"))
        if signed == ZERO:
            continue
        parsed.append(
            SaldoRow(
                line_no=line_no,
                account_number=number,
                name=cell(row, name_col) or rest.strip() or None,
                debit=signed if signed > ZERO else ZERO,
                credit=-signed if signed < ZERO else ZERO,
            )
        )
    if not parsed:
        raise InvalidSaldobalanseError("fant ingen kontoer med saldo i filen")
    return parsed

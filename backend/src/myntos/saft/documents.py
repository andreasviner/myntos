"""The documents behind the previous program's vouchers, put on the vouchers they belong to.

The SAF-T file brings the books and none of the paper: the receipts and
invoices stay in the old program's archive (:mod:`myntos.saft.voucher_import`).
From Tripletex they come out as one printout of the vouchers with their
attachments, which :mod:`myntos.saft.printout` cuts into one PDF per voucher.
This module finds each one's voucher in Myntos and puts the PDF in the bilag
archive on it.

**Matched by the old program's number, never by what the document says.** The
import kept Tripletex's voucher number on every voucher it brought over, as
``SourceDocumentID`` («Tripletex bilag 240-2026»), precisely so each one could
be found in the old archive. The printout heads every voucher with the same
number. So the match is that key, looked up in the company's ``IMPORT``
journal, and nothing is guessed: a printed voucher whose number Myntos does not
have is reported and left out. Reading the invoice to find its voucher would
be a guess, and a document on the wrong voucher stays there for good.

**What is checked beside the number**, because a key that matches can still be
the wrong one:

- **The company.** Every cover names the company's organisasjonsnummer, and a
  printout naming another is refused whole
  (:class:`~myntos.saft.errors.PrintoutWrongCompanyError`), as another
  company's SAF-T file is.
- **The amount.** The cover's debit total against the voucher's in Myntos. On
  the farm pilot's September they agreed for all 51. A difference means the
  voucher was changed in Tripletex after the SAF-T file was taken; the document
  still belongs to that number, so it is put on, with a note
  (``belop_avviker``) saying to compare with a fresh file.

**A voucher split by date gets the document on every part.** The import splits
a Tripletex voucher whose payment is dated later into one voucher per date
(``2026-246`` and ``2026-246-1010``); each part carries the same reference, and
each is a voucher an auditor may open on its own and ask for its documentation.

**Only vouchers with attachments.** A voucher printed alone is Tripletex's page
of its own posting lines, which the voucher in Myntos already holds line for
line. Put in the archive it would look like documentation and be none
(``uten_vedlegg``).

**Append-only, so nothing twice.** A document whose bytes are already on the
voucher (the same SHA-256) is skipped, which makes uploading the same month
again harmless (:mod:`myntos.saft.printout` cuts the same way every time).
A changed printout (an attachment added in Tripletex since) is a new document
and goes on beside the first, as the archive's own rule says a correction
does.

**The review is the plan the post carries out**, computed by :func:`plan` both
times. Not the posting rolled back, as the SAF-T import does it: the document
store cannot roll back, so the review writes nothing and the post writes what
the same function says, after checking it is still what the person saw.
"""

from __future__ import annotations

import enum
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.arsoppgjor.findings import Finding, merknad
from myntos.domain.bilag import service as bilag_service
from myntos.domain.bilag.models import MAX_ATTACHMENT_BYTES, PDF_CONTENT_TYPE, Attachment
from myntos.domain.ledger.chart import IMPORT_JOURNAL_ID
from myntos.domain.ledger.models import Company, Journal, Transaction, TransactionLine
from myntos.saft import printout
from myntos.saft.errors import InvalidPrintoutError, PrintoutWrongCompanyError
from myntos.saft.printout import PrintedVoucher, Printout
from myntos.storage.base import DocumentStorage

ZERO = Decimal("0.00")

#: The program whose printout this reads, as the import names it in the
#: reference. The SAF-T header's ``SoftwareCompanyName``, cut to 12 characters
#: (``voucher_import._document``).
SOFTWARE = "Tripletex"

# «Tripletex bilag 240-2026». Anchored on the program, so a voucher brought over
# from another program's file that happens to share a number is never matched.
_REFERENCE = re.compile(r"^Tripletex\b.* bilag (\d{1,12})-(\d{4})$")

#: How many voucher numbers a note lists before it says how many more.
_LISTED = 10


class Action(enum.StrEnum):
    #: The document goes on the voucher, or on each of its parts.
    ATTACH = "attach"
    #: Every part already has these exact bytes.
    ALREADY_ATTACHED = "already_attached"
    #: Printed alone: nothing but Tripletex's page of the posting.
    NO_ATTACHMENTS = "no_attachments"
    #: Myntos has no voucher with this number from Tripletex.
    NOT_IN_BOOKS = "not_in_books"
    #: Larger than the archive takes as one document.
    TOO_LARGE = "too_large"
    #: The same voucher, the same pages, earlier in this upload.
    REPEATED = "repeated"


@dataclass(frozen=True)
class Target:
    """A voucher in Myntos a printed voucher's document belongs on."""

    voucher_pk: int
    voucher_no: str
    #: Whether it has the document already.
    already: bool


@dataclass(frozen=True)
class PlannedVoucher:
    file_index: int
    file_name: str
    printed: PrintedVoucher
    action: Action
    targets: tuple[Target, ...]
    #: The debit total of its vouchers in Myntos, None when there are none.
    booked_debit: Decimal | None

    @property
    def attach_to(self) -> tuple[Target, ...]:
        if self.action is not Action.ATTACH:
            return ()
        return tuple(target for target in self.targets if not target.already)


@dataclass(frozen=True)
class PlannedFile:
    name: str
    pages: int
    vouchers: int
    loose_pages: int


@dataclass(frozen=True)
class DocumentPlan:
    files: tuple[PlannedFile, ...]
    vouchers: tuple[PlannedVoucher, ...]
    findings: tuple[Finding, ...]

    def count(self, action: Action) -> int:
        return sum(1 for voucher in self.vouchers if voucher.action is action)

    @property
    def documents(self) -> int:
        """How many documents go into the archive: one per voucher in Myntos."""
        return sum(len(voucher.attach_to) for voucher in self.vouchers)

    @property
    def postable(self) -> bool:
        return self.documents > 0

    def attachments(self) -> frozenset[tuple[int, str]]:
        """What the post adds: (voucher, digest) pairs, to hold a later plan to."""
        return frozenset(
            (target.voucher_pk, voucher.printed.sha256 or "")
            for voucher in self.vouchers
            for target in voucher.attach_to
        )


def _kroner(value: Decimal) -> str:
    """``85 329,76``, the way the rest of the page writes an amount."""
    return f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")


def _listed(labels: Sequence[str]) -> str:
    shown = ", ".join(labels[:_LISTED])
    rest = len(labels) - _LISTED
    return shown + (f" og {rest} til" if rest > 0 else "")


def _check_company(company: Company, printouts: Sequence[tuple[str, Printout]]) -> None:
    """Refuse a printout of another company's vouchers, or one naming no company."""
    for name, read in printouts:
        named = {voucher.orgnr for voucher in read.vouchers if voucher.orgnr is not None}
        other = sorted(orgnr for orgnr in named if orgnr != company.orgnr)
        if other:
            raise PrintoutWrongCompanyError(
                f"«{name}» er bilagene til org.nr. {other[0]}, ikke til dette selskapet "
                f"({company.orgnr}). Velg riktig selskap øverst, eller skriv ut dette "
                "selskapets bilag"
            )
        if not named:
            raise InvalidPrintoutError(
                f"«{name}» sier ikke hvilket selskap bilagene er til. Myntos leser utskriften "
                "fra Tripletex, der hvert bilag har «Org.nr.» øverst"
            )


async def _imported(db: AsyncSession, company: Company) -> dict[tuple[str, int], list[Target]]:
    """The company's vouchers brought over from Tripletex, by the old program's number.

    Through the company's own journal, so another company's vouchers never
    reach the lookup (docs/company-isolation.md). Not a reversal: one of those
    carries no document of its own.
    """
    found: dict[tuple[str, int], list[Target]] = defaultdict(list)
    rows = await db.execute(
        select(Transaction.id, Transaction.transaction_id, Transaction.source_document_id)
        .join(Journal, Journal.id == Transaction.journal_id)
        .where(
            Journal.company_id == company.id,
            Journal.journal_id == IMPORT_JOURNAL_ID,
            Transaction.posted_at.is_not(None),
            Transaction.reverses_transaction_pk.is_(None),
            Transaction.source_document_id.like(f"{SOFTWARE}%"),
        )
        .order_by(Transaction.transaction_date, Transaction.id)
    )
    for pk, voucher_no, reference in rows:
        matched = _REFERENCE.match(reference or "")
        if matched is not None:
            key = (matched.group(1), int(matched.group(2)))
            found[key].append(Target(voucher_pk=int(pk), voucher_no=voucher_no, already=False))
    return found


async def plan(
    db: AsyncSession, *, company: Company, printouts: Sequence[tuple[str, Printout]]
) -> DocumentPlan:
    """What putting the printouts' documents on the vouchers would do. Writes nothing."""
    _check_company(company, printouts)
    imported = await _imported(db, company)
    printed_keys = {(v.number, v.year) for _, read in printouts for v in read.vouchers}
    pks = [
        target.voucher_pk
        for key, targets in imported.items()
        if key in printed_keys
        for target in targets
    ]

    debits: dict[int, Decimal] = {}
    held: set[tuple[int, str]] = set()
    if pks:
        for pk, debit in await db.execute(
            select(TransactionLine.transaction_pk, func.sum(TransactionLine.debit))
            .where(TransactionLine.transaction_pk.in_(pks))
            .group_by(TransactionLine.transaction_pk)
        ):
            debits[int(pk)] = debit or ZERO
        held = {
            (int(pk), digest)
            for pk, digest in await db.execute(
                select(Attachment.transaction_pk, Attachment.sha256).where(
                    Attachment.company_id == company.id, Attachment.transaction_pk.in_(pks)
                )
            )
        }

    files: list[PlannedFile] = []
    planned: list[PlannedVoucher] = []
    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()
    for index, (name, read) in enumerate(printouts):
        files.append(
            PlannedFile(
                name=name,
                pages=read.pages,
                vouchers=len(read.vouchers),
                loose_pages=read.loose_pages,
            )
        )
        if read.loose_pages:
            findings.append(
                merknad(
                    "sider_uten_bilag",
                    f"«{name}» har {read.loose_pages} sider før det første bilaget. De hører ikke "
                    "til noe bilag og legges ikke i bilagsarkivet.",
                )
            )
        for printed in read.vouchers:
            matches = imported.get((printed.number, printed.year), [])
            digest = printed.sha256
            targets = tuple(
                Target(
                    voucher_pk=target.voucher_pk,
                    voucher_no=target.voucher_no,
                    already=digest is not None and (target.voucher_pk, digest) in held,
                )
                for target in matches
            )
            booked = (
                sum((debits.get(t.voucher_pk, ZERO) for t in targets), ZERO) if targets else None
            )
            if printed.attachment_pages == 0:
                action = Action.NO_ATTACHMENTS
            elif not targets:
                action = Action.NOT_IN_BOOKS
            elif (printed.size or 0) > MAX_ATTACHMENT_BYTES:
                action = Action.TOO_LARGE
            elif (printed.label, digest or "") in seen:
                action = Action.REPEATED
            elif all(target.already for target in targets):
                action = Action.ALREADY_ATTACHED
            else:
                action = Action.ATTACH
            if action is Action.ATTACH:
                seen.add((printed.label, digest or ""))
            if (
                action is Action.ATTACH
                and printed.debit is not None
                and booked is not None
                and printed.debit != booked
            ):
                findings.append(
                    merknad(
                        "belop_avviker",
                        f"Bilag {printed.label}: Tripletex-siden sier {_kroner(printed.debit)} i "
                        f"debet, Myntos har {_kroner(booked)}. Bilaget kan være endret i "
                        "Tripletex etter at SAF-T-filen ble tatt ut; sammenlign med en ny "
                        "SAF-T-fil. Dokumentet legges likevel på bilaget med samme nummer.",
                    )
                )
            if action is Action.TOO_LARGE:
                findings.append(
                    merknad(
                        "for_stort",
                        f"Bilag {printed.label} er {(printed.size or 0) // (1024 * 1024)} MB med "
                        f"vedleggene, og bilagsarkivet tar høyst "
                        f"{MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB i ett dokument. Det legges "
                        "ikke på; ta vare på det i Tripletex.",
                    )
                )
            planned.append(
                PlannedVoucher(
                    file_index=index,
                    file_name=name,
                    printed=printed,
                    action=action,
                    targets=targets,
                    booked_debit=booked,
                )
            )

    def labels(action: Action) -> list[str]:
        return [v.printed.label for v in planned if v.action is action]

    missing = labels(Action.NOT_IN_BOOKS)
    if missing:
        findings.append(
            merknad(
                "bilag_finnes_ikke",
                f"{len(missing)} bilag i utskriften finnes ikke blant bilagene hentet fra "
                f"Tripletex: {_listed(missing)}. Hent dem med SAF-T-filen først, og last opp "
                "utskriften igjen; det som alt er lagt på, hoppes da over.",
            )
        )
    alone = labels(Action.NO_ATTACHMENTS)
    if alone:
        findings.append(
            merknad(
                "uten_vedlegg",
                f"{len(alone)} bilag har bare Tripletex' egen side med posteringene, uten "
                f"vedlegg, og får ingenting i bilagsarkivet: {_listed(alone)}.",
            )
        )
    already = labels(Action.ALREADY_ATTACHED)
    if already:
        findings.append(
            merknad(
                "allerede_i_arkivet",
                f"{len(already)} bilag har fått det samme dokumentet før, og hoppes over.",
            )
        )
    split = [v.printed.label for v in planned if v.action is Action.ATTACH and len(v.targets) > 1]
    if split:
        findings.append(
            merknad(
                "bilag_delt_paa_dato",
                f"{len(split)} bilag ble delt på dato da de ble hentet, og dokumentet legges på "
                f"hver del: {_listed(split)}.",
            )
        )
    result = DocumentPlan(files=tuple(files), vouchers=tuple(planned), findings=tuple(findings))
    if not result.postable:
        findings.append(
            merknad(
                "ingenting_aa_legge_paa",
                "Ingenting å legge i bilagsarkivet: ingen bilag i utskriften har vedlegg som ikke "
                "alt ligger på et bilag i Myntos.",
            )
        )
        result = DocumentPlan(
            files=result.files, vouchers=result.vouchers, findings=tuple(findings)
        )
    return result


async def attach(
    db: AsyncSession,
    storage: DocumentStorage,
    *,
    company: Company,
    plan: DocumentPlan,
    file_index: int,
    content: bytes,
    user_id: int | None,
) -> int:
    """Put one printout's planned documents in the bilag archive. Returns how many.

    The caller has just made ``plan`` from these bytes and held it to the
    review; each document is cut again and refused if it is not the one
    hashed then (:func:`myntos.saft.printout.cut`).
    """
    wanted = [
        voucher
        for voucher in plan.vouchers
        if voucher.file_index == file_index and voucher.attach_to
    ]
    if not wanted:
        return 0
    name = wanted[0].file_name
    pieces = await run_in_threadpool(
        printout.cut, content, name=name, vouchers=[voucher.printed for voucher in wanted]
    )
    added = 0
    for voucher, (_, document) in zip(wanted, pieces, strict=True):
        for target in voucher.attach_to:
            # With the company, like every lookup of a voucher: the id came off
            # this company's own journal in plan(), and the wall is kept anyway.
            transaction = await db.scalar(
                select(Transaction)
                .join(Journal, Journal.id == Transaction.journal_id)
                .where(Transaction.id == target.voucher_pk, Journal.company_id == company.id)
            )
            if transaction is None:
                raise InvalidPrintoutError(f"bilag {target.voucher_no} finnes ikke lenger")
            await bilag_service.attach_document(
                db,
                storage,
                company=company,
                transaction=transaction,
                content=document,
                filename=f"{SOFTWARE} bilag {voucher.printed.label}.pdf",
                uploaded_by_user_id=user_id,
                content_type=PDF_CONTENT_TYPE,
            )
            added += 1
    return added


__all__ = [
    "SOFTWARE",
    "Action",
    "DocumentPlan",
    "PlannedFile",
    "PlannedVoucher",
    "Target",
    "attach",
    "plan",
]

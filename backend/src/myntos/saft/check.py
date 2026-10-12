"""What the XSD does not check, checked before the file leaves.

Altinn validates an RF-1363 attachment against the schema and nothing else. The
schema says an ``AccountID`` on a line is a string of at most 70 characters; it
does not say the account exists in ``MasterFiles``. It says a
``GroupingCategory`` is a string; it does not say the category is in
Skatteetaten's list for the year. A file that is wrong in those ways is
accepted, and the error surfaces at the bokettersyn, which is the one moment
the file exists for.

So this module reads the same :class:`~myntos.saft.export.AuditFile` the file is
written from and lists what is wrong with it, in Norwegian, because the person
who has to fix it is the one reading.

Two severities. A **feil** stops the download: a file that would fail at the
auditor is worse than no file, because it looks like compliance. A **merknad**
is something a person should look at and may well be right, like a year whose
grouping list Skatteetaten has not published yet.
"""

from __future__ import annotations

import enum
from collections import Counter
from dataclasses import dataclass

from myntos.domain.ledger.chart import INPUT_VAT_ACCOUNT, OUTPUT_VAT_ACCOUNT
from myntos.saft import codes
from myntos.saft.export import ZERO, AuditFile, PartyKind

# Maximum lengths the schema gives the identifiers Myntos stores in wider
# columns. Truncating would be worse than refusing: two vouchers whose ids
# differ only past the limit would become one id in the file.
_LIMITS: dict[str, int] = {
    "JournalID": 18,  # SAFshorttextType
    "Journal/Type": 9,  # SAFcodeType
    "RecordID": 18,  # SAFshorttextType
    "TransactionID": 70,  # SAFmiddle2textType
    "AccountID": 70,  # SAFmiddle2textType
    "CustomerID/SupplierID": 35,  # SAFmiddle1textType
    "SourceDocumentID": 35,  # SAFmiddle1textType
    "VoucherType": 70,  # SAFmiddle2textType
    "AnalysisType": 9,  # SAFcodeType
    "AnalysisID": 35,  # SAFmiddle1textType
}

# The two standard codes whose TaxInformation legitimately sits on the VAT
# account line itself: border VAT on a tollpass, where the bill *is* the tax.
_TAX_ON_VAT_LINE = frozenset({"14", "15"})


class Severity(enum.StrEnum):
    FEIL = "feil"
    MERKNAD = "merknad"


@dataclass(frozen=True)
class Finding:
    severity: Severity
    # Stable and machine-readable, for the frontend and for tests; the message
    # is for people and may be reworded.
    code: str
    message: str


def _too_long(kind: str, value: str | None) -> bool:
    return value is not None and len(value) > _LIMITS[kind]


def check(audit: AuditFile) -> list[Finding]:
    findings: list[Finding] = []

    def feil(code: str, message: str) -> None:
        findings.append(Finding(Severity.FEIL, code, message))

    def merknad(code: str, message: str) -> None:
        findings.append(Finding(Severity.MERKNAD, code, message))

    # --- Master data ----------------------------------------------------------
    account_ids = {account.account_id for account in audit.accounts}
    published = codes.grouping_pairs(audit.year) is not None
    if not published:
        merknad(
            "grupperingsliste_mangler",
            f"Skatteetaten har ikke publisert en grupperingsliste for {audit.year} som Myntos "
            "kjenner til, så kontoenes næringsspesifikasjonskoder er ikke kontrollert mot den.",
        )
    for account in audit.accounts:
        if _too_long("AccountID", account.account_id):
            feil("for_lang_konto", f"Kontonummeret {account.account_id} er for langt for SAF-T.")
        if account.grouping_category is None or account.grouping_code is None:
            feil(
                "gruppering_mangler",
                f"Konto {account.account_id} har ingen næringsspesifikasjonskode for {audit.year}.",
            )
            continue
        valid = codes.is_valid_grouping(
            audit.year, account.grouping_category, account.grouping_code
        )
        if valid is False:
            feil(
                "ukjent_gruppering",
                f"Konto {account.account_id} er knyttet til {account.grouping_category} "
                f"{account.grouping_code}, som ikke finnes i Skatteetatens liste for "
                f"{audit.year}.",
            )
        if account.grouping_code == "2050" and account.closing.net > ZERO:
            # Positive equity (2050) with a debit closing balance is negative
            # equity, which the list puts on 2080. A static mapping cannot know.
            merknad(
                "negativ_egenkapital",
                f"Konto {account.account_id} er ført som positiv egenkapital, men har "
                "debetsaldo ved årets slutt. Negativ egenkapital skal grupperes som 2080.",
            )

    party_ids: dict[PartyKind, set[str]] = {PartyKind.CUSTOMER: set(), PartyKind.SUPPLIER: set()}
    for party in audit.parties:
        party_ids[party.kind].add(party.party_id)
        if _too_long("CustomerID/SupplierID", party.party_id):
            feil(
                "for_lang_partsid",
                f"Kunde- eller leverandørnummeret {party.party_id} er lengre enn 35 tegn.",
            )
        if party.balance_account_id not in account_ids:
            feil(
                "ukjent_reskontrokonto",
                f"{party.name} avregnes mot konto {party.balance_account_id}, som ikke er i "
                "kontoplanen.",
            )

    standard = codes.standard_tax_codes()
    tax_codes = {rate.code for rate in audit.tax_rates}
    for rate in audit.tax_rates:
        if rate.standard_tax_code not in standard:
            feil(
                "ukjent_standardkode",
                f"Mva-kode {rate.code} er knyttet til standardkode {rate.standard_tax_code}, "
                "som ikke er i Skatteetatens liste.",
            )

    analysis_pairs = {(v.analysis_type, v.analysis_id) for v in audit.analysis_values}
    for value in audit.analysis_values:
        if _too_long("AnalysisType", value.analysis_type) or _too_long(
            "AnalysisID", value.analysis_id
        ):
            feil(
                "for_lang_dimensjon",
                f"Dimensjonen {value.analysis_type}/{value.analysis_id} er for lang for SAF-T.",
            )

    # --- Entries ---------------------------------------------------------------
    for journal in audit.journals:
        if _too_long("JournalID", journal.journal_id) or _too_long(
            "Journal/Type", journal.journal_type
        ):
            feil(
                "for_lang_journal",
                f"Journalen {journal.journal_id} har en id eller type som er for lang for SAF-T.",
            )
        seen = Counter(entry.transaction_id for entry in journal.entries)
        for transaction_id, count in seen.items():
            if count > 1:
                feil(
                    "dobbelt_bilagsnummer",
                    f"Bilagsnummer {transaction_id} finnes {count} ganger i journalen "
                    f"{journal.journal_id}.",
                )

        for entry in journal.entries:
            name = f"Bilag {entry.transaction_id}"
            if _too_long("TransactionID", entry.transaction_id) or _too_long(
                "VoucherType", entry.voucher_type
            ):
                feil("for_langt_bilag", f"{name} har en id eller type som er for lang for SAF-T.")
            if not audit.start <= entry.transaction_date <= audit.end:
                feil(
                    "bilag_utenfor_aaret",
                    f"{name} er datert {entry.transaction_date:%d.%m.%Y}, utenfor "
                    f"regnskapsåret {audit.year}.",
                )
            if entry.period != entry.transaction_date.month:
                merknad(
                    "periode_avviker",
                    f"{name} er ført i periode {entry.period}, men datert "
                    f"{entry.transaction_date:%d.%m.%Y}.",
                )
            debit = sum((line.debit for line in entry.lines), ZERO)
            credit = sum((line.credit for line in entry.lines), ZERO)
            if debit != credit:
                feil(
                    "bilag_i_ubalanse",
                    f"{name} går ikke i null: debet {debit} mot kredit {credit}.",
                )
            records = Counter(line.record_id for line in entry.lines)
            if any(count > 1 for count in records.values()):
                feil("dobbel_linje", f"{name} har to linjer med samme linjenummer.")

            for line in entry.lines:
                where = f"{name}, linje {line.record_id}"
                if _too_long("RecordID", line.record_id):
                    feil("for_lang_linje", f"{where}: linjenummeret er for langt for SAF-T.")
                if line.account_id not in account_ids:
                    feil(
                        "ukjent_konto",
                        f"{where} er ført på konto {line.account_id}, som ikke er i kontoplanen.",
                    )
                if line.customer_id is not None and (
                    line.customer_id not in party_ids[PartyKind.CUSTOMER]
                ):
                    feil(
                        "ukjent_kunde", f"{where} viser til en kunde som ikke er i kunderegisteret."
                    )
                if line.supplier_id is not None and (
                    line.supplier_id not in party_ids[PartyKind.SUPPLIER]
                ):
                    feil(
                        "ukjent_leverandor",
                        f"{where} viser til en leverandør som ikke er i leverandørregisteret.",
                    )
                if _too_long("SourceDocumentID", line.source_document_id):
                    feil(
                        "for_lang_dokumentreferanse",
                        f"{where}: dokumentreferansen er lengre enn 35 tegn.",
                    )
                for allocation in line.analysis:
                    if (allocation.analysis_type, allocation.analysis_id) not in analysis_pairs:
                        feil(
                            "ukjent_dimensjon",
                            f"{where} er fordelt på en dimensjon som ikke er i dimensjonstabellen.",
                        )
                if line.tax is None:
                    continue
                if line.tax.code not in tax_codes:
                    feil(
                        "ukjent_mvakode",
                        f"{where} bruker mva-kode {line.tax.code}, som ikke er i mva-tabellen.",
                    )
                if (
                    line.account_id in {OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT}
                    and line.tax.code not in _TAX_ON_VAT_LINE
                ):
                    # The classic exporter bug, caught on the data: TaxInformation
                    # belongs on the line that carries the basis, not on the line
                    # that carries the VAT.
                    merknad(
                        "mva_paa_mvalinje",
                        f"{where} har mva-informasjon på selve mva-kontoen. Den hører hjemme "
                        "på linjen med grunnlaget.",
                    )

    if audit.total_debit != audit.total_credit:
        feil(
            "hovedbok_i_ubalanse",
            f"Hovedboken går ikke i null for {audit.year}: debet {audit.total_debit} mot "
            f"kredit {audit.total_credit}.",
        )
    opening = sum((account.opening.net for account in audit.accounts), ZERO)
    if opening != ZERO:
        feil(
            "inngaaende_balanse_i_ubalanse",
            f"Inngående balanse for {audit.year} går ikke i null (differanse {opening}).",
        )

    return findings


def blocking(findings: list[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.severity is Severity.FEIL]

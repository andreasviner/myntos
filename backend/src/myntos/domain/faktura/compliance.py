"""What bokføringsforskriften § 5-1 requires, as checks that run before issue.

Pure functions over the models. No session, no HTTP — the same reasoning as
:mod:`myntos.domain.ledger.posting`: the rules that matter legally belong in one
place that can be tested without a database, not spread across a request handler
and a template.

Everything here is checked at **issue**, never on save. A draft is allowed to be
half-written; § 5-1 describes what a salgsdokument must contain when it is
issued, and running the checks earlier would only mean nagging someone about a
field they were about to fill in.

Two severities, and the distinction is not cosmetic:

- :attr:`Severity.BLOKKERENDE` — the document would not satisfy the regulation.
  Issue refuses. Letting it through would produce a numbered, posted, archived
  document that is unlawful, and there is no edit path afterwards to fix it.
- :attr:`Severity.ADVARSEL` — the document is lawful but something is worth
  knowing: a deadline already missed (§ 5-2-2), or a requirement that is not in
  force yet but will be. Blocking on these would be us inventing rules.

The e-invoice requirement is the one that moves. Lov 19. juni 2026 nr. 39
(pliktig digital bokføring og e-fakturering) makes documentation of sales to
another bokføringspliktig issuable only in an electronic invoice format, with
sending mandatory from 1 January 2027 and receiving from 2030. Until that date
this module warns; from it, the same finding blocks. The date is a constant
rather than a deployment flag, because the law does not care what we configured.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from myntos.domain.faktura import kid
from myntos.domain.faktura.models import DocumentType, SalesDocument, SalesDocumentLine
from myntos.domain.faktura.totals import totals
from myntos.domain.faktura.vat import REVERSE_CHARGE_MARKING, VatCategory, is_sales_code
from myntos.domain.ledger.posting import ZERO

# Lov 19. juni 2026 nr. 39. Sending an e-invoice to a bokføringspliktig buyer
# becomes mandatory on this date; before it, the same gap is a warning.
E_INVOICE_MANDATORY_FROM = date(2027, 1, 1)

# § 5-2-2: salgsdokumentasjon skal utstedes snarest mulig og senest en måned
# etter levering. Expressed in days because a month is not a fixed length and
# the check only needs to know whether the deadline is behind us.
ISSUE_DEADLINE_DAYS = 31

# Organisasjonsformer whose sales documents must carry the word
# "Foretaksregisteret" together with the head-office address (§ 5-1-2 second
# paragraph). Aksjeselskap, allmennaksjeselskap and the Norwegian branch of a
# foreign company are always registered there.
FORETAKSREGISTERET_FORMS = frozenset({"AS", "ASA", "NUF"})

# The marking itself, printed by the renderer whenever the flag above is set.
FORETAKSREGISTERET_MARKING = "Foretaksregisteret"


class Severity(enum.StrEnum):
    """Whether a finding stops the document from being issued."""

    BLOKKERENDE = "blokkerende"
    ADVARSEL = "advarsel"


@dataclass(frozen=True)
class Violation:
    """One unmet requirement, with the rule it comes from."""

    # The legal reference, e.g. "bokføringsforskriften § 5-1-1 nr. 4". Carried so
    # the interface can show a user *why* rather than only *what*, and so a
    # change in the regulation is greppable.
    rule: str
    # The field the user has to go and fill in. Names a column on the document,
    # or "lines" for something the line list as a whole is missing.
    field: str
    message: str
    severity: Severity = Severity.BLOKKERENDE


def blocking(violations: Iterable[Violation]) -> list[Violation]:
    """Only the findings that must stop an issue."""
    return [v for v in violations if v.severity is Severity.BLOKKERENDE]


def check(
    document: SalesDocument,
    lines: Iterable[SalesDocumentLine] | None = None,
    *,
    today: date,
) -> list[Violation]:
    """Every requirement ``document`` does not meet, in the order § 5-1 states them.

    ``today`` is passed rather than read from the clock so the caller decides
    what "now" means — the e-invoice rule changes behaviour on a date, and a test
    that cannot move the date cannot cover both sides of it.

    § 5-1-1 nr. 1 — nummer og dokumentasjonsdato — has no check of its own. Both
    are set by :func:`~myntos.domain.faktura.service.issue` at the moment it
    assigns them, from the number series and the clock; neither can be supplied
    or withheld by a caller, so a missing one would be our bug rather than the
    user's omission, and a check here would only ever pass.
    """
    rows = list(lines if lines is not None else document.lines)
    found: list[Violation] = []
    found += _check_seller(document)
    found += _check_buyer(document)
    found += _check_lines(document, rows)
    found += _check_delivery(document, today=today)
    found += _check_payment(document, today=today)
    found += _check_vat(document, rows)
    found += _check_e_invoice(document, today=today)
    return found


def _check_seller(document: SalesDocument) -> list[Violation]:
    """§ 5-1-2 second paragraph — the seller's own details."""
    found: list[Violation] = []
    if not document.seller_name.strip():
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-2",
                field="seller_name",
                message="selgerens navn mangler",
            )
        )
    if not document.seller_orgnr:
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-2",
                field="seller_orgnr",
                message="selgerens organisasjonsnummer mangler",
            )
        )
    if document.seller_in_foretaksregisteret and not document.seller_address_line:
        # The regulation asks for the head-office address alongside the
        # "Foretaksregisteret" marking, so the two are checked together.
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-2",
                field="seller_address_line",
                message=(
                    "foretak registrert i Foretaksregisteret må oppgi hovedkontorets adresse "
                    "på salgsdokumentet"
                ),
            )
        )
    # The marking is applied whenever the flag is set, so the flag is what has to
    # be checked — and the organisasjonsform is the evidence. An AS whose flag was
    # never set would otherwise issue documents silently missing a word the
    # regulation requires, and nothing in the document itself would show it.
    if (
        document.seller_organisation_form in FORETAKSREGISTERET_FORMS
        and not document.seller_in_foretaksregisteret
    ):
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-2",
                field="seller_in_foretaksregisteret",
                message=(
                    f"et {document.seller_organisation_form} er registrert i Foretaksregisteret, "
                    f"og da skal ordet «Foretaksregisteret» stå på salgsdokumentet. "
                    f"Kryss av for det under Organisasjon"
                ),
            )
        )
    return found


def _check_buyer(document: SalesDocument) -> list[Violation]:
    """§ 5-1-2 first paragraph — the buyer, by name and address or orgnr."""
    found: list[Violation] = []
    if not document.buyer_name.strip():
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-2",
                field="buyer_name",
                message="kjøperens navn mangler",
            )
        )
    has_address = bool(document.buyer_address_line or document.buyer_postal_code)
    if not has_address and not document.buyer_orgnr:
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-2",
                field="buyer_address_line",
                message=(
                    "kjøperen må identifiseres med adresse eller organisasjonsnummer — "
                    "navnet alene er ikke nok"
                ),
            )
        )
    return found


def _check_lines(document: SalesDocument, lines: list[SalesDocumentLine]) -> list[Violation]:
    """§ 5-1-1 nr. 3 — ytelsens art og omfang."""
    if not lines:
        return [
            Violation(
                rule="bokføringsforskriften § 5-1-1 nr. 3",
                field="lines",
                message="salgsdokumentet må beskrive hva som er solgt; det har ingen linjer",
            )
        ]
    found: list[Violation] = []
    for line in lines:
        if not line.description.strip():
            found.append(
                Violation(
                    rule="bokføringsforskriften § 5-1-1 nr. 3",
                    field=f"lines[{line.line_no}].description",
                    message=f"linje {line.line_no} mangler beskrivelse av ytelsens art",
                )
            )
        if line.quantity == ZERO:
            found.append(
                Violation(
                    rule="bokføringsforskriften § 5-1-1 nr. 3",
                    field=f"lines[{line.line_no}].quantity",
                    message=f"linje {line.line_no} mangler omfang (antall)",
                )
            )
    return found


def _check_delivery(document: SalesDocument, *, today: date) -> list[Violation]:
    """§ 5-1-1 nr. 4 — leveringstidspunkt og leveringssted, and § 5-2-2's deadline."""
    found: list[Violation] = []
    if document.delivery_date is None:
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-1 nr. 4",
                field="delivery_date",
                message="leveringstidspunktet mangler",
            )
        )
    elif (today - document.delivery_date).days > ISSUE_DEADLINE_DAYS:
        # A warning, not a refusal. The document is late, and it stays late
        # whether or not we issue it — refusing would leave the sale
        # undocumented, which is the worse of the two breaches.
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-2-2",
                field="delivery_date",
                message=(
                    "salgsdokumentet skulle vært utstedt senest en måned etter levering "
                    f"({document.delivery_date.isoformat()})"
                ),
                severity=Severity.ADVARSEL,
            )
        )
    if not document.delivery_place:
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-1 nr. 4",
                field="delivery_place",
                message="leveringsstedet mangler",
            )
        )
    return found


def _check_payment(document: SalesDocument, *, today: date) -> list[Violation]:
    """§ 5-1-1 nr. 5 — vederlag og forfallsdato.

    A kreditnota is exempt from the due date: it does not fall due, it reverses
    something that did.
    """
    if document.is_credit_note:
        return []
    if document.due_date is None:
        return [
            Violation(
                rule="bokføringsforskriften § 5-1-1 nr. 5",
                field="due_date",
                message="forfallsdato mangler",
            )
        ]
    found = _check_payment_reference(document)
    if document.due_date < today:
        # Lawful — § 5-1-1 nr. 5 asks for a forfallsdato, not for a future one —
        # and the ordinary consequence of invoicing late with terms counted from
        # delivery. Worth saying out loud, because an invoice that arrives
        # already overdue is as often a mistyped date as a deliberate one.
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-1 nr. 5",
                field="due_date",
                message=(
                    f"forfallsdatoen {document.due_date.isoformat()} har allerede vært; "
                    f"fakturaen forfaller i det den sendes"
                ),
                severity=Severity.ADVARSEL,
            )
        )
    return found


def _check_payment_reference(document: SalesDocument) -> list[Violation]:
    """A hand-typed KID whose check digit does not work.

    Not a legal requirement at all — a KID is a banking convenience, and nothing
    in § 5-1 asks for one — so this can only ever be a warning. It is worth
    making because the consequence is invisible until it happens: the payer's
    bank refuses the number, or worse accepts it against a different invoice, and
    the seller finds out when the payment does not reconcile.

    Only a reference the user typed is checked. An automatic one is generated
    with its own check digit (:mod:`myntos.domain.faktura.kid`), so a finding
    here would be our bug rather than the user's, and the check runs against both
    MOD10 and MOD11 because which one applies is between the seller and their
    bank.
    """
    if document.payment_reference_auto or not document.payment_reference:
        return []
    if kid.is_valid(document.payment_reference):
        return []
    return [
        Violation(
            rule="KID-avtale med banken",
            field="payment_reference",
            message=(
                f"kontrollsifferet i KID-en {document.payment_reference} går ikke opp, verken "
                f"etter mod10 eller mod11. Nettbanken til kjøperen vil trolig avvise den — "
                f"kontroller nummeret, eller la Myntos sette det"
            ),
            severity=Severity.ADVARSEL,
        )
    ]


def _check_vat(document: SalesDocument, lines: list[SalesDocumentLine]) -> list[Violation]:
    """§ 5-1-1 nr. 6 and nr. 7, and § 5-1-5's separate summing."""
    found: list[Violation] = []

    # § 5-1-1 nr. 6: the merverdiavgift must be stated in NOK. The ledger carries
    # NOK only, so the guard is on the document's currency rather than on a
    # restatement we do not yet compute.
    if document.currency_code != "NOK":
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-1 nr. 6",
                field="currency_code",
                message=(
                    "merverdiavgiften skal angis i norske kroner; "
                    "fakturering i annen valuta støttes ikke ennå"
                ),
            )
        )

    for line in lines:
        if not is_sales_code(line.tax_code):
            found.append(
                Violation(
                    rule="bokføringsforskriften § 5-1-1 nr. 6",
                    field=f"lines[{line.line_no}].tax_code",
                    message=(
                        f"linje {line.line_no} bruker mva-koden {line.tax_code}, "
                        f"som er en inngående kode og ikke kan stå på en salgsfaktura"
                    ),
                )
            )

    computed = totals(document, lines)

    # Charging output VAT without being in Merverdiavgiftsregisteret is not a
    # formality — it is collecting a tax on the state's behalf without the right
    # to, and the money is still owed once discovered.
    if not document.seller_vat_registered and computed.tax_total > ZERO:
        found.append(
            Violation(
                rule="merverdiavgiftsloven § 15-14",
                field="seller_vat_registered",
                message=(
                    "selskapet er ikke registrert i Merverdiavgiftsregisteret og kan "
                    "ikke oppgi merverdiavgift på salgsdokumentet"
                ),
            )
        )

    # BR-O-11: an "outside scope" breakdown cannot share a document with any
    # other. Caught here rather than at the recipient's validator, where it
    # surfaces as a rejected invoice days later.
    categories = {subtotal.category for subtotal in computed.subtotals}
    if VatCategory.OUT_OF_SCOPE in categories and len(categories) > 1:
        found.append(
            Violation(
                rule="EN 16931 BR-O-11",
                field="lines",
                message=(
                    "omsetning utenfor merverdiavgiftsloven kan ikke stå på samme "
                    "faktura som avgiftspliktig omsetning — skriv to fakturaer"
                ),
            )
        )

    # § 5-1-1 nr. 7. The marking and the suppression of VAT are both applied
    # automatically once the flag is set, so this cannot fail — what it can do is
    # surprise. Someone who ticks omvendt avgiftsplikt on lines coded 25 % gets a
    # document with no VAT on it and no error, and the difference between that
    # and a mistake is only visible if we say so.
    if document.reverse_charge:
        charged = [line for line in lines if line.tax_rate > ZERO]
        if charged:
            found.append(
                Violation(
                    rule="bokføringsforskriften § 5-1-1 nr. 7",
                    field="reverse_charge",
                    message=(
                        f"fakturaen er merket omvendt avgiftsplikt, så det beregnes ikke "
                        f"merverdiavgift — heller ikke på de {len(charged)} linjene som "
                        f"har en avgiftspliktig mva-kode"
                    ),
                    severity=Severity.ADVARSEL,
                )
            )

    if computed.payable == ZERO and not document.is_credit_note:
        found.append(
            Violation(
                rule="bokføringsforskriften § 5-1-1 nr. 5",
                field="lines",
                message="salgsdokumentet har ikke noe vederlag",
            )
        )
    return found


def _check_e_invoice(document: SalesDocument, *, today: date) -> list[Violation]:
    """The e-invoice requirement — lov 19. juni 2026 nr. 39.

    An EHF invoice is addressed by the buyer's organisasjonsnummer: it becomes
    the Peppol participant identifier under scheme 0192, and without it there is
    nowhere to send the document. So the check that matters is not "did we
    produce XML" — we always do — but whether the XML can be delivered.

    Consumers are outside the requirement entirely, which is why
    ``buyer_is_business`` is asked rather than inferred from a missing orgnr.
    """
    if not document.buyer_is_business or document.buyer_orgnr:
        return []
    mandatory = today >= E_INVOICE_MANDATORY_FROM
    return [
        Violation(
            rule="lov 19. juni 2026 nr. 39 (pliktig e-fakturering)",
            field="buyer_orgnr",
            message=(
                "kjøperen er bokføringspliktig, og da skal salgsdokumentet sendes som "
                "elektronisk faktura. Uten kjøperens organisasjonsnummer kan den ikke "
                "adresseres i Peppol"
                + ("" if mandatory else " — kravet gjelder fra 1. januar 2027")
            ),
            severity=Severity.BLOKKERENDE if mandatory else Severity.ADVARSEL,
        )
    ]


def describe_markings(document: SalesDocument) -> list[str]:
    """The sentences the document itself must carry, in print and in the XML.

    Collected in one function so the PDF and the EHF cannot disagree about what
    the document says — the § 5-1-1 nr. 7 marking in particular is prescribed
    wording, not a note the renderer is free to phrase.
    """
    markings: list[str] = []
    if document.seller_in_foretaksregisteret:
        markings.append(FORETAKSREGISTERET_MARKING)
    if document.reverse_charge:
        markings.append(REVERSE_CHARGE_MARKING)
    if document.document_type is DocumentType.KREDITNOTA:
        markings.append("Kreditnota")
    return markings

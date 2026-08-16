"""Refusals from the invoicing domain.

Same shape as :mod:`myntos.domain.ledger.errors`: meaning here, status codes in
:mod:`myntos.api.errors`, Norwegian messages because the person who has to act
on them is the one issuing the invoice.

The split worth noticing is between :class:`InvoiceNotCompliantError` and the
rest. The others are ordinary refusals — wrong id, wrong state, bad amount. That
one carries the list of :class:`~myntos.domain.faktura.compliance.Violation` the
document failed on, because "this invoice is not legal yet" is only useful to a
user together with *which requirement* is unmet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle only exists for type checkers
    from myntos.domain.faktura.compliance import Violation


class FakturaError(Exception):
    """Base class for every refusal from the invoicing domain."""


class InvoiceNotFoundError(FakturaError):
    """No such invoice in this company.

    The same error whether it never existed or belongs to another tenant, so the
    API cannot be used to probe for ids.
    """


class EhfNotAvailableError(FakturaError):
    """The document has no e-invoice, because it has not been issued.

    Distinct from :class:`InvoiceNotFoundError` even though both answer 404: the
    invoice exists and the caller may see it — it is the XML that does not exist
    yet, and a client that got ``invoice_not_found`` here would reasonably
    conclude the id was wrong.
    """


class InvoiceNotDraftError(FakturaError):
    """The invoice has been issued and can no longer be changed.

    Bokføringsforskriften § 5-2-7 is the reason there is no edit path: a issued
    sales document is corrected by a kreditnota that reverses it, never by
    rewriting what the customer already received.
    """


class InvoiceAlreadyIssuedError(FakturaError):
    """Issue was called on an invoice that already carries a number."""


class AlreadyCreditedError(FakturaError):
    """The invoice has already been reversed by a kreditnota."""


class NotCreditableError(FakturaError):
    """Only an issued faktura can be credited — not a draft, not a kreditnota."""


class InvoiceNotCompliantError(FakturaError):
    """The document does not meet bokføringsforskriften § 5-1.

    Raised on issue, never on save: a draft is allowed to be incomplete while it
    is being written. The checks run at the one moment the answer becomes
    binding.
    """

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        summary = "; ".join(v.message for v in violations)
        super().__init__(f"fakturaen mangler noe loven krever: {summary}")


class NoInvoiceLinesError(FakturaError):
    """An invoice with no lines has no art og omfang to document."""


class InvalidLineError(FakturaError):
    """A line's quantity, price or discount is not a usable number."""


class NotACustomerError(FakturaError):
    """The party on an invoice must be registered as a kunde, not a leverandør."""


class UnsupportedCurrencyError(FakturaError):
    """Only NOK is supported for now.

    Foreign-currency invoicing is legal, but § 5-1-1 nr. 6 requires the
    merverdiavgift to be restated in NOK on the document, and the ledger carries
    NOK only. Accepting a EUR invoice without that restatement would produce a
    document that looks fine and is not.
    """

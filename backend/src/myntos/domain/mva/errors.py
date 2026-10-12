"""Refusals from the MVA-melding.

Subclasses of :class:`~myntos.domain.ledger.errors.LedgerError`, so the handler
registered for the ledger renders them without a line of its own in
``main.py``; the status codes are in :mod:`myntos.api.errors` like every other.
Messages are Norwegian because the person filing is the one who has to act.
"""

from myntos.domain.ledger.errors import LedgerError


class MvaError(LedgerError):
    """Base class for every refusal about an MVA-melding."""


class InvalidPeriodError(MvaError):
    """The period named does not exist for this company's period length."""


class UnsupportedCategoryError(MvaError):
    """A melding category Myntos does not file."""


class NotVatRegisteredError(MvaError):
    """The company is not registered in Merverdiavgiftsregisteret.

    An unregistered business files no ordinary MVA-melding. Validating one
    anyway would send Skatteetaten a melding for an orgnr it has no VAT account
    for, and the answer would be about registration rather than about the
    figures.
    """


class PeriodNotOverError(MvaError):
    """The period has not ended, so its melding cannot be filed yet."""


class MeldingNotBuildableError(MvaError):
    """The ledger holds something the melding cannot express."""


class FilingNotFoundError(MvaError):
    """No such filing, or one belonging to another company."""


class FilingNotSubmittableError(MvaError):
    """The filing is not one that may be submitted: refused, or already sent."""


class MeldingChangedError(MvaError):
    """The ledger moved between validation and submission.

    The user confirmed the validated melding. Submitting a freshly built one
    that differs would file figures nobody looked at, so the answer is to
    validate again and look.
    """


class ConfirmationRequiredError(MvaError):
    """The submission was not confirmed against the figure the user saw."""


class SubmissionFailedError(MvaError):
    """Skatteetaten or Altinn refused, or could not be reached, mid-submission."""


class AdjustmentNotFoundError(MvaError):
    """No such uttak, tap på krav or justering in this company."""


class InvalidAdjustmentError(MvaError):
    """The spesifikasjon cannot be recorded with that code, amount or account."""

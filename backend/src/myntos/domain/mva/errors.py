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


class UnknownNaeringError(UnsupportedCategoryError):
    """The company files no melding of that category.

    A company files one melding, or two when it has a primærnæring and other
    business above 30 000 kroner beside it (docs/integrations/mva.md, «Two
    returns»). Asking for the other one before it is set up is asking for terms
    the company does not have.
    """


class FiledElsewhereError(FilingNotSubmittableError):
    """The term is marked as filed from another program.

    A second melding for a term *replaces* the first at Skatteetaten, so
    checking or sending one from Myntos for a term the previous program filed
    would overwrite that program's melding with whatever Myntos has, which
    during a parallel run is exactly what nobody wants. The mark is taken back
    first when a correction from Myntos is what the person means.
    """


class AlreadyFiledError(FilingNotSubmittableError):
    """The term was filed from Myntos, so it cannot be said to be filed elsewhere."""


class NotFiledElsewhereError(FilingNotSubmittableError):
    """Only a standing «filed from another program» statement can be taken back."""


class FiledLinesWouldMoveError(MeldingChangedError):
    """A change to the næringer would move VAT out of, or into, a term already filed.

    Which melding a line belongs to is read from its account and date when the
    melding is built, so moving an account to the other melding, moving the day
    the second melding starts, or swapping the categories would change what a
    filed (or filed-elsewhere) melding says after the fact. Refused rather than
    allowed with a warning: a posted voucher is history, and so is the melding
    that reported it. A line booked on the wrong account is corrected the way
    every posted line is, by a reversal and a new entry, and the correction then
    shows on the term as a change to file. A 409 like the other "the books moved"
    refusals, which is why it is a kind of :class:`MeldingChangedError`.
    """


class InvalidFilingDateError(InvalidPeriodError):
    """The day a term was filed from another program cannot be that day.

    A day in the future is a statement about something that has not happened.
    A 422 like an impossible period, which is what it is a kind of: a date the
    term cannot have.
    """

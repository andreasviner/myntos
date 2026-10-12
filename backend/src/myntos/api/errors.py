"""Mapping raised errors to HTTP responses.

The domain raises meaning ("this invitation is expired", "that year is
closed"); the status code is a transport detail decided here. One handler and
one table beats a try/except ladder in every endpoint, and it makes the whole
policy readable at once — including the deliberate choices, like every
credential failure being 401 with the same body so the response cannot be used
to enumerate accounts, and every "not yours" being 404 so ids cannot be probed.

Integration failures ride along in the same table. They are not domain errors —
an unreachable external register says nothing about our books — but they reach a
client the same way, and splitting them into a second mechanism would only mean
two places to look up how an error is rendered.

Response shape is ``{"detail": ..., "code": ...}``: ``detail`` is Norwegian
prose the frontend can show, ``code`` is the stable machine-readable name a
client should branch on.
"""

import logging
import re
from collections.abc import Awaitable, Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse

from myntos.auth.approvals import ApprovalHeld
from myntos.core.clock import as_utc
from myntos.core.config import get_settings
from myntos.domain.arsoppgjor.errors import (
    FinancialYearNotFoundError,
    InvalidOpeningBalanceError,
    InvalidSaldobalanseError,
    NoOpeningBalanceError,
    OpeningBalanceExistsError,
    OpeningBalanceNotAllowedError,
    ReopeningReasonRequiredError,
    YearNotClosableError,
    YearNotReopenableError,
)
from myntos.domain.bank.errors import (
    BankLineAlreadyMatchedError,
    BankLineNotFoundError,
    BankMatchMismatchError,
    BankStatementConflictError,
    BankStatementNotFoundError,
    InvalidBankStatementError,
    UnbalancedBankStatementError,
    UnknownStatementAccountError,
    UnsupportedStatementCurrencyError,
    VoucherAlreadyMatchedError,
)
from myntos.domain.bilag.errors import (
    AttachmentNotFoundError,
    DocumentTooLargeError,
    UnsupportedDocumentError,
)
from myntos.domain.faktura.errors import (
    AlreadyCreditedError,
    EhfNotAvailableError,
    FakturaError,
    InvalidLineError,
    InvoiceNotCompliantError,
    InvoiceNotDraftError,
    InvoiceNotFoundError,
    NotACustomerError,
    NotCreditableError,
    UnsupportedCurrencyError,
)
from myntos.domain.faktura.vat import UnsupportedSalesTaxCodeError
from myntos.domain.identity.errors import (
    AccountDisabledError,
    AccountLockedError,
    AccountMailRateLimitedError,
    AgentAccessDisabledError,
    AgentKeyNotFoundError,
    AgentKeyRequiredError,
    AgentRateLimitedError,
    AlreadyFiledError,
    ApprovalNotFoundError,
    ApprovalNotPendingError,
    ApprovalTooLargeError,
    AttachmentsNotAcceptedError,
    BankidAccountError,
    EmailAlreadyRegisteredError,
    EmailAlreadyVerifiedError,
    EmailDeliveryUnavailableError,
    ErasureBlockedError,
    FilingIsDoneOnThePageError,
    IdentityAlreadyLinkedError,
    IdentityError,
    IdentityNotFoundError,
    InvalidAgentScopeError,
    InvalidAgentTokenError,
    InvalidCredentialsError,
    InvalidEmailTokenError,
    InvalidGrantError,
    InvalidInvitationError,
    InvalidProviderSignupError,
    InvitationEmailMismatchError,
    InvitationNotFoundError,
    LastOwnerError,
    LastSignInMethodError,
    MissingScopeError,
    NoLinkedAccountError,
    NoPasswordSetError,
    NotAMemberError,
    NotAnAccessTokenError,
    NotForAgentKeysError,
    NotYourAgentError,
    PermissionDeniedError,
    ReauthenticationFailedError,
    ReauthenticationRequiredError,
    SessionNotFoundError,
    UnverifiedProviderEmailError,
    WeakPasswordError,
)
from myntos.domain.ledger.errors import (
    AccountInUseError,
    AccountNotFoundError,
    AlreadyReversedError,
    BankAccountNotFoundError,
    CannotReverseHereError,
    ChartIncompleteError,
    ClosedFinancialYearError,
    CompanyAlreadyClaimedError,
    CompanyNotFoundError,
    CompanyOrgnrLockedError,
    DuplicateAccountError,
    DuplicateBankAccountError,
    DuplicateCompanyError,
    DuplicatePartyError,
    InactiveAccountError,
    InvalidAccountNumberError,
    InvalidAmountError,
    InvalidBankAccountError,
    InvalidCompositeEntryError,
    InvalidGroupingError,
    InvalidOrgnrError,
    LedgerError,
    NotAuthorizedForPartyError,
    PartyNotFoundError,
    PeriodOutsideBooksError,
    PrimaryBankAccountRequiredError,
    SegmentNotEnabledError,
    TaxCodeNotAllowedError,
    UnknownCashFlowTypeError,
    UnknownTaxCodeError,
    VoucherNotFoundError,
)
from myntos.domain.mva.errors import (
    AdjustmentNotFoundError,
    ConfirmationRequiredError,
    FilingNotFoundError,
    FilingNotSubmittableError,
    InvalidAdjustmentError,
    InvalidPeriodError,
    MeldingChangedError,
    MeldingNotBuildableError,
    NotVatRegisteredError,
    PeriodNotOverError,
    SubmissionFailedError,
    UnsupportedCategoryError,
)
from myntos.integrations.brreg import (
    BrregError,
    RegisterUnavailableError,
    UnitDeletedError,
    UnitNotFoundError,
)
from myntos.saft.errors import SaftYearNotFoundError, UnsupportedSaftVersionError

# Errors this module knows how to render. Every hierarchy is handled the same
# way; keeping one table means a new error cannot be given a status code in one
# place and forgotten in another.
HandledError = IdentityError | LedgerError | FakturaError | BrregError

_STATUS_BY_ERROR: dict[type[Exception], int] = {
    # --- Identity ---
    InvalidCredentialsError: status.HTTP_401_UNAUTHORIZED,
    AccountLockedError: status.HTTP_429_TOO_MANY_REQUESTS,
    AccountDisabledError: status.HTTP_403_FORBIDDEN,
    # 404, not 403: a non-member must not learn whether the organization exists.
    NotAMemberError: status.HTTP_404_NOT_FOUND,
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    LastOwnerError: status.HTTP_409_CONFLICT,
    EmailAlreadyRegisteredError: status.HTTP_409_CONFLICT,
    WeakPasswordError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    NoPasswordSetError: status.HTTP_409_CONFLICT,
    UnverifiedProviderEmailError: status.HTTP_403_FORBIDDEN,
    # Only ever a marker on a redirect from the callback, never a JSON body,
    # but mapped like its siblings so a future caller cannot make it a 500.
    NoLinkedAccountError: status.HTTP_403_FORBIDDEN,
    IdentityAlreadyLinkedError: status.HTTP_409_CONFLICT,
    # 404 like every other thing that is not yours to name.
    IdentityNotFoundError: status.HTTP_404_NOT_FOUND,
    LastSignInMethodError: status.HTTP_409_CONFLICT,
    # 409: the request is well-formed, and what refuses it is how the account
    # signs in. Its subclass for a Google or Microsoft login inherits this, and
    # is only ever a marker on a redirect anyway.
    BankidAccountError: status.HTTP_409_CONFLICT,
    # 400, like every other unusable token.
    InvalidProviderSignupError: status.HTTP_400_BAD_REQUEST,
    InvalidInvitationError: status.HTTP_400_BAD_REQUEST,
    # 404, like every other record that belongs to another company: naming
    # an id you do not have must not confirm that somebody else does.
    InvitationNotFoundError: status.HTTP_404_NOT_FOUND,
    InvitationEmailMismatchError: status.HTTP_403_FORBIDDEN,
    # 404 for somebody else's session, like every other id that is not yours.
    SessionNotFoundError: status.HTTP_404_NOT_FOUND,
    # 403 and never 401, for either: the caller is signed in, and a 401 makes
    # the frontend send them to the login page with the deletion half done.
    ReauthenticationRequiredError: status.HTTP_403_FORBIDDEN,
    ReauthenticationFailedError: status.HTTP_403_FORBIDDEN,
    # 409: well-formed, and refused by the state of the books the person owns.
    ErasureBlockedError: status.HTTP_409_CONFLICT,
    # 400 for every unusable confirmation or reset link, like an invitation.
    InvalidEmailTokenError: status.HTTP_400_BAD_REQUEST,
    EmailAlreadyVerifiedError: status.HTTP_409_CONFLICT,
    # 429, the status the login lockout uses: the same per-account throttle,
    # on the mail side.
    AccountMailRateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    # 503: the request is fine and so is the account; this deployment simply
    # has no way to deliver a link.
    EmailDeliveryUnavailableError: status.HTTP_503_SERVICE_UNAVAILABLE,
    # --- Keys for agents and integrations ---
    # 401 only for the two an agent fixes by itself: refresh an expired access
    # token, exchange a connection key it sent too early. Everything else is a
    # refusal the agent has to report rather than retry.
    InvalidAgentTokenError: status.HTTP_401_UNAUTHORIZED,
    NotAnAccessTokenError: status.HTTP_401_UNAUTHORIZED,
    # 400 for every unusable grant, as OAuth's token endpoint answers; the
    # subclasses keep their own codes, which is what says whether to refresh,
    # ask for a new key, or give up.
    InvalidGrantError: status.HTTP_400_BAD_REQUEST,
    AgentAccessDisabledError: status.HTTP_403_FORBIDDEN,
    NotForAgentKeysError: status.HTTP_403_FORBIDDEN,
    MissingScopeError: status.HTTP_403_FORBIDDEN,
    AgentKeyRequiredError: status.HTTP_403_FORBIDDEN,
    InvalidAgentScopeError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # 404 like every other record that is not yours to name.
    AgentKeyNotFoundError: status.HTTP_404_NOT_FOUND,
    AgentRateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    # --- Requests that wait for a person's approval ---
    ApprovalNotFoundError: status.HTTP_404_NOT_FOUND,
    # 409: well-formed, and refused by the state the request is already in.
    ApprovalNotPendingError: status.HTTP_409_CONFLICT,
    NotYourAgentError: status.HTTP_403_FORBIDDEN,
    ApprovalTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    AttachmentsNotAcceptedError: status.HTTP_409_CONFLICT,
    FilingIsDoneOnThePageError: status.HTTP_409_CONFLICT,
    AlreadyFiledError: status.HTTP_409_CONFLICT,
    # --- Accounting core ---
    # Same reasoning as NotAMemberError: another organization's company is
    # indistinguishable from one that does not exist.
    CompanyNotFoundError: status.HTTP_404_NOT_FOUND,
    VoucherNotFoundError: status.HTTP_404_NOT_FOUND,
    AttachmentNotFoundError: status.HTTP_404_NOT_FOUND,
    PartyNotFoundError: status.HTTP_404_NOT_FOUND,
    # 404 for the same reason as every other sub-resource: an account belonging
    # to the company next door is indistinguishable from one that never existed.
    BankAccountNotFoundError: status.HTTP_404_NOT_FOUND,
    DuplicateCompanyError: status.HTTP_409_CONFLICT,
    DuplicatePartyError: status.HTTP_409_CONFLICT,
    # 409: the number is a perfectly good one, the company simply already has it.
    DuplicateBankAccountError: status.HTTP_409_CONFLICT,
    # 409: well-formed, and refused by the state the company is in — the way to
    # move the primary marking is to promote another account, not to clear this.
    PrimaryBankAccountRequiredError: status.HTTP_409_CONFLICT,
    # 409: the request was well-formed and the caller may well be entitled to
    # these books — another organization simply confirmed the same orgnr first.
    # The frontend branches on the code to offer the way out, so this one must
    # stay distinguishable from DuplicateCompanyError.
    CompanyAlreadyClaimedError: status.HTTP_409_CONFLICT,
    # 409 for the same reason: the request is well-formed, and what refuses it
    # is the state the company is already in. Confirming ownership settles the
    # orgnr, so the only way past this is a new set of books.
    CompanyOrgnrLockedError: status.HTTP_409_CONFLICT,
    # 403, not 404: the company is one the caller can see, and what is missing
    # is the right to act for it in Altinn.
    NotAuthorizedForPartyError: status.HTTP_403_FORBIDDEN,
    # 500: the company's kontoplan is missing an account we seed, which is our
    # problem to fix and not something the user can act on.
    ChartIncompleteError: status.HTTP_500_INTERNAL_SERVER_ERROR,
    ClosedFinancialYearError: status.HTTP_409_CONFLICT,
    AlreadyReversedError: status.HTTP_409_CONFLICT,
    InvalidOrgnrError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidBankAccountError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidAmountError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnknownCashFlowTypeError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnknownTaxCodeError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    PeriodOutsideBooksError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnsupportedDocumentError: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    DocumentTooLargeError: status.HTTP_413_CONTENT_TOO_LARGE,
    # --- Bank import ---
    # 404 for a statement or a line belonging to the company next door, for the
    # reason every other sub-resource gives.
    BankStatementNotFoundError: status.HTTP_404_NOT_FOUND,
    BankLineNotFoundError: status.HTTP_404_NOT_FOUND,
    # 422: the file is well-formed HTTP and refused for what it says. Each has
    # its own code, because the page answers each one differently: an unknown
    # account links to /organisasjon/#selskap, an unbalanced file asks for a
    # fresh download.
    InvalidBankStatementError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnbalancedBankStatementError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnsupportedStatementCurrencyError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnknownStatementAccountError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    BankMatchMismatchError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # 409: refused by what is already stored, not by the request itself.
    BankStatementConflictError: status.HTTP_409_CONFLICT,
    BankLineAlreadyMatchedError: status.HTTP_409_CONFLICT,
    VoucherAlreadyMatchedError: status.HTTP_409_CONFLICT,
    # --- Kontoplan ---
    # 404 like every sub-resource: another company's account does not exist.
    AccountNotFoundError: status.HTTP_404_NOT_FOUND,
    DuplicateAccountError: status.HTTP_409_CONFLICT,
    # 409: a well-formed request refused by what the account already carries
    # (history, or being one Myntos refers to by number).
    AccountInUseError: status.HTTP_409_CONFLICT,
    # 409: the account exists and is switched off; switching it on is the fix.
    InactiveAccountError: status.HTTP_409_CONFLICT,
    InvalidAccountNumberError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidGroupingError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # 422: the code is real, and this company may not use it on that date.
    TaxCodeNotAllowedError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidCompositeEntryError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # 409: the category exists, and these books have not switched on the
    # add-on it belongs to. A setting stands in the way, not the request.
    SegmentNotEnabledError: status.HTTP_409_CONFLICT,
    # --- Fakturering ---
    InvoiceNotFoundError: status.HTTP_404_NOT_FOUND,
    # 404 on the XML resource, not on the invoice: the document exists, the
    # e-invoice does not exist yet.
    EhfNotAvailableError: status.HTTP_404_NOT_FOUND,
    # 409: the request was well-formed and the document is simply past the point
    # where it could be changed or credited again.
    InvoiceNotDraftError: status.HTTP_409_CONFLICT,
    AlreadyCreditedError: status.HTTP_409_CONFLICT,
    NotCreditableError: status.HTTP_409_CONFLICT,
    # 422: the invoice does not meet bokføringsforskriften § 5-1. The body
    # carries the prose; the interface shows the per-field list from
    # /faktura/{id}/kontroll rather than parsing this one.
    InvoiceNotCompliantError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidLineError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    NotACustomerError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnsupportedCurrencyError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnsupportedSalesTaxCodeError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # --- MVA-melding ---
    # 404 like every other sub-resource: another company's filing does not exist.
    FilingNotFoundError: status.HTTP_404_NOT_FOUND,
    AdjustmentNotFoundError: status.HTTP_404_NOT_FOUND,
    InvalidAdjustmentError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidPeriodError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    UnsupportedCategoryError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # 422: the ledger holds a code the melding cannot carry. The person has to
    # change a voucher, not retry.
    MeldingNotBuildableError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ConfirmationRequiredError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # 409 for the states that refuse a well-formed request: not registered, the
    # period still running, a filing past the point of sending, and a ledger
    # that moved after the check. The last one is the important one: the
    # answer is to look again, and the frontend branches on its code to say so.
    NotVatRegisteredError: status.HTTP_409_CONFLICT,
    PeriodNotOverError: status.HTTP_409_CONFLICT,
    FilingNotSubmittableError: status.HTTP_409_CONFLICT,
    MeldingChangedError: status.HTTP_409_CONFLICT,
    # 502: Skatteetaten or Altinn refused or could not be reached.
    SubmissionFailedError: status.HTTP_502_BAD_GATEWAY,
    # --- SAF-T ---
    # 404: the year is a thing the company does or does not have, like a filing.
    SaftYearNotFoundError: status.HTTP_404_NOT_FOUND,
    UnsupportedSaftVersionError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # --- Inngående balanse og årsavslutning ---
    FinancialYearNotFoundError: status.HTTP_404_NOT_FOUND,
    # 409 for every refusal that comes from the state the books are in rather
    # than from what was sent: a year that is closed or not over, an opening
    # balance already standing, a voucher that is corrected elsewhere.
    OpeningBalanceNotAllowedError: status.HTTP_409_CONFLICT,
    OpeningBalanceExistsError: status.HTTP_409_CONFLICT,
    NoOpeningBalanceError: status.HTTP_409_CONFLICT,
    YearNotReopenableError: status.HTTP_409_CONFLICT,
    CannotReverseHereError: status.HTTP_409_CONFLICT,
    # 422: what was sent cannot be posted as it is. The two carrying findings
    # are answered by their routes with the list rather than through here.
    InvalidOpeningBalanceError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    YearNotClosableError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    InvalidSaldobalanseError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    ReopeningReasonRequiredError: status.HTTP_422_UNPROCESSABLE_CONTENT,
    # --- Enhetsregisteret ---
    UnitNotFoundError: status.HTTP_404_NOT_FOUND,
    UnitDeletedError: status.HTTP_410_GONE,
    # 502, not 500: the request was fine and we are fine — the register we asked
    # on the caller's behalf is not. The distinction decides whether an alert
    # about this is ours to act on.
    RegisterUnavailableError: status.HTTP_502_BAD_GATEWAY,
}

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")

_logger = logging.getLogger(__name__)


def error_code(error: type[Exception]) -> str:
    """``InvalidInvitationError`` → ``invalid_invitation``."""
    name = error.__name__.removesuffix("Error")
    return _CAMEL_BOUNDARY.sub("_", name).lower()


def status_for(error: Exception) -> int:
    """The status code for ``error``, walking up its base classes.

    Walking the MRO means a new subclass inherits a sensible code instead of
    silently becoming a 500 — and 400 is the fallback for anything genuinely
    unmapped, because every error in these hierarchies describes a rejected
    request.
    """
    for candidate in type(error).__mro__:
        if candidate in _STATUS_BY_ERROR:
            return _STATUS_BY_ERROR[candidate]
    return status.HTTP_400_BAD_REQUEST


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render a handled error as JSON."""
    if not isinstance(exc, HandledError):  # pragma: no cover - registered per type
        raise exc
    status_code = status_for(exc)
    response = JSONResponse(
        status_code=status_code,
        content={"detail": str(exc), "code": error_code(type(exc))},
    )
    if status_code == status.HTTP_401_UNAUTHORIZED:
        # RFC 9110: a 401 must say how to authenticate. "Bearer" is the machine
        # answer; browsers get a cookie and the frontend's login form.
        response.headers["WWW-Authenticate"] = "Bearer"
    if isinstance(exc, AgentRateLimitedError):
        # The per-key limit counts a rolling minute, so a minute is always
        # enough. Said in the header an HTTP client already knows to read,
        # because the agent on the other end may not read the detail at all.
        response.headers["Retry-After"] = "60"
    return response


async def approval_held_handler(request: Request, exc: Exception) -> JSONResponse:
    """Answer a request that was stored to wait for its key's person: 202 and where to look.

    Not an error, but raised like one, because the gate that stores it is a
    dependency and cannot answer any other way (myntos.auth.approvals). The
    ``code`` is what an agent branches on, as on a refusal, and ``Location``
    is the route that says how it went, as RFC 9110 suggests for a 202.
    """
    if not isinstance(exc, ApprovalHeld):  # pragma: no cover - registered for this type
        raise exc
    base = get_settings().frontend_base_url.rstrip("/")
    location = f"/api/v1/companies/{exc.company_id}/godkjenninger/{exc.approval_id}"
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        headers={"Location": location},
        content={
            "code": "approval_pending",
            "detail": (
                "Dette er ikke gjort ennå. Det venter på at personen godkjenner det under "
                "Godkjenning i Myntos. Ikke send det på nytt; spør etter status på adressen i "
                "Location."
            ),
            "approval_id": exc.approval_id,
            "status": "pending",
            "expires_at": as_utc(exc.expires_at).isoformat(),
            "url": f"{base}/godkjenning/#godkjenning-{exc.approval_id}",
        },
    )


async def unhandled_error_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Turn any unhandled exception into a 500 *response*, rather than letting it
    escape the middleware stack.

    Without this, an exception nothing above has claimed propagates all the way
    out to Starlette's ``ServerErrorMiddleware``, which is installed outside
    every middleware we add — including ``CORSMiddleware``. Its bare
    ``Internal Server Error`` therefore carries no ``Access-Control-Allow-Origin``,
    and the browser refuses to show the page a 500 at all: the fetch rejects with
    "No 'Access-Control-Allow-Origin' header is present", which reads as a
    misconfigured allow-list and sends you looking in entirely the wrong place.

    Answering here — inside ``CORSMiddleware`` — means the failure is a response
    like any other and picks up the same headers, so the frontend sees the 500 it
    actually got. This must stay the innermost middleware registered in
    :func:`myntos.main.create_app` for that to hold.

    ``ServerErrorMiddleware`` would also have logged the traceback, so we do it
    here; nothing else will.
    """
    try:
        return await call_next(request)
    except Exception:
        _logger.exception("unhandled error on %s %s", request.method, request.url.path)
        # Deliberately opaque: the caller gets no exception text, only that the
        # fault was ours. The traceback is in the logs, where it belongs.
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Noe gikk galt hos oss.", "code": "internal_error"},
        )

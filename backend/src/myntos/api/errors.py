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
    EmailAlreadyRegisteredError,
    IdentityError,
    InvalidCredentialsError,
    InvalidInvitationError,
    InvitationEmailMismatchError,
    LastOwnerError,
    NoPasswordSetError,
    NotAMemberError,
    PermissionDeniedError,
    UnverifiedProviderEmailError,
    WeakPasswordError,
)
from myntos.domain.ledger.errors import (
    AlreadyReversedError,
    ChartIncompleteError,
    ClosedFinancialYearError,
    CompanyNotFoundError,
    DuplicateCompanyError,
    DuplicatePartyError,
    InvalidAmountError,
    InvalidBankAccountError,
    InvalidOrgnrError,
    LedgerError,
    PartyNotFoundError,
    PeriodOutsideBooksError,
    UnknownCashFlowTypeError,
    UnknownTaxCodeError,
    VoucherNotFoundError,
)
from myntos.integrations.brreg import (
    BrregError,
    RegisterUnavailableError,
    UnitDeletedError,
    UnitNotFoundError,
)

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
    InvalidInvitationError: status.HTTP_400_BAD_REQUEST,
    InvitationEmailMismatchError: status.HTTP_403_FORBIDDEN,
    # --- Accounting core ---
    # Same reasoning as NotAMemberError: another organization's company is
    # indistinguishable from one that does not exist.
    CompanyNotFoundError: status.HTTP_404_NOT_FOUND,
    VoucherNotFoundError: status.HTTP_404_NOT_FOUND,
    AttachmentNotFoundError: status.HTTP_404_NOT_FOUND,
    PartyNotFoundError: status.HTTP_404_NOT_FOUND,
    DuplicateCompanyError: status.HTTP_409_CONFLICT,
    DuplicatePartyError: status.HTTP_409_CONFLICT,
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
    return response


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

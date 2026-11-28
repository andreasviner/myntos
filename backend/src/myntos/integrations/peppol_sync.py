"""Keeping Myntos in step with Ademico: a webhook first, a sweep behind it.

Ademico reports two kinds of event for every company under this deployment's
credentials at once: an outgoing EHF was delivered (or failed, or the buyer
answered), and an incoming EHF arrived for a company that receives. Until
2026-11-25 Myntos asked for them every minute (``GET …/notifications``). Now
Ademico pushes each event to ``POST /api/v1/webhooks/ademico`` as it happens,
and a slow sweep catches whatever no push brought.

**A push is a hint, never a fact.** Ademico cannot sign a webhook body; all it
can send is the credentials Myntos gave it (Basic Auth, ``ADEMICO_WEBHOOK_SECRET``).
So a push only decides *what to look at*. The receiver, sender, type and
status of every pushed document are read back from Ademico with the
deployment's own credentials (``GET …/documents``) before anything is stored,
the stance Mollie's webhook takes too (myntos.api.v1.ki.mollie_webhook).
Routing by the push's own ``receiver`` would let one forged field put one
company's supplier invoice in another company's inbox; read back, a forged
push can at most make Myntos fetch a document Ademico already holds for this
deployment and file it where Ademico says it belongs.

**The sweep is not optional.** Ademico documents neither the body it pushes,
nor whether it retries a push that failed, nor whether a pushed event also
stays in the pull queue, and every restart (``update.sh``) is a window in
which a push meets a closed port. So every ``PEPPOL_SWEEP_INTERVAL_SECONDS``,
and on ``POST …/peppol/synkroniser``, :func:`sync_once`

1. drains the pull queue as the poller did, which finds everything if Ademico
   keeps a copy there and nothing if it does not, and
2. checks Ademico's document list itself, whatever the queue holds: every
   invoice or credit note received in the last week that Myntos has not
   stored, and every outgoing send still pending.

A background sweep that finds anything at all is a webhook that did not
deliver, so once a webhook secret is set it says so at ERROR, where the
operator sees it (docs/operator.md).

Crash safety is the poller's: every database change is committed before the
notification is consumed (``DELETE …/notifications/{id}``), and a received
document is stored at most once per transmissionId, so the push, the queue and
the sweep may all see the same document.
"""

from __future__ import annotations

import argparse
import asyncio
import enum
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.domain.peppol import service as peppol_service
from myntos.domain.peppol.models import TransmissionStatus
from myntos.domain.peppol.ubl_reader import InvalidEhfXmlError
from myntos.integrations import ademico

logger = logging.getLogger(__name__)

_PAGE_SIZE = 100
_MAX_QUEUE_PAGES = 5
_MAX_RECEIVED_PAGES = 10
_INCOMING_DOC_TYPES = frozenset({"INVOICE", "CREDIT_NOTE"})
_SENDING_EVENTS = frozenset(
    {"DOCUMENT_SENT", "DOCUMENT_SEND_FAILED", "INVOICE_RESPONSE_RECEIVED", "MLR_RECEIVED"}
)
# Events that only happen once a send has settled one way or the other. A buyer's
# answer may leave a send pending (an invoice under query); these may not.
_SETTLING_EVENTS = frozenset({"DOCUMENT_SENT", "DOCUMENT_SEND_FAILED"})
_FIRST_SWEEP_DELAY_SECONDS = 15
# How far back the sweep looks for received documents no push brought. A week
# covers a long weekend with the backend down; a document missed for longer
# than that was missed by more than a webhook, and is the operator's to find.
_RECEIVED_WINDOW = timedelta(days=7)
# Outgoing sends the sweep asks Ademico about. Younger than the first are left
# to the push that is probably on its way (and the invoice page asks for itself
# when it is opened); older than the second will not be settled by asking again.
_PENDING_SETTLE = timedelta(minutes=2)
_PENDING_WINDOW = timedelta(days=30)
_MAX_PENDING_CHECKS = 100


@dataclass(frozen=True)
class SyncSummary:
    """Counts from one sweep, safe to return and log (no invoice data).

    Per-company breakdowns let ``POST …/peppol/synkroniser`` answer only with
    counts for the caller's own company, never revealing another tenant's
    activity even as an integer.
    """

    received: int = 0
    deliveries_updated: int = 0
    consumed: int = 0
    skipped: int = 0
    received_by_company: dict[int, int] = field(default_factory=dict)
    deliveries_updated_by_company: dict[int, int] = field(default_factory=dict)

    def received_for(self, company_id: int) -> int:
        return self.received_by_company.get(company_id, 0)

    def deliveries_updated_for(self, company_id: int) -> int:
        return self.deliveries_updated_by_company.get(company_id, 0)


@dataclass(frozen=True)
class PushResult:
    """What became of one webhook request. ``retry`` asks Ademico to push it again."""

    handled: int = 0
    retry: bool = False


class _Received(enum.Enum):
    STORED = "stored"
    # Stored before, by a push, the queue or the sweep.
    ALREADY = "already"
    # A document type Myntos does not receive (an order, say). No retry changes that.
    NOT_OURS = "not_ours"
    # No active Myntos company receives for that address. Left where it is:
    # the company may be turning receiving on right now.
    NO_INBOX = "no_inbox"
    # The XML did not parse. Left where it is, for the operator.
    UNREADABLE = "unreadable"


class _Outcome(enum.Enum):
    # Settled: consume the notification if it has an id.
    DONE = "done"
    # Not settled, and no push will settle it; the sweep keeps looking.
    LEAVE = "leave"
    # Not settled yet; ask Ademico to push it again.
    RETRY = "retry"


_OUTCOME_OF_RECEIVED = {
    _Received.STORED: _Outcome.DONE,
    _Received.ALREADY: _Outcome.DONE,
    _Received.NOT_OURS: _Outcome.DONE,
    _Received.NO_INBOX: _Outcome.LEAVE,
    _Received.UNREADABLE: _Outcome.LEAVE,
}


@dataclass(frozen=True)
class _Incoming:
    """A received document, whichever of the three channels named it."""

    transmission_id: str
    document_type: str | None
    receiver: str | None
    sender: str | None
    sbdh_transmission_id: str | None
    received_at: datetime | None

    @classmethod
    def from_record(cls, record: ademico.DocumentRecord) -> _Incoming:
        return cls(
            transmission_id=record.transmission_id,
            document_type=record.document_type,
            receiver=record.receiver,
            sender=record.sender,
            sbdh_transmission_id=record.sbdh_transmission_id,
            received_at=record.created_at,
        )

    @classmethod
    def from_queue(cls, notification: ademico.Notification, transmission_id: str) -> _Incoming:
        # The queue is read with the deployment's credentials, so what it says
        # is Ademico's own word and needs no reading back.
        return cls(
            transmission_id=transmission_id,
            document_type=notification.document_type,
            receiver=notification.receiver,
            sender=notification.sender,
            sbdh_transmission_id=notification.sbdh_transmission_id,
            received_at=notification.received_at or notification.notification_at,
        )


async def _store_received(
    sessions: async_sessionmaker[AsyncSession], incoming: _Incoming
) -> tuple[_Received, int | None]:
    """Store one received invoice or credit note; return what happened and for whom."""
    if incoming.document_type not in _INCOMING_DOC_TYPES:
        logger.info(
            "Skipping unsupported incoming Peppol document type=%s",
            incoming.document_type or "unknown",
        )
        return _Received.NOT_OURS, None

    async with sessions() as db:
        # Before the inbox lookup and before downloading anything, so a
        # document seen again (pushed twice, then swept) costs one query.
        if await peppol_service.incoming_exists(db, transmission_id=incoming.transmission_id):
            return _Received.ALREADY, None
        orgnr = ademico.parse_norwegian_orgnr(incoming.receiver)
        if orgnr is None:
            logger.warning("Incoming Peppol document has non-Norwegian receiver scheme")
            return _Received.NO_INBOX, None
        company = await peppol_service.find_receiving_company(db, orgnr=orgnr)
        if company is None:
            logger.warning("Incoming Peppol document for orgnr with no active Myntos inbox")
            return _Received.NO_INBOX, None
        company_id = company.id

    raw_xml = await ademico.get_invoice_ubl(incoming.transmission_id)

    async with sessions() as db:
        # Asked again: receiving may have been switched off during the download.
        company = await peppol_service.find_receiving_company(db, orgnr=orgnr)
        if company is None or company.id != company_id:
            return _Received.NO_INBOX, None
        try:
            _, created = await peppol_service.store_incoming(
                db,
                company=company,
                transmission_id=incoming.transmission_id,
                sbdh_transmission_id=incoming.sbdh_transmission_id,
                raw_xml=raw_xml,
                received_at=incoming.received_at or utc_now(),
                fallback_sender=incoming.sender or "",
            )
            await db.commit()
        except InvalidEhfXmlError:
            logger.error("Received Peppol document failed XML parsing")
            return _Received.UNREADABLE, None
        except IntegrityError:
            # A push and the sweep stored the same document at the same moment.
            # The unique constraint kept one copy, which is the one we wanted.
            await db.rollback()
            return _Received.ALREADY, company_id
    return (_Received.STORED if created else _Received.ALREADY), company_id


async def _apply_delivery(
    sessions: async_sessionmaker[AsyncSession], transmission_id: str, delivery: ademico.Delivery
) -> int | None:
    """Record where a send stands; the company's id if that changed, else ``None``."""
    async with sessions() as db:
        transmission = await peppol_service.find_transmission_by_remote_id(
            db, remote_document_id=transmission_id
        )
        if transmission is None:
            # Sent outside Myntos or unknown to this database.
            return None
        company_id = transmission.company_id
        status = TransmissionStatus(delivery.status)
        changed = transmission.status != status
        await peppol_service.record_delivery(
            db,
            transmission,
            status=status,
            failure_category=delivery.failure_category,
            failure_message=delivery.failure_message,
        )
        await db.commit()
    return company_id if changed else None


async def _is_our_send(sessions: async_sessionmaker[AsyncSession], transmission_id: str) -> bool:
    async with sessions() as db:
        found = await peppol_service.find_transmission_by_remote_id(
            db, remote_document_id=transmission_id
        )
    return found is not None


# --- The webhook ---------------------------------------------------------------------


async def _handle_pushed(
    sessions: async_sessionmaker[AsyncSession], hint: ademico.Notification
) -> _Outcome:
    """Act on one pushed notification, believing nothing in it but which document it names."""
    if not hint.transmission_id:
        return _Outcome.DONE

    if hint.event_type == "DOCUMENT_RECEIVED":
        record = await ademico.find_document(hint.transmission_id, direction="RECEIVING")
        if record is None:
            # Pushed before Ademico's own list shows it, or no document of this
            # deployment at all. Either way nothing is stored; a second push or
            # the sweep settles which.
            return _Outcome.RETRY
        received, _ = await _store_received(sessions, _Incoming.from_record(record))
        return _OUTCOME_OF_RECEIVED[received]

    if hint.event_type in _SENDING_EVENTS:
        # Our own table first: a push about a send Myntos never made costs no
        # call to Ademico, whoever sent it.
        if not await _is_our_send(sessions, hint.transmission_id):
            return _Outcome.DONE
        record = await ademico.find_document(hint.transmission_id, direction="SENDING")
        if record is None:
            return _Outcome.RETRY
        # The status is Ademico's; the push's details only explain a failure
        # Ademico itself reports, as the text the seller reads.
        delivery = ademico.delivery_from_status(record.document_status, details=hint.details)
        if delivery.status == "pending" and hint.event_type in _SETTLING_EVENTS:
            # Ademico's own record has not caught up with its push. Settled
            # now, the sweep would later make the change and report it as a
            # webhook that failed, when it was only a race.
            return _Outcome.RETRY
        await _apply_delivery(sessions, hint.transmission_id, delivery)
        return _Outcome.DONE

    # Events Myntos does not act on (orders, receive failures, other countries'
    # reporting). Consumed so they never pile up in the queue.
    logger.info("Ignoring pushed Ademico event=%s", hint.event_type)
    return _Outcome.DONE


async def _consume_quietly(notification_id: int) -> None:
    """Take a handled event off the pull queue, if Ademico keeps one there.

    Whether a pushed event also sits in the queue is not documented. A 404 is
    the answer that it does not; any other failure leaves it for the sweep,
    which will find it already stored and consume it then.
    """
    try:
        await ademico.consume_notification(notification_id)
    except ademico.AdemicoNotFoundError:
        pass
    except ademico.AdemicoError:
        logger.info("Could not consume a pushed Ademico notification; the sweep will")


async def handle_push(
    sessions: async_sessionmaker[AsyncSession], notifications: list[ademico.Notification]
) -> PushResult:
    """Handle the notifications in one webhook body."""
    handled = 0
    retry = False
    for hint in notifications:
        try:
            outcome = await _handle_pushed(sessions, hint)
        except ademico.AdemicoError:
            logger.exception("Ademico could not confirm a pushed event=%s", hint.event_type)
            retry = True
            continue
        if outcome is _Outcome.RETRY:
            retry = True
            continue
        handled += 1
        if outcome is _Outcome.DONE and hint.notification_id is not None:
            await _consume_quietly(hint.notification_id)
    return PushResult(handled=handled, retry=retry)


# --- The sweep -----------------------------------------------------------------------


@dataclass
class _Tally:
    received: Counter[int] = field(default_factory=Counter)
    deliveries: Counter[int] = field(default_factory=Counter)
    consumed: int = 0
    skipped: int = 0


async def _handle_queued(
    sessions: async_sessionmaker[AsyncSession], item: ademico.Notification, tally: _Tally
) -> bool:
    """Act on one queued notification; whether to consume it."""
    if item.event_type == "DOCUMENT_RECEIVED":
        if not item.transmission_id:
            return True
        received, company_id = await _store_received(
            sessions, _Incoming.from_queue(item, item.transmission_id)
        )
        if received is _Received.STORED and company_id is not None:
            tally.received[company_id] += 1
        return _OUTCOME_OF_RECEIVED[received] is _Outcome.DONE
    if item.event_type in _SENDING_EVENTS:
        delivery = ademico.delivery_from_notification(item)
        if delivery is not None and item.transmission_id:
            company_id = await _apply_delivery(sessions, item.transmission_id, delivery)
            if company_id is not None:
                tally.deliveries[company_id] += 1
        return True
    # Unrelated or receive-failure events: logged without data and consumed so
    # they never block the queue.
    logger.info("Consuming unhandled Ademico notification event=%s", item.event_type)
    return True


async def _drain_queue(sessions: async_sessionmaker[AsyncSession], tally: _Tally) -> None:
    """Everything waiting in ``GET …/notifications``, all companies at once."""
    seen_ids: set[int] = set()
    page = 0
    for _ in range(_MAX_QUEUE_PAGES):
        items, total = await ademico.list_notifications(page=page, page_size=_PAGE_SIZE)
        if not items:
            break
        # The queue never lists a notification without an id; the type allows
        # one only because a pushed body might not carry it.
        fresh = [
            (item.notification_id, item)
            for item in items
            if item.notification_id is not None and item.notification_id not in seen_ids
        ]
        consumed_on_page = 0
        for notification_id, item in fresh:
            seen_ids.add(notification_id)
            try:
                should_consume = await _handle_queued(sessions, item, tally)
            except ademico.AdemicoError:
                logger.exception(
                    "Ademico error while processing notification event=%s", item.event_type
                )
                tally.skipped += 1
                continue
            if not should_consume:
                tally.skipped += 1
                continue
            try:
                await ademico.consume_notification(notification_id)
            except ademico.AdemicoError:
                logger.exception("Could not consume Ademico notification after commit")
                tally.skipped += 1
                continue
            tally.consumed += 1
            consumed_on_page += 1

        if (page + 1) * _PAGE_SIZE >= total:
            break
        # Consuming deletes items from the queue and shifts later ones forward,
        # so the page only advances when nothing on it was consumed.
        if consumed_on_page == 0:
            page += 1


async def _sweep_received(sessions: async_sessionmaker[AsyncSession], tally: _Tally) -> None:
    """Every invoice and credit note Ademico received this week that Myntos has not stored."""
    since = utc_now() - _RECEIVED_WINDOW
    page = 0
    for _ in range(_MAX_RECEIVED_PAGES):
        records, total = await ademico.list_documents(
            direction="RECEIVING", since=since, page=page, page_size=_PAGE_SIZE
        )
        for record in records:
            if record.document_type not in _INCOMING_DOC_TYPES:
                continue
            received, company_id = await _store_received(sessions, _Incoming.from_record(record))
            if received is _Received.STORED and company_id is not None:
                tally.received[company_id] += 1
        if not records or (page + 1) * _PAGE_SIZE >= total:
            return
        page += 1
    logger.warning("Peppol sweep stopped after %d pages of received documents", page + 1)


async def _sweep_pending(sessions: async_sessionmaker[AsyncSession], tally: _Tally) -> None:
    """Ask Ademico about every send still pending that a push should have settled."""
    now = utc_now()
    async with sessions() as db:
        pending = await peppol_service.pending_transmission_ids(
            db,
            sent_after=now - _PENDING_WINDOW,
            sent_before=now - _PENDING_SETTLE,
            limit=_MAX_PENDING_CHECKS,
        )
    for transmission_id in pending:
        delivery = await ademico.delivery(transmission_id)
        company_id = await _apply_delivery(sessions, transmission_id, delivery)
        if company_id is not None:
            tally.deliveries[company_id] += 1


async def sync_once(sessions: async_sessionmaker[AsyncSession]) -> SyncSummary:
    """Drain the queue, then check Ademico's own lists for anything no push brought."""
    if not ademico.is_configured():
        return SyncSummary()
    tally = _Tally()
    await _drain_queue(sessions, tally)
    await _sweep_received(sessions, tally)
    await _sweep_pending(sessions, tally)
    return SyncSummary(
        received=sum(tally.received.values()),
        deliveries_updated=sum(tally.deliveries.values()),
        consumed=tally.consumed,
        skipped=tally.skipped,
        received_by_company=dict(tally.received),
        deliveries_updated_by_company=dict(tally.deliveries),
    )


async def sweep_and_report(sessions: async_sessionmaker[AsyncSession]) -> None:
    """One round of the background sweep, and what it says about the webhook."""
    if not ademico.is_configured():
        return
    try:
        summary = await sync_once(sessions)
    except ademico.AdemicoError:
        logger.exception("Peppol sweep could not reach Ademico")
        return
    except Exception:
        logger.exception("Unexpected error during Peppol sweep")
        return
    if not (summary.received or summary.deliveries_updated):
        return
    if get_settings().ademico_webhook_configured:
        # Everything the sweep finds is something a push should have brought:
        # the webhook is off at Ademico, refused (a secret that differs), or met
        # a backend that was down when it came and Ademico did not try again.
        logger.error(
            "Peppol sweep found %d received documents and %d delivery updates "
            "that no webhook brought",
            summary.received,
            summary.deliveries_updated,
        )
    else:
        logger.info(
            "Peppol sweep: %d received, %d deliveries updated, %d consumed",
            summary.received,
            summary.deliveries_updated,
            summary.consumed,
        )


async def sweep_regularly(sessions: async_sessionmaker[AsyncSession]) -> None:
    """Background loop started by FastAPI lifespan. Silent when Ademico is unconfigured."""
    interval = get_settings().peppol_sweep_interval_seconds
    if interval <= 0:
        return
    await asyncio.sleep(_FIRST_SWEEP_DELAY_SECONDS)
    while True:
        interval = get_settings().peppol_sweep_interval_seconds
        if interval <= 0:
            return
        await sweep_and_report(sessions)
        await asyncio.sleep(interval)


# --- Telling Ademico where to push -------------------------------------------------


async def _show() -> int:
    current = await ademico.webhook_settings()
    print(f"active:         {current.active}")
    print(f"bulk:           {current.bulk}")
    print(f"url:            {current.url or '-'}")
    print(f"authentication: {current.authentication} (user {current.user or '-'})")
    return 0


async def _on() -> int:
    settings = get_settings()
    if not settings.ademico_webhook_configured or settings.ademico_webhook_secret is None:
        print("ADEMICO_WEBHOOK_SECRET is not set in .env; nothing was changed at Ademico.")
        return 1
    await ademico.set_webhook(
        url=settings.ademico_webhook_url,
        secret=settings.ademico_webhook_secret.get_secret_value().strip(),
    )
    return await _show()


async def _off() -> int:
    await ademico.disable_webhook()
    return await _show()


def main(argv: list[str] | None = None) -> int:
    """Show, switch on or switch off Ademico's webhook for this deployment.

    Talks to whichever Ademico ``ADEMICO_API_BASE_URL`` names with this
    deployment's credentials: production's, when run from this repository's
    ``backend/`` with its ``.env``. Never prints the secret. The backend must
    already be running with the same secret, or every push is refused with 401
    until it is.
    """
    parser = argparse.ArgumentParser(
        prog="python -m myntos.integrations.peppol_sync",
        description="Show or set where Ademico pushes Peppol events for this deployment.",
    )
    parser.add_argument("action", choices=("show", "on", "off"))
    args = parser.parse_args(argv)
    if not ademico.is_configured():
        print("Ademico is not configured (ADEMICO_USER, ADEMICO_PASSWORD, ADEMICO_TOKEN).")
        return 1
    action = {"show": _show, "on": _on, "off": _off}[args.action]
    return asyncio.run(action())


if __name__ == "__main__":
    raise SystemExit(main())

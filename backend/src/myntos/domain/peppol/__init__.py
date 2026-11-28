"""Peppol: sending and receiving EHF invoices and credit notes.

Outgoing EHF XML is written and frozen at issue (:mod:`myntos.domain.faktura`);
incoming EHF XML is stored verbatim as it arrives from Peppol and booked into
the ledger with a readable PDF archived beside the voucher.

This package decides who may register, send, receive and book, and keeps what
the access point answered. External calls live in
:mod:`myntos.integrations.ademico` and :mod:`myntos.integrations.peppol_smp`,
called from :mod:`myntos.api.v1.peppol` and :mod:`myntos.integrations.peppol_sync`
so that the domain never makes a network request.
docs/integrations/peppol.md explains the whole design.
"""

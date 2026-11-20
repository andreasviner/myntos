"""Peppol: sending an issued invoice to the customer as an EHF.

The EHF is written and frozen at issue (myntos.domain.faktura); this package
decides who may send it and keeps what the access point answered. The access
point itself is myntos.integrations.recommand, called from
myntos.api.v1.peppol so that the domain never makes a request.
docs/integrations/peppol.md explains the whole design.
"""

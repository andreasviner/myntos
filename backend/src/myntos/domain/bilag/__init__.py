"""Bilagsføring — vouchers and their document attachments.

Every journal entry traces back to a bilag; uploaded documents (PDFs) are
stored via ``myntos.storage`` and referenced here. The archive is
append-only for the whole legal retention period (oppbevaringsplikt):
documents can be superseded but never destroyed from within the application.
"""
